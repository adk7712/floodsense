"""
FloodSense - Model artifacts.

``FloodModel`` bundles everything needed to score zone features consistently with training:
the estimator, its own feature list, rarity quantiles fitted on its training years, the
calibrator, the alert thresholds chosen under the false-alarm budget, and provenance.

``LegacyModel`` adapts the original synthetic-data pipeline (models/champion_model.joblib) to the
same interface so the app keeps working until a real model exists.
"""

import json
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

    is_synthetic = False

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


@dataclass
class LegacyModel:
    """The original bundled pipeline, trained on synthetic data. Thresholds come from settings."""

    pipeline: Any
    feature_columns: list[str]
    thresholds: dict[str, float] | None = None
    provenance: dict[str, Any] = field(
        default_factory=lambda: {"data": "synthetic", "note": "pre-Phase 4 placeholder"}
    )

    is_synthetic = True

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        X = features[self.feature_columns].to_numpy(dtype=float)
        return np.asarray(self.pipeline.predict_proba(X)[:, 1], dtype=float)


def load_model() -> FloodModel | LegacyModel | None:
    """The Phase 4 artifact if present, else the legacy pipeline, else None (heuristic)."""
    if settings.flood_model_path.exists():
        model = joblib.load(settings.flood_model_path)
        if not isinstance(model, FloodModel):
            raise TypeError(f"{settings.flood_model_path} is not a FloodModel")
        return model
    if settings.champion_model_path.exists():
        meta_path = settings.models_dir / "model_metadata.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        from floodsense.common.config import FEATURE_COLUMNS

        return LegacyModel(
            pipeline=joblib.load(settings.champion_model_path),
            feature_columns=meta.get("feature_columns", FEATURE_COLUMNS),
        )
    return None
