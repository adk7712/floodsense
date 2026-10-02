"""
FloodSense - Model artifacts.

``FloodModel`` bundles everything needed to score zone features consistently with training:
the estimator, its own feature list, rarity quantiles fitted on its training years, the
calibrator, the alert thresholds chosen under the false-alarm budget, and provenance.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.features.feature_pipeline import StormRarityEstimator
from floodsense.models.calibration import Calibrator


def fit_rarity_quantiles(features: pd.DataFrame) -> dict[str, list[float]]:
    """Per-zone 30-minute burst quantiles from (training) rows; zones with too few bursts keep
    the estimator's defaults."""
    est = StormRarityEstimator()
    est.fit_zone_distributions(features[["ura_planning_area", "rain_30m"]])
    return {zone: [float(v) for v in q] for zone, q in est.zone_quantiles.items()}


def rarity_scores(features: pd.DataFrame, quantiles: dict[str, list[float]]) -> np.ndarray:
    est = StormRarityEstimator()
    est.zone_quantiles = {z: np.asarray(q) for z, q in quantiles.items()}
    out = np.zeros(len(features))
    zones = features["ura_planning_area"].to_numpy()
    rain = features["rain_30m"].to_numpy(dtype=float)
    for zone in np.unique(zones):
        m = zones == zone
        out[m] = est.score_array(str(zone), rain[m])[0]
    return out


def git_sha() -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                cwd=settings.root_dir,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.CalledProcessError):
        return None


@dataclass
class FloodModel:
    estimator: Any  # sklearn-compatible: predict_proba(X) -> (n, 2)
    feature_columns: list[str]
    rarity_quantiles: dict[str, list[float]]
    calibrator: Calibrator | None = None
    thresholds: dict[str, float] | None = None  # {"moderate": p, "high": p}; None = not selected
    provenance: dict[str, Any] = field(default_factory=dict)

    def prepare(self, features: pd.DataFrame) -> np.ndarray:
        df = features.copy()
        if "storm_rarity_score" in self.feature_columns:
            df["storm_rarity_score"] = rarity_scores(df, self.rarity_quantiles)
        return df[self.feature_columns].to_numpy(dtype=float)

    def raw_proba(self, features: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.estimator.predict_proba(self.prepare(features))[:, 1], dtype=float)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        raw = self.raw_proba(features)
        return self.calibrator(raw) if self.calibrator is not None else raw

    def save(self, path: Path | None = None) -> Path:
        path = path or settings.flood_model_path
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)
        return path


def load_model() -> FloodModel | None:
    """The trained artifact (``models/flood_model.joblib``) if present, else None (heuristic)."""
    if not settings.flood_model_path.exists():
        return None
    model = joblib.load(settings.flood_model_path)
    if not isinstance(model, FloodModel):
        raise TypeError(f"{settings.flood_model_path} is not a FloodModel")
    return model
