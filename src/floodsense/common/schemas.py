"""
FloodSense - Common Schemas and Data Contracts.
Strict Pydantic models ensuring data integrity across ingestion, feature engineering,
model training, and live inference.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from floodsense.common.timeutil import to_sgt


class SeverityLevel(str, Enum):  # noqa: UP042 - StrEnum changes str() output; revisit in a later phase
    MINOR = "Minor"
    MODERATE = "Moderate"
    SEVERE = "Severe"


class RiskTier(str, Enum):  # noqa: UP042 - StrEnum changes str() output; revisit in a later phase
    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"


class StationMetadata(BaseModel):
    """Metadata for an automated weather station."""

    station_id: str = Field(..., description="Unique station identifier, e.g. 'S104'")
    name: str = Field(..., description="Human readable station location name")
    latitude: float = Field(..., ge=1.1, le=1.5, description="Station WGS84 Latitude")
    longitude: float = Field(..., ge=103.5, le=104.1, description="Station WGS84 Longitude")
    is_active: bool = Field(default=True, description="Whether station is currently active")


class RainfallReading(BaseModel):
    """Raw 5-minute rainfall reading from a weather station."""

    station_id: str = Field(..., description="Reporting station ID")
    timestamp: datetime = Field(..., description="Observation timestamp, normalised to SGT")
    rainfall_mm: float = Field(..., ge=0.0, description="5-minute precipitation in mm")
    is_valid: bool = Field(default=True, description="Defensive data quality check flag")

    @field_validator("timestamp")
    @classmethod
    def normalise_timezone(cls, v: datetime) -> datetime:
        return to_sgt(v)

    @field_validator("rainfall_mm")
    @classmethod
    def validate_rainfall_bounds(cls, v: float) -> float:
        # Physical upper bound: > 100mm in 5 minutes is sensor error in Singapore meteorology
        if v > 100.0:
            raise ValueError(f"Anomalous rainfall reading: {v} mm in 5 min")
        return round(v, 2)


class RainfallSnapshot(BaseModel):
    """All station readings for one 5-minute interval, with the station metadata that came with them."""

    timestamp: datetime = Field(..., description="Interval timestamp, normalised to SGT")
    readings: dict[str, float] = Field(..., description="Station ID -> 5-minute rainfall (mm)")
    stations: dict[str, StationMetadata] = Field(
        ..., description="Station ID -> metadata as published alongside these readings"
    )

    @field_validator("timestamp")
    @classmethod
    def normalise_timezone(cls, v: datetime) -> datetime:
        return to_sgt(v)


class ZoneRainfall(BaseModel):
    """IDW-interpolated rainfall mapped to a URA Planning Area."""

    ura_planning_area: str = Field(..., description="Target URA Planning Area (e.g. 'BISHAN')")
    timestamp: datetime = Field(..., description="Timestamp of the aggregated 5-min interval")
    rainfall_mm: float = Field(..., ge=0.0, description="Interpolated rainfall in mm")
    reporting_stations_count: int = Field(
        ..., ge=0, description="Number of active reporting stations"
    )
    total_stations_count: int = Field(..., ge=1, description="Total stations in IDW matrix")


class FloodEvent(BaseModel):
    """Ground truth flood event extracted from official PUB/LTA alerts or news reports."""

    timestamp_start: datetime = Field(..., description="Start timestamp of the flash flood event")
    timestamp_end: datetime | None = Field(None, description="End/clearance timestamp if available")
    location_raw: str = Field(
        ..., description="Raw text location, e.g. 'Dunearn Road near Sime Darby Centre'"
    )
    ura_planning_area: str = Field(
        ..., description="Resolved URA Planning Area (e.g. 'BUKIT TIMAH')"
    )
    severity: Literal["Minor", "Moderate", "Severe"] = Field(
        ..., description="Severity classification"
    )
    source_reference: str = Field(
        ..., description="Source citation, e.g. 'PUB Flash Flood Warning Twitter / Telegram'"
    )
    geocoding_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score of polygon mapping"
    )


class ZoneFeatureVector(BaseModel):
    """Engineered feature vector for a specific URA Planning Area at time t."""

    ura_planning_area: str
    timestamp: datetime
    rain_5m: float = Field(..., ge=0.0, description="Current 5-min rainfall (mm)")
    rain_15m: float = Field(..., ge=0.0, description="Rolling 15-min cumulative rainfall (mm)")
    rain_30m: float = Field(..., ge=0.0, description="Rolling 30-min cumulative rainfall (mm)")
    rain_60m: float = Field(..., ge=0.0, description="Rolling 60-min cumulative rainfall (mm)")
    rain_120m: float = Field(..., ge=0.0, description="Rolling 120-min cumulative rainfall (mm)")
    rain_decay_72h: float = Field(
        ..., ge=0.0, description="72-hour exponential antecedent moisture factor"
    )
    storm_rarity_score: float = Field(
        ..., ge=0.0, le=1.0, description="Empirical quantile score [0, 1]"
    )
    return_period_years: float = Field(..., ge=0.0, description="Estimated return period in years")
    pub_monitored: int = Field(
        ..., ge=0, le=1, description="Binary flag indicating PUB sensor/CCTV presence"
    )


class PredictionResult(BaseModel):
    """Inference output for a specific URA Planning Area."""

    ura_planning_area: str
    timestamp: datetime
    flood_probability: float = Field(
        ..., ge=0.0, le=1.0, description="Model predicted probability of flood within 60 min"
    )
    risk_tier: Literal["Low", "Moderate", "High"] = Field(..., description="Operational alert tier")
    lead_time_min: int = Field(default=60, description="Prediction horizon in minutes")
    contributing_factors: list[str] = Field(default_factory=list, description="Top feature drivers")
