"""
FloodSense - Training core (pure functions; I/O and MLflow live in ``train.py``).

Protocol:
1. Development rows are years <= ``settings.last_training_year``. Test rows (years >=
   ``settings.test_start_year``) are unreachable from every function here except
   ``final_report``.
2. Forward-chaining CV by year: for each validation year v, fit on years < v, predict year v.
   Rarity quantiles are refitted inside every fold from that fold's training rows.
3. Pool the out-of-fold (OOF) predictions; pick the candidate with the best certain-label PR-AUC;
   fit its calibrator on its OOF predictions; compute the hit-rate / false-alarm trade-off curve
   on the calibrated OOF predictions; choose thresholds only if budgets are set.
4. Refit the chosen candidate on all development rows; attach calibrator and thresholds.
5. ``final_report`` scores the test years once.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from floodsense.common.config import MODEL_FEATURE_COLUMNS, settings
from floodsense.labels.policy import EventWindow, certain_labels, expand_soft_labels
from floodsense.models.artifact import FloodModel, fit_rarity_quantiles, git_sha
from floodsense.models.calibration import (
    Calibrator,
    expected_calibration_error,
    fit_calibrator,
    reliability_table,
)
from floodsense.models.evaluation import (
    evaluate_alerts,
    hit_rate_ci,
    ranking_check,
    row_metrics,
    select_threshold,
    tradeoff_curve,
)
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

# --------------------------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------------------------

logger = logging.getLogger("FloodSense.Training")


class RuleModel:
    """Baseline that ranks rows by one rainfall feature (e.g. 60-minute rain).

    The score is ``x / (x + scale)``: monotone in millimetres and inside [0, 1], so calibration
    and the threshold sweep turn it into "alert at N mm" rules. Comparing at matched false-alarm
    levels makes this the bar every learned model has to clear.
    """

    def __init__(self, column_index: int, scale_mm: float = 25.0):
        self.column_index, self.scale_mm = column_index, scale_mm

    def fit(
        self, X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None
    ) -> "RuleModel":
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        x = np.clip(np.asarray(X, dtype=float)[:, self.column_index], 0, None)
        p = x / (x + self.scale_mm)
        return np.column_stack([1 - p, p])


def _lightgbm(n_features: int) -> Any:
    import lightgbm as lgb  # imported lazily: needs libomp on macOS

    # A few hundred positive rows a fold: keep trees small and force "more rain never lowers
    # risk" (every model feature is a rainfall amount, wetness or rarity score).
    return lgb.LGBMClassifier(
        n_estimators=200,
        learning_rate=0.05,
        num_leaves=7,
        min_child_samples=200,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        monotone_constraints=[1] * n_features,
        random_state=0,
        verbose=-1,
    )


def candidate_factories(feature_columns: list[str]) -> dict[str, Callable[[], Any]]:
    return {
        "rule_rain30": lambda: RuleModel(feature_columns.index("rain_30m")),
        "rule_rain60": lambda: RuleModel(feature_columns.index("rain_60m")),
        # Rainfall is heavy-tailed; log1p keeps a few extreme storms from dominating the fit.
        "logistic": lambda: Pipeline(
            [
                ("log", FunctionTransformer(np.log1p)),
                ("scale", StandardScaler()),
                ("clf", LogisticRegression(C=1.0, max_iter=2000)),
            ]
        ),
        "lightgbm": lambda: _lightgbm(len(feature_columns)),
    }


# --------------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------------


@dataclass
class LabelledData:
    """Feature rows (pruned dry rows may be absent), soft labels aligned to them, event windows."""

    features: pd.DataFrame  # ura_planning_area, timestamp (tz-aware), rain_* columns
    label_prob: pd.Series  # NaN = excluded
    windows: list[EventWindow]

    def __post_init__(self) -> None:
        if not self.features.index.equals(self.label_prob.index):
            raise ValueError("label_prob must be aligned to features")
        self.year = self.features["timestamp"].dt.tz_convert(settings.tzinfo).dt.year

    def subset(self, mask: pd.Series) -> "LabelledData":
        start = self.features.loc[mask, "timestamp"].min()
        end = self.features.loc[mask, "timestamp"].max()
        windows = [w for w in self.windows if start is not pd.NaT and start <= w.start_lo <= end]
        return LabelledData(self.features.loc[mask], self.label_prob.loc[mask], windows)

    def development(self) -> "LabelledData":
        if settings.last_training_year >= settings.test_start_year:
            raise ValueError("last_training_year must be before test_start_year")
        return self.subset(self.year <= settings.last_training_year)

    def zone_years(self) -> float:
        """Zones x years of data covered. A year the data only partly covers (e.g. the current
        one) counts by the share of it between the first and last timestamp, not as a full year."""
        zones = self.features["ura_planning_area"].nunique()
        if self.features.empty:
            return 0.0
        ts = self.features["timestamp"].dt.tz_convert(settings.tzinfo)
        first, last = ts.min(), ts.max() + pd.Timedelta(minutes=settings.step_minutes)
        covered = 0.0
        for y in sorted(int(v) for v in self.year.unique()):
            start = pd.Timestamp(year=y, month=1, day=1, tz=settings.tzinfo)
            end = pd.Timestamp(year=y + 1, month=1, day=1, tz=settings.tzinfo)
            covered += (min(end, last) - max(start, first)) / (end - start)
        return float(zones * covered)


def _balanced(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Reweight positives so both classes carry equal total weight."""
    pos, neg = w[y == 1].sum(), w[y == 0].sum()
    if pos == 0 or neg == 0:
        return w
    return np.where(y == 1, w * (neg / pos), w)


