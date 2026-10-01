"""
FloodSense - Probability calibration.

Class weighting makes raw model scores rank well but overstate risk. A calibrator fitted on
out-of-fold validation predictions maps them back to frequencies, so "30%" means floods follow
about 30% of the time. Platt scaling (a logistic fit on the logit) is stable with few positives;
isotonic regression is more flexible and is used only when there are enough positives.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from floodsense.common.config import settings

_EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


@dataclass
class Calibrator:
    method: str  # "platt" | "isotonic"
    model: LogisticRegression | IsotonicRegression

    def __call__(self, raw: np.ndarray) -> np.ndarray:
        raw = np.asarray(raw, dtype=float)
        if self.method == "platt":
            assert isinstance(self.model, LogisticRegression)
            return self.model.predict_proba(_logit(raw).reshape(-1, 1))[:, 1]
        return np.asarray(self.model.predict(raw), dtype=float)


def fit_calibrator(
    raw: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray | None = None,
    method: str | None = None,
) -> Calibrator:
    """Fit on out-of-fold predictions (never on the rows the model was trained on)."""
    raw, y = np.asarray(raw, dtype=float), np.asarray(y, dtype=float)
    w = np.ones_like(y) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    method = method or settings.calibration_method
    if method == "auto":
        method = "isotonic" if w[y == 1].sum() >= settings.isotonic_min_positives else "platt"
    if method == "platt":
        lr = LogisticRegression(C=1e6, max_iter=1000)  # effectively unregularised
        lr.fit(_logit(raw).reshape(-1, 1), y, sample_weight=w)
        return Calibrator("platt", lr)
    if method == "isotonic":
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        iso.fit(raw, y, sample_weight=w)
        return Calibrator("isotonic", iso)
    raise ValueError(f"Unknown calibration method {method!r}")


def reliability_table(
    prob: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None, bins: int = 10
) -> pd.DataFrame:
    """Mean predicted probability vs observed frequency per probability bin (weighted)."""
    prob, y = np.asarray(prob, dtype=float), np.asarray(y, dtype=float)
    w = np.ones_like(y) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(prob, edges[1:-1]), 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        weight = w[m].sum()
        rows.append(
            {
                "bin_lo": edges[b],
                "bin_hi": edges[b + 1],
                "weight": weight,
                "mean_predicted": float(np.average(prob[m], weights=w[m])) if weight else np.nan,
                "observed_rate": float(np.average(y[m], weights=w[m])) if weight else np.nan,
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(table: pd.DataFrame) -> float:
    """Weighted mean |predicted - observed| over non-empty bins."""
    t = table[table["weight"] > 0]
    if t.empty:
        return float("nan")
    gap = (t["mean_predicted"] - t["observed_rate"]).abs()
    return float(np.average(gap, weights=t["weight"]))
