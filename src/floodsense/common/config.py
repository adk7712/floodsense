"""
FloodSense - Central configuration.

Single source of truth for thresholds, feature columns, timing constants,
external endpoints and filesystem paths. Values can be overridden through
environment variables prefixed with ``FLOODSENSE_`` (e.g. ``FLOODSENSE_ROOT_DIR``).
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root, computed once: src/floodsense/common/config.py -> parents[3].
_DEFAULT_ROOT_DIR = Path(__file__).resolve().parents[3]

# Order matters: this is the model input vector layout.
FEATURE_COLUMNS: list[str] = [
    "rain_5m",
    "rain_15m",
    "rain_30m",
    "rain_60m",
    "rain_120m",
    "rain_decay_72h",
    "storm_rarity_score",
    "return_period_years",
    "pub_monitored",
]


class Settings(BaseSettings):
    """Runtime settings, overridable via ``FLOODSENSE_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="FLOODSENSE_")

    # Risk tier thresholds (probability)
    risk_low_moderate: float = 0.25
    risk_moderate_high: float = 0.65

    # Temporal constants
    decay_half_life_hours: float = 24.0
    step_minutes: float = 5.0
    prediction_lead_time_minutes: int = 60
    timezone: str = "Asia/Singapore"

    # NEA / data.gov.sg endpoints
    nea_api_primary: str = "https://api-open.data.gov.sg/v1/public/api/weather/rainfall"
    nea_api_fallback: str = "https://api.data.gov.sg/v1/environment/rainfall"

    # Paths
    root_dir: Path = _DEFAULT_ROOT_DIR

    @property
    def operational_thresholds(self) -> dict[str, float]:
        return {
            "low_moderate": self.risk_low_moderate,
            "moderate_high": self.risk_moderate_high,
        }

    @property
    def models_dir(self) -> Path:
        return self.root_dir / "models"

    @property
    def champion_model_path(self) -> Path:
        return self.models_dir / "champion_model.joblib"

    @property
    def replay_file(self) -> Path:
        return self.root_dir / "data" / "replay" / "april_2021_storm.json"

    @property
    def landing_dir(self) -> Path:
        return self.root_dir / "data" / "raw" / "landing_volume"


settings = Settings()