def fit_candidate(name: str, data: LabelledData, feature_columns: list[str]) -> FloodModel:
    """Fit one candidate on ``data`` (rarity quantiles fitted on the same rows). Uncalibrated."""
    model = FloodModel(
        estimator=candidate_factories(feature_columns)[name](),
        feature_columns=feature_columns,
        rarity_quantiles=fit_rarity_quantiles(data.features),
    )
    X = pd.DataFrame(model.prepare(data.features), index=data.features.index)
    Xe, y, w = expand_soft_labels(X, data.label_prob)
    w = _balanced(y, w)
    est = model.estimator
    if isinstance(est, Pipeline):
        est.fit(Xe.to_numpy(), y, **{f"{est.steps[-1][0]}__sample_weight": w})
    else:
        est.fit(Xe.to_numpy(), y, sample_weight=w)
    model.provenance = {
        "candidate": name,
        "trained_rows": int(len(data.features)),
        "trained_from": str(data.features["timestamp"].min()),
        "trained_to": str(data.features["timestamp"].max()),
    }
    return model


# --------------------------------------------------------------------------------------------
# Cross-validation and selection
# --------------------------------------------------------------------------------------------


@dataclass
class CandidateResult:
    name: str
    oof: pd.DataFrame  # ura_planning_area, timestamp, label_prob, raw, prob (calibrated)
    folds: list[dict[str, Any]]
    calibrator: Calibrator
    row: dict[str, float]  # certain-label metrics on calibrated OOF (threshold-free parts)
    ece: float
    reliability: pd.DataFrame
    curve: pd.DataFrame

    @property
    def event_score(self) -> float:
        return event_score(self.curve)


# Reference false-alarm levels (false episodes per zone-year) for comparing candidates.
REFERENCE_BUDGETS = (1.0, 2.0, 5.0, 10.0)


def event_score(curve: pd.DataFrame) -> float:
    """Mean event hit rate at the reference false-alarm budgets: floods caught at a matched level
    of false alarms, which is what people experience, rather than row-level ranking."""
    hits = []
    for budget in REFERENCE_BUDGETS:
        best = select_threshold(curve, budget)
        hits.append(0.0 if best is None else float(best["hit_rate"]))
    return float(np.mean(hits))


def validation_years(data: LabelledData) -> list[int]:
    years = sorted(int(y) for y in data.year.unique())
    first = settings.cv_first_validation_year
    return [v for v in years if first <= v <= settings.last_training_year and min(years) < v]


def cross_validate(
    name: str, dev: LabelledData, feature_columns: list[str] | None = None
) -> CandidateResult:
    cols = feature_columns or MODEL_FEATURE_COLUMNS
    parts, folds = [], []
    for v in validation_years(dev):
        train, val = dev.subset(dev.year < v), dev.subset(dev.year == v)
        model = fit_candidate(name, train, cols)
        parts.append(
            pd.DataFrame(
                {
                    "ura_planning_area": val.features["ura_planning_area"],
                    "timestamp": val.features["timestamp"],
                    "label_prob": val.label_prob,
                    "raw": model.raw_proba(val.features),
                    "fold": v,
                }
            )
        )
        folds.append(
            {
                "validation_year": v,
                "train_max_timestamp": train.features["timestamp"].max(),
                "train_rows": len(train.features),
                "validation_rows": len(val.features),
            }
        )
    if not parts:
        raise ValueError("No validation years available; need data before cv_first_validation_year")
    oof = pd.concat(parts)

    # The calibrator shipped with the model is fitted on all OOF predictions. Everything reported
    # here (and the threshold curve) uses cross-fitted calibration instead - each fold calibrated
    # by a calibrator fitted on the other folds - so calibration is never scored in-sample.
    calibrator = _fit_oof_calibrator(oof)
    oof["prob"] = _cross_fitted_probabilities(oof) if len(folds) > 1 else calibrator(oof["raw"])

    certain = certain_labels(oof["label_prob"]).dropna()
    row = row_metrics(certain.to_numpy(), oof.loc[certain.index, "prob"].to_numpy(), threshold=0.5)
    table = reliability_table(oof["prob"].to_numpy()[oof["label_prob"].notna()], *_soft_pairs(oof))
    val_windows = [
        w for w in dev.windows if int(w.start_lo.year) in {f["validation_year"] for f in folds}
    ]
    zone_years = float(oof["ura_planning_area"].nunique() * len(folds))
    curve = tradeoff_curve(oof[["ura_planning_area", "timestamp", "prob"]], val_windows, zone_years)
    return CandidateResult(
        name=name,
        oof=oof,
        folds=folds,
        calibrator=calibrator,
        row=row,
        ece=expected_calibration_error(table),
        reliability=table,
        curve=curve,
    )


