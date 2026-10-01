"""
FloodSense - Turning zone features into flood probabilities and risk tiers.

One place for the probability -> tier mapping, shared by the app, backtests and evaluation.
Models carry their own feature list and (once selected under the false-alarm budget) their own
thresholds; ``settings.risk_*`` apply only when a model has none, or to the heuristic.
"""

from typing import Any

import numpy as np
import pandas as pd

from floodsense.common.config import settings

# Placeholder used only when no trained model is available: 45 mm in 30 minutes maps to 1.0.
HEURISTIC_FULL_SCALE_MM_30M = 45.0


def default_thresholds() -> dict[str, float]:
    return {"moderate": settings.risk_low_moderate, "high": settings.risk_moderate_high}


def risk_tier(probability: float, thresholds: dict[str, float] | None = None) -> str:
    """Map a flood probability to the operational Low / Moderate / High tier."""
    t = thresholds or default_thresholds()
    if probability >= t["high"]:
        return "High"
    if probability >= t["moderate"]:
        return "Moderate"
    return "Low"


def predict_probabilities(features: pd.DataFrame, model: Any | None) -> np.ndarray:
    """Model probabilities (``model.predict_proba(features_df)``) or the rainfall heuristic."""
    if model is not None:
        return np.asarray(model.predict_proba(features), dtype=float)
    return np.clip(features["rain_30m"].to_numpy(dtype=float) / HEURISTIC_FULL_SCALE_MM_30M, 0, 1)


def score_zone_features(features: pd.DataFrame, model: Any | None) -> pd.DataFrame:
    """Add ``flood_probability`` and ``risk_tier`` columns to a feature table (new DataFrame)."""
    scored = features.copy()
    probs = predict_probabilities(scored, model)
    thresholds = getattr(model, "thresholds", None) or default_thresholds()
    scored["flood_probability"] = np.round(probs, 3)
    scored["risk_tier"] = [risk_tier(p, thresholds) for p in scored["flood_probability"]]
    return scored
