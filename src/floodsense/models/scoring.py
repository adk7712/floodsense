"""
FloodSense - Turning zone features into flood probabilities and risk tiers.

One place for the probability -> tier mapping, shared by the app, backtests and evaluation.
Models carry their own feature list and (once selected under the false-alarm budget) their own
thresholds (0.00531 Moderate / 0.00706 High for v3); ``settings.risk_*`` are heuristic-only
fallbacks, used when a model has none, or for the heuristic.
"""

from typing import Any

import numpy as np
import pandas as pd

from floodsense.common.config import MODEL_FEATURE_COLUMNS, active_rain_mask, settings

# Tier for a zone whose inputs are unknown; shared with floodsense.serving.coverage.
NO_DATA_TIER = "No data"

# Placeholder used only when no trained model is available: 45 mm in 30 minutes maps to 1.0.
HEURISTIC_FULL_SCALE_MM_30M = 45.0


def default_thresholds() -> dict[str, float]:
    return {"moderate": settings.risk_low_moderate, "high": settings.risk_moderate_high}


def risk_tier(probability: float, thresholds: dict[str, float] | None = None) -> str:
    """Map a flood probability to the operational Low / Moderate / High tier.

    A NaN probability (an unknown input) is "No data", never Low.
    """
    if np.isnan(probability):
        return NO_DATA_TIER
    t = thresholds or default_thresholds()
    if probability >= t["high"]:
        return "High"
    if probability >= t["moderate"]:
        return "Moderate"
    return "Low"


def predict_probabilities(features: pd.DataFrame, model: Any | None) -> np.ndarray:
    """
    Model probabilities (``model.predict_proba(features_df)``) or the rainfall heuristic.

    Rows failing the active-rain gate (``settings.active_rain_min_mm_120m``) score 0: the feature
    store leaves them out of training, so the model has never seen them. A row with a NaN in any
    feature the model needs scores NaN (unknown), not 0 and not an error.
    """
    if model is not None:
        wanted = getattr(model, "feature_columns", None) or MODEL_FEATURE_COLUMNS
        # The rarity score is computed from rain_30m inside the model, so it isn't an input column.
        needed = [c for c in wanted if c in features.columns and c != "storm_rarity_score"]
        unknown = (features[needed].isna().any(axis=1) | features["rain_120m"].isna()).to_numpy()
        probs = np.full(len(features), np.nan)
        if (~unknown).any():
            probs[~unknown] = np.asarray(model.predict_proba(features[~unknown]), dtype=float)
    else:
        probs = np.clip(
            features["rain_30m"].to_numpy(dtype=float) / HEURISTIC_FULL_SCALE_MM_30M, 0, 1
        )
        unknown = np.isnan(probs) | features["rain_120m"].isna().to_numpy()
    active = active_rain_mask(features["rain_120m"])
    return np.where(unknown, np.nan, np.where(active, probs, 0.0))


def score_zone_features(features: pd.DataFrame, model: Any | None) -> pd.DataFrame:
    """Add ``flood_probability`` and ``risk_tier`` columns to a feature table (new DataFrame)."""
    scored = features.copy()
    probs = predict_probabilities(scored, model)
    thresholds = getattr(model, "thresholds", None) or default_thresholds()
    # Unrounded: calibrated flood probabilities sit around 0.001-0.03, so rounding before the
    # tiers are applied would move zones across the 0.00531 / 0.00706 thresholds. Round for display.
    scored["flood_probability"] = probs
    scored["risk_tier"] = [risk_tier(p, thresholds) for p in probs]
    return scored