def _fit_oof_calibrator(oof: pd.DataFrame) -> Calibrator:
    """Calibrator on OOF predictions, soft labels expanded into weighted 0/1 rows."""
    Xr, y, w = expand_soft_labels(oof[["raw"]], oof["label_prob"])
    return fit_calibrator(Xr["raw"].to_numpy(), y, w)


def _cross_fitted_probabilities(oof: pd.DataFrame) -> np.ndarray:
    prob = np.empty(len(oof))
    fold = oof["fold"].to_numpy()
    for f in np.unique(fold):
        prob[fold == f] = _fit_oof_calibrator(oof[fold != f])(oof["raw"].to_numpy()[fold == f])
    return prob


def _soft_pairs(oof: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Observed values for the reliability table: soft label as the outcome, weight 1."""
    p = oof["label_prob"].dropna().to_numpy()
    return p, np.ones_like(p)


def choose_thresholds(curve: pd.DataFrame) -> dict[str, float] | None:
    """Thresholds under the configured budgets, or None while budgets are unset."""
    hi_budget, mod_budget = settings.false_alarm_budget_high, settings.false_alarm_budget_moderate
    if hi_budget is None or mod_budget is None:
        return None
    high, moderate = select_threshold(curve, hi_budget), select_threshold(curve, mod_budget)
    if high is None or moderate is None:
        raise ValueError("No threshold fits the false-alarm budget; see the trade-off curve")
    return {
        "moderate": float(min(moderate["threshold"], high["threshold"])),
        "high": float(high["threshold"]),
    }


@dataclass
class TrainingRun:
    results: dict[str, CandidateResult]
    selected: str
    model: FloodModel
    notes: list[str] = field(default_factory=list)


def train(data: LabelledData, candidates: list[str] | None = None) -> TrainingRun:
    """CV every candidate on development years, select, calibrate, refit. Never sees test years."""
    dev = data.development()
    names = candidates or list(candidate_factories(MODEL_FEATURE_COLUMNS))
    results = {n: cross_validate(n, dev) for n in names}
    selected = max(
        results,
        key=lambda n: (results[n].event_score, np.nan_to_num(results[n].row["pr_auc"], nan=-1.0)),
    )
    for n, r in results.items():
        logger.info("%s: event score %.3f, PR-AUC %.5f", n, r.event_score, r.row["pr_auc"])
    best = results[selected]

    model = fit_candidate(selected, dev, MODEL_FEATURE_COLUMNS)
    model.calibrator = best.calibrator
    model.thresholds = choose_thresholds(best.curve)
    notes = [] if model.thresholds else ["false-alarm budgets unset: thresholds not selected"]
    model.provenance.update(
        {
            "selected_by": (
                "mean out-of-fold event hit rate at "
                f"{list(REFERENCE_BUDGETS)} false episodes per zone-year (tie: PR-AUC)"
            ),
            "validation_years": [f["validation_year"] for f in best.folds],
            "events_in_development": len(dev.windows),
            "label_horizon_minutes": settings.prediction_lead_time_minutes,
            "calibration": best.calibrator.method,
            "git_sha": git_sha(),
        }
    )
    return TrainingRun(results=results, selected=selected, model=model, notes=notes)


# --------------------------------------------------------------------------------------------
# One-time test report
# --------------------------------------------------------------------------------------------


def final_report(model: FloodModel, data: LabelledData) -> dict[str, Any]:
    """Score the test years once. Requires thresholds chosen under the false-alarm budget."""
    if not model.thresholds:
        raise ValueError("Set the false-alarm budgets and retrain before the final report")
    test = data.subset(data.year >= settings.test_start_year)
    if test.features.empty:
        raise ValueError(f"No rows from {settings.test_start_year} onward")
    prob = model.predict_proba(test.features)
    certain = certain_labels(test.label_prob).dropna()
    scored = test.features[["ura_planning_area", "timestamp"]].assign(prob=prob)
    high = model.thresholds["high"]
    summary, outcomes, _ = evaluate_alerts(scored, test.windows, high, test.zone_years())
    lo, hi = hit_rate_ci([o.hit for o in outcomes])
    moderate, _, _ = evaluate_alerts(
        scored, test.windows, model.thresholds["moderate"], test.zone_years()
    )
    n_zones = len(URA_PLANNING_AREAS)
    return {
        "row": row_metrics(
            certain.to_numpy(),
            pd.Series(prob, index=test.features.index)[certain.index].to_numpy(),
            high,
        ),
        "events_high": summary,
        "hit_rate_ci90": (lo, hi),
        "events_moderate": moderate,
        "ranking": ranking_check(scored, test.windows, n_zones),
        "outcomes": outcomes,
        "test_years": sorted(int(y) for y in test.year.unique()),
    }
