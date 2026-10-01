"""
FloodSense - Zone feature table from station rainfall snapshots.

Turns a sequence of 5-minute station snapshots into per-zone engineered features. This is the one
path used for both replay and live mode, so features at time ``t`` are always computed from the
full history up to ``t`` and never from interaction state.
"""

import logging
from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.common.schemas import RainfallSnapshot, StationMetadata
from floodsense.features.feature_pipeline import FeaturePipeline
from floodsense.spatial.idw_matrix import IDWMatrixEngine

logger = logging.getLogger("FloodSense.ZoneFeatures")


def build_idw_engine(stations: Mapping[str, StationMetadata]) -> IDWMatrixEngine:
    """IDW engine over exactly the stations described in the payload or replay file."""
    return IDWMatrixEngine(
        stations={
            sid: {"name": s.name, "lat": s.latitude, "lon": s.longitude}
            for sid, s in stations.items()
        }
    )


def interpolate_snapshots(
    snapshots: Sequence[RainfallSnapshot],
    stations: Mapping[str, StationMetadata],
    engine: IDWMatrixEngine | None = None,
) -> pd.DataFrame:
    """
    Zone rainfall on a contiguous 5-minute grid.

    Returns long-format rows ``(ura_planning_area, timestamp, rainfall_mm, reporting_stations)``.
    Grid steps with no snapshot are treated as no station reporting (zone rainfall 0).
    """
    if not snapshots:
        raise ValueError("No rainfall snapshots to interpolate")
    engine = engine or build_idw_engine(stations)

    unknown = {sid for s in snapshots for sid in s.readings} - set(engine.station_ids)
    if unknown:
        logger.warning(
            "Readings from %d stations without coordinates ignored: %s",
            len(unknown),
            sorted(unknown),
        )

    step = pd.Timedelta(minutes=settings.step_minutes)
    by_time = {pd.Timestamp(s.timestamp): s.readings for s in snapshots}
    grid = pd.date_range(min(by_time), max(by_time), freq=step)

    values = np.full((len(grid), len(engine.station_ids)), np.nan)
    col = {sid: j for j, sid in enumerate(engine.station_ids)}
    for i, ts in enumerate(grid):
        for sid, mm in by_time.get(ts, {}).items():
            if sid in col:
                values[i, col[sid]] = mm

    zone_rain = np.round(engine.interpolate_matrix(values), 2)
    reporting = (~np.isnan(values)).sum(axis=1)

    return pd.DataFrame(
        {
            "ura_planning_area": np.tile(engine.zone_names, len(grid)),
            "timestamp": np.repeat(grid, len(engine.zone_names)),
            "rainfall_mm": zone_rain.ravel(),
            "reporting_stations": np.repeat(reporting, len(engine.zone_names)),
        }
    )


def compute_zone_feature_table(
    snapshots: Sequence[RainfallSnapshot],
    stations: Mapping[str, StationMetadata],
    engine: IDWMatrixEngine | None = None,
) -> pd.DataFrame:
    """Engineered features for every zone at every 5-minute step, using only past data."""
    zone_rain = interpolate_snapshots(snapshots, stations, engine)
    features = FeaturePipeline().process_batch_dataframe(
        zone_rain[["ura_planning_area", "timestamp", "rainfall_mm"]], prune_zero_rain=False
    )
    reporting = zone_rain[["timestamp", "reporting_stations"]].drop_duplicates("timestamp")
    return features.merge(reporting, on="timestamp", how="left")
