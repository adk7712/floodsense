"""
FloodSense - Turning zone features into flood probabilities and risk tiers.

One place for the probability -> tier mapping, shared by the app and training/evaluation.
"""

from typing import Any

import numpy as np
import pandas as pd

from floodsense.common.config import FEATURE_COLUMNS, settings

# Placeholder used only when no trained model is available: 45 mm in 30 minutes maps to 1.0.
HEURISTIC_FULL_SCALE_MM_30M = 45.0


def risk_tier(probability: float) -> str:
    """Map a flood probability to the operational Low / Moderate / High tier."""
    if probability >= settings.risk_moderate_high:
        return "High"
    if probability >= settings.risk_low_moderate:
        return "Moderate"
    return "Low"


def score_zone_features(features: pd.DataFrame, model: Any | None) -> pd.DataFrame:
    """
    Add ``flood_probability`` and ``risk_tier`` columns to a feature table.

    Uses ``model.predict_proba`` on ``FEATURE_COLUMNS`` when a model is given, otherwise a
    rainfall heuristic (``rain_30m / 45 mm``). Returns a new DataFrame.
    """
    scored = features.copy()
    if model is not None:
        probs = model.predict_proba(scored[FEATURE_COLUMNS].to_numpy())[:, 1]
    else:
        probs = np.clip(scored["rain_30m"].to_numpy() / HEURISTIC_FULL_SCALE_MM_30M, 0.0, 1.0)
    scored["flood_probability"] = np.round(probs.astype(float), 3)
    scored["risk_tier"] = [risk_tier(p) for p in scored["flood_probability"]]
    return scored
