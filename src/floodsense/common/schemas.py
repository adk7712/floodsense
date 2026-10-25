"""
FloodSense - Common Schemas and Data Contracts.
Strict Pydantic models ensuring data integrity across ingestion, feature engineering,
model training, and live inference.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from floodsense.common.timeutil import to_sgt


class StationMetadata(BaseModel):
    """Metadata for an automated weather station."""

    station_id: str = Field(..., description="Unique station identifier, e.g. 'S104'")
    name: str = Field(..., description="Human readable station location name")
    latitude: float = Field(..., ge=1.1, le=1.5, description="Station WGS84 Latitude")
    longitude: float = Field(..., ge=103.5, le=104.1, description="Station WGS84 Longitude")


class RainfallReading(BaseModel):
    """Raw 5-minute rainfall reading from a weather station."""

    station_id: str = Field(..., description="Reporting station ID")
    timestamp: datetime = Field(..., description="Observation timestamp, normalised to SGT")
    rainfall_mm: float = Field(..., ge=0.0, description="5-minute precipitation in mm")

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
    """A flood event from a cited source (see ``floodsense.data.flood_events``) or a parsed alert."""

    event_id: str = Field(
        default="", description="Unique id, e.g. '2021-04-17-bukit-timah-dunearn'"
    )
    timestamp_start: datetime = Field(..., description="When flooding began, normalised to SGT")
    timestamp_end: datetime | None = Field(None, description="End/clearance timestamp if available")
    time_precision: Literal["exact", "approx_15min", "approx_hour", "day_only", "pub_issued"] = (
        Field(default="approx_hour", description="How precisely the source gives the start time")
    )
    location_raw: str = Field(
        ..., description="Raw text location, e.g. 'Dunearn Road near Sime Darby Centre'"
    )
    ura_planning_area: str = Field(
        ..., description="Resolved URA Planning Area (e.g. 'BUKIT TIMAH')"
    )
    severity: Literal["Minor", "Moderate", "Severe"] = Field(
        ..., description="Severity classification"
    )
    cause: Literal["rain", "rain_tide", "other"] = Field(default="rain")
    source_url: str = Field(default="", description="Article / PUB post the event was taken from")
    source_name: str = Field(default="", description="e.g. 'PUB press release', 'CNA'")
    evidence_quote: str = Field(
        default="", description="Sentence from the source naming place and time"
    )
    verified_by: str = Field(default="", description="Who opened the source and confirmed the row")
    drafted_by: str = Field(default="", description="Who drafted the row; never the verifier")
    notes: str = Field(default="")

    @field_validator("timestamp_start")
    @classmethod
    def normalise_start(cls, v: datetime) -> datetime:
        return to_sgt(v)

    @field_validator("timestamp_end")
    @classmethod
    def normalise_end(cls, v: datetime | None) -> datetime | None:
        return to_sgt(v) if v is not None else None
