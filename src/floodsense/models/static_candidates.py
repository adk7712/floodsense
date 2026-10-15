"""
FloodSense - Candidates that can use the static zone features (B4.2b).

``data/reference/zone_static.csv`` gives each of the 55 planning areas a built-up share, a mean
elevation and a low-lying share. The core candidates (``training.candidate_factories``) see only
rainfall, so these features can change nothing unless a candidate takes them. With a few dozen
training floods, anything fitted per zone would overfit, so the candidates here use the three
zone values as shared inputs only:

``rule_rain60_static``  the 60-minute rain rule plus one shared, heavily shrunk coefficient per
                        static feature on the logit scale (``RuleStaticAdjustment``). Without the
                        static features (``rule_rain60_nostatic``) it is a monotone transform of
                        ``rule_rain60``, so the pair is a clean ablation.
``logistic_static``     the core logistic regression with the static columns added.
``lightgbm_static``     the core LightGBM with the static columns added (no monotone constraint on
                        them: the direction of each effect is not assumed).

The static values are looked up from ``ura_planning_area`` inside the artifact
(``StaticFloodModel``), so callers keep passing the same rain feature rows as for any
``FloodModel`` and ``feature_columns`` stays the rain list (``databricks/register_model.py`` builds
its input example from it). A zone missing from the lookup is an error, never filled in.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from floodsense.common.config import settings
from floodsense.labels.policy import expand_soft_labels
from floodsense.models.artifact import FloodModel, fit_rarity_quantiles
from floodsense.models.training import LabelledData, _balanced, _lightgbm
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

STATIC_COLUMNS: list[str] = ["built_up_pct", "elevation_m_mean", "low_lying_pct"]
# Prior standard deviation of each static coefficient, in log-odds per standard deviation of the
# feature across the 55 zones: a priori, a one-SD difference between zones is expected to change
# the odds of a flood by a factor of about e^0.5 = 1.6 or less. Fixed before any run.
DEFAULT_PRIOR_SD = 0.5
_EPS = 1e-6


def zone_static_path() -> Path:
    return settings.root_dir / "data" / "reference" / "zone_static.csv"


def load_zone_static(path: Path | None = None) -> dict[str, list[float]]:
    """Static values per zone, in ``STATIC_COLUMNS`` order. Fails on missing zones or blanks."""
    df = pd.read_csv(path or zone_static_path())
    missing_cols = [c for c in ["ura_planning_area", *STATIC_COLUMNS] if c not in df.columns]
    if missing_cols:
        raise ValueError(f"zone_static is missing columns {missing_cols}")
    if df["ura_planning_area"].duplicated().any():
        raise ValueError("zone_static has duplicate zones")
    if set(df["ura_planning_area"]) != set(URA_PLANNING_AREAS):
        raise ValueError("zone_static zones do not match the 55 URA planning areas")
    if df[STATIC_COLUMNS].isna().any().any():
        raise ValueError("zone_static has blank static values")
    return {
        str(row["ura_planning_area"]): [float(row[c]) for c in STATIC_COLUMNS]
        for _, row in df.iterrows()
    }


# --------------------------------------------------------------------------------------------
# Artifact
# --------------------------------------------------------------------------------------------


@dataclass
class StaticFloodModel(FloodModel):
    """A ``FloodModel`` whose estimator also sees the zone's static values (appended after the
    rain ``feature_columns``, in ``static_columns`` order)."""

    zone_static: dict[str, list[float]] = field(default_factory=dict)
    static_columns: list[str] = field(default_factory=lambda: list(STATIC_COLUMNS))

    def static_matrix(self, features: pd.DataFrame) -> np.ndarray:
        zones = features["ura_planning_area"].astype(str)
        unknown = sorted(set(zones.unique()) - set(self.zone_static))
        if unknown:
            raise ValueError(f"No static values for zones {unknown}")
        table = pd.DataFrame.from_dict(
            self.zone_static, orient="index", columns=self.static_columns
        )
        return table.loc[zones.to_numpy()].to_numpy(dtype=float)

    def prepare(self, features: pd.DataFrame) -> np.ndarray:
        return np.hstack([super().prepare(features), self.static_matrix(features)])


# --------------------------------------------------------------------------------------------
# Rule plus a shrunk static adjustment
# --------------------------------------------------------------------------------------------


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return np.asarray(0.5 * (1.0 + np.tanh(0.5 * z)), dtype=float)


class RuleStaticAdjustment:
    """``logit p = b + a * logit(rule) + sum_k beta_k * z_k``.

    ``rule`` is the rain rule's score ``x / (x + scale_mm)`` on ``column_index`` (60-minute rain),
    so ``logit(rule) = log(x / scale_mm)``. ``z_k`` are the static columns (from ``n_rain`` on),
    standardised across the distinct zones seen in training. ``a`` and ``b`` are free; each
    ``beta_k`` has a Gaussian prior with standard deviation ``prior_sd`` (an L2 penalty that only
    touches the static coefficients, so the rain term is not shrunk).

    The weighted rows the training code passes (soft labels, classes balanced) are rescaled so
    the positives carry the weight of ``n_events`` observations and the negatives the same: rows
    from one flood are not independent, so the flood count, not the row count, sets how far the
    data can move the static coefficients away from zero. ``n_events`` must be set before ``fit``.
    With ``use_static=False`` the static columns are ignored and the model is a monotone transform
    of the rule (the ablation).
    """

    def __init__(
        self,
        column_index: int,
        n_rain: int,
        scale_mm: float = 25.0,
        prior_sd: float = DEFAULT_PRIOR_SD,
        use_static: bool = True,
        n_events: int | None = None,
    ):
        self.column_index, self.n_rain, self.scale_mm = column_index, n_rain, scale_mm
        self.prior_sd, self.use_static, self.n_events = prior_sd, use_static, n_events
        self.static_mean_: np.ndarray | None = None
        self.static_sd_: np.ndarray | None = None
        self.intercept_ = 0.0
        self.rain_coef_ = 1.0
        self.static_coef_ = np.zeros(0)

    def _rule_logit(self, X: np.ndarray) -> np.ndarray:
        x = np.clip(np.asarray(X, dtype=float)[:, self.column_index], 0, None)
        p = np.clip(x / (x + self.scale_mm), _EPS, 1 - _EPS)
        return np.asarray(np.log(p / (1 - p)), dtype=float)

    def _static(self, X: np.ndarray) -> np.ndarray:
        if not self.use_static:
            return np.zeros((len(X), 0))
        assert self.static_mean_ is not None and self.static_sd_ is not None
        S = np.asarray(X, dtype=float)[:, self.n_rain :]
        return np.asarray((S - self.static_mean_) / self.static_sd_, dtype=float)

    def fit(
        self, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None
    ) -> "RuleStaticAdjustment":
        if self.n_events is None or self.n_events <= 0:
            raise ValueError("RuleStaticAdjustment needs n_events (training floods) before fit")
        X, y = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
        w = np.ones_like(y) if sample_weight is None else np.asarray(sample_weight, dtype=float)
        pos = y == 1
        if not pos.any() or pos.all():
            raise ValueError("RuleStaticAdjustment needs both classes")
        w = np.where(pos, w / w[pos].sum(), w / w[~pos].sum()) * float(self.n_events)

        if self.use_static:
            zones = np.unique(X[:, self.n_rain :], axis=0)  # one row per distinct zone
            self.static_mean_ = zones.mean(axis=0)
            sd = zones.std(axis=0)
            self.static_sd_ = np.where(sd > 0, sd, 1.0)
        r, Z = self._rule_logit(X), self._static(X)
        k = Z.shape[1]
        inv_var = 1.0 / self.prior_sd**2

        def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
            b, a, beta = theta[0], theta[1], theta[2:]
            eta = b + a * r + Z @ beta
            # log(1 + e^eta) - y * eta, computed stably
            loss = np.logaddexp(0.0, eta) - y * eta
            g = w * (_sigmoid(eta) - y)
            value = float(w @ loss + 0.5 * inv_var * beta @ beta)
            grad = np.concatenate([[g.sum(), g @ r], Z.T @ g + inv_var * beta])
            return value, grad

        res = minimize(
            objective, np.concatenate([[0.0, 1.0], np.zeros(k)]), jac=True, method="L-BFGS-B"
        )
        if not res.success:
            raise RuntimeError(f"RuleStaticAdjustment did not converge: {res.message}")
        self.intercept_, self.rain_coef_ = float(res.x[0]), float(res.x[1])
        self.static_coef_ = np.asarray(res.x[2:], dtype=float)
        return self

    def decision_function(self, X: np.ndarray) -> np.ndarray:
        """The fitted log-odds ``b + a * logit(rule) + beta . z`` (balanced-class scale)."""
        return np.asarray(
            self.intercept_
            + self.rain_coef_ * self._rule_logit(X)
            + self._static(X) @ self.static_coef_,
            dtype=float,
        )

    def zone_shift(self, X: np.ndarray) -> np.ndarray:
        """Each row's static shift in units of ``logit(rule) = log(rain / scale_mm)``: the zone's
        rainfall threshold is multiplied by ``exp(-shift)``."""
        return np.asarray(self._static(X) @ self.static_coef_ / self.rain_coef_, dtype=float)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """The rule's own score with each zone's shift added on the logit scale.

        This ranks rows exactly as ``decision_function`` does (it is that divided by ``a``, minus
        a constant), but stays on the rule's scale: the steep fitted slope would push heavy-rain
        scores to within 1e-6 of 1, where the Platt calibrator clips them and the ranking at the
        top would be lost. Calibration is refitted on these scores, so the scale is free.
        """
        if self.rain_coef_ <= 0:
            raise ValueError("RuleStaticAdjustment fitted a non-positive rain coefficient")
        p = _sigmoid(self._rule_logit(X) + self.zone_shift(X))
        return np.column_stack([1 - p, p])

    def summary(self) -> dict[str, Any]:
        """Fitted coefficients, for the selection report."""
        out: dict[str, Any] = {
            "intercept": self.intercept_,
            "rain_coef": self.rain_coef_,
            "n_events": self.n_events,
            "prior_sd": self.prior_sd,
        }
        if self.use_static:
            out["static_coef_per_sd"] = dict(
                zip(STATIC_COLUMNS, (float(c) for c in self.static_coef_), strict=True)
            )
        return out


# --------------------------------------------------------------------------------------------
# Factories and fitting
# --------------------------------------------------------------------------------------------


def _log1p_rain(X: np.ndarray, n_rain: int) -> np.ndarray:
    """log1p on the rain columns only; the static columns pass through."""
    X = np.asarray(X, dtype=float).copy()
    X[:, :n_rain] = np.log1p(X[:, :n_rain])
    return X


def static_factories(feature_columns: list[str]) -> dict[str, Callable[[], Any]]:
    n_rain = len(feature_columns)
    rain60 = feature_columns.index("rain_60m")
    n_static = len(STATIC_COLUMNS)
    return {
        "rule_rain60_static": lambda: RuleStaticAdjustment(rain60, n_rain),
        "rule_rain60_nostatic": lambda: RuleStaticAdjustment(rain60, n_rain, use_static=False),
        # Same settings as the core logistic candidate, so the only change is the extra columns.
        "logistic_static": lambda: Pipeline(
            [
                ("log", FunctionTransformer(_log1p_rain, kw_args={"n_rain": n_rain})),
                ("scale", StandardScaler()),
                ("clf", LogisticRegression(C=1.0, max_iter=2000)),
            ]
        ),
        "lightgbm_static": lambda: _lightgbm(n_rain + n_static).set_params(
            monotone_constraints=[1] * n_rain + [0] * n_static
        ),
    }


STATIC_CANDIDATES: list[str] = list(static_factories(["rain_60m"]))


def fit_static_candidate(
    name: str,
    data: LabelledData,
    feature_columns: list[str],
    zone_static: dict[str, list[float]] | None = None,
) -> StaticFloodModel:
    """Fit one static candidate on ``data`` (mirrors ``training.fit_candidate``). Uncalibrated."""
    model = StaticFloodModel(
        estimator=static_factories(feature_columns)[name](),
        feature_columns=feature_columns,
        rarity_quantiles=fit_rarity_quantiles(data.features),
        zone_static=zone_static or load_zone_static(),
    )
    est = model.estimator
    if isinstance(est, RuleStaticAdjustment):
        est.n_events = len(data.windows)
    X = pd.DataFrame(model.prepare(data.features), index=data.features.index)
    Xe, y, w = expand_soft_labels(X, data.label_prob)
    w = _balanced(y, w)
    if isinstance(est, Pipeline):
        est.fit(Xe.to_numpy(), y, **{f"{est.steps[-1][0]}__sample_weight": w})
    else:
        est.fit(Xe.to_numpy(), y, sample_weight=w)
    model.provenance = {
        "candidate": name,
        "trained_rows": int(len(data.features)),
        "trained_from": str(data.features["timestamp"].min()),
        "trained_to": str(data.features["timestamp"].max()),
        "static_columns": list(model.static_columns),
        "static_source": "data/reference/zone_static.csv",
    }
    return model
