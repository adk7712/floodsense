"""
FloodSense - Central configuration.

Single source of truth for thresholds, feature columns, timing constants,
external endpoints and filesystem paths. Values can be overridden through
environment variables prefixed with ``FLOODSENSE_`` (e.g. ``FLOODSENSE_ROOT_DIR``).
"""

from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root, computed once: src/floodsense/common/config.py -> parents[3].
_DEFAULT_ROOT_DIR = Path(__file__).resolve().parents[3]

# Features the trained model uses. Excludes ``return_period_years`` (the percentile ->
# years mapping is not valid) and ``pub_monitored`` (hand-assigned, unsourced). The rarity score
# is recomputed from ``rain_30m`` with quantiles fitted on each model's training years.
MODEL_FEATURE_COLUMNS: list[str] = [
    "rain_5m",
    "rain_15m",
    "rain_30m",
    "rain_60m",
    "rain_120m",
    "rain_decay_72h",
    "storm_rarity_score",
]


class Settings(BaseSettings):
    """Runtime settings, overridable via ``FLOODSENSE_*`` environment variables."""

    # Environment variables win over the repo-root .env (gitignored; holds secrets such as the
    # data.gov.sg API key).
    model_config = SettingsConfigDict(
        env_prefix="FLOODSENSE_", env_file=_DEFAULT_ROOT_DIR / ".env", extra="ignore"
    )

    # Risk tier thresholds (probability)
    risk_low_moderate: float = 0.25
    risk_moderate_high: float = 0.65

    # Temporal constants
    decay_half_life_hours: float = 24.0
    step_minutes: float = 5.0
    prediction_lead_time_minutes: int = 60
    # "Active rain" gate: a zone with less than one gauge tip (0.2 mm) in the last 120 minutes is
    # never scored above zero, and such rows are left out of the feature store. Training and
    # serving share this rule, so dropping the rows changes no alert.
    active_rain_min_mm_120m: float = 0.2
    timezone: str = "Asia/Singapore"

    # Training / evaluation (Phase 4). Years are calendar years in SGT.
    cv_first_validation_year: int = 2020  # forward-chaining CV validates 2020..last_training_year
    last_training_year: int = 2023
    test_start_year: int = 2024  # scored once, by `train --final-report`
    calibration_method: str = (
        "auto"  # "platt", "isotonic", or "auto" (isotonic if enough positives)
    )
    isotonic_min_positives: int = 200
    # False-alarm budgets (false High / Moderate alert episodes per zone per year), chosen by the
    # team on 2026-10-01 from the 2020-2023 out-of-fold trade-off curve (23 validation floods).
    # High 2.5 -> ~44 mm/h, caught 12/23; Moderate 11 -> ~30 mm/h, caught 17/23. A "false" alarm is
    # one not followed by a *reported* flood, so these overstate true false alarms.
    false_alarm_budget_high: float | None = 2.5
    false_alarm_budget_moderate: float | None = 11.0

    # NEA / data.gov.sg endpoints. The v2 API also serves history via ``?date=YYYY-MM-DD``.
    nea_api_primary: str = "https://api-open.data.gov.sg/v2/real-time/api/rainfall"
    nea_api_fallback: str = "https://api.data.gov.sg/v1/environment/rainfall"
    pub_flood_alerts_url: str = "https://api-open.data.gov.sg/v2/real-time/api/weather/flood-alerts"
    # Optional data.gov.sg API key; anonymous use is rate-limited to a few calls per ~10 s.
    data_gov_api_key: str | None = None
    api_timeout_sec: float = 8.0
    api_max_attempts: int = 2
    api_backoff_sec: float = 1.0
    api_page_delay_sec: float = 0.5

    # Hours of live history fetched for rolling features (the 72 h wet-ground feature needs the
    # pipeline's stored history; live mode approximates it from this window).
    live_history_hours: float = 6.0

    # Paths
    root_dir: Path = _DEFAULT_ROOT_DIR

    @cached_property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

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
    def flood_model_path(self) -> Path:
        """Phase 4 model artifact (``FloodModel``)."""
        return self.models_dir / "flood_model.joblib"

    @property
    def replay_file(self) -> Path:
        return self.root_dir / "data" / "replay" / "2021-04-17_western_storm.json"

    @property
    def station_snapshot_file(self) -> Path:
        return self.root_dir / "data" / "reference" / "nea_rainfall_stations.json"

    @property
    def landing_dir(self) -> Path:
        return self.root_dir / "data" / "raw" / "landing_volume"

    # Data products: the NEA rainfall store and sourced flood events (README.md, section Data)
    @property
    def rainfall_dir(self) -> Path:
        """Historical station rainfall store (readings and stations committed; raw downloads local)."""
        return self.root_dir / "data" / "raw" / "rainfall"

    @property
    def rainfall_readings_dir(self) -> Path:
        """Readings as Parquet partitioned by year (``year=YYYY/``); nothing else lives here."""
        return self.rainfall_dir / "readings"

    @property
    def rainfall_stations_file(self) -> Path:
        return self.rainfall_dir / "stations.parquet"

    @property
    def zone_polygons_file(self) -> Path:
        return self.root_dir / "data" / "reference" / "ura_planning_areas_mp2019.geojson"

    @property
    def flood_events_file(self) -> Path:
        return self.root_dir / "data" / "reference" / "flood_events.csv"


settings = Settings()
