"""
FloodSense - Historical NEA station rainfall store.

PHASE 3 DELIVERABLE (a) - implementation.
Provides efficient query and snapshot loading over the on-disk Parquet store in ``settings.rainfall_dir``.

On-disk layout under ``settings.rainfall_dir`` (gitignored, never committed):
    data/raw/rainfall/
      readings/year=2017/part-*.parquet ... readings/year=2026/part-*.parquet
          station_id   string
          timestamp    timestamp[ns, tz=Asia/Singapore]   aligned to 5-minute boundaries
          rainfall_mm  float64                            5-minute total, 0 <= x <= 100
      stations.parquet
          station_id   string
          name         string
          latitude     float64
          longitude    float64
          valid_from   timestamp[ns, tz=Asia/Singapore]
          valid_to     timestamp[ns, tz=Asia/Singapore]
"""

from datetime import datetime

import pandas as pd
import pyarrow.dataset as ds

from floodsense.common.config import settings
from floodsense.common.schemas import RainfallSnapshot, StationMetadata
from floodsense.common.timeutil import to_sgt


def load_station_table() -> pd.DataFrame:
    """The ``stations.parquet`` table containing metadata for all historical stations."""
    if not settings.rainfall_stations_file.exists():
        raise FileNotFoundError(f"Missing station table at {settings.rainfall_stations_file}")
    df = pd.read_parquet(settings.rainfall_stations_file)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_convert(settings.tzinfo)
    for col in ["valid_from", "valid_to"]:
        if col in df.columns and pd.api.types.is_datetime64_any_dtype(df[col]):
            if df[col].dt.tz is None:
                df[col] = df[col].dt.tz_localize(settings.tzinfo)
            else:
                df[col] = df[col].dt.tz_convert(settings.tzinfo)
    return df


def stations_at(when: datetime) -> dict[str, StationMetadata]:
    """Station metadata valid at ``when`` (accounting for station movements / validity windows)."""
    df = load_station_table()
    when_sgt = to_sgt(when)
    when_ts = pd.Timestamp(when_sgt)

    # Filter rows valid at when
    if "valid_from" in df.columns and "valid_to" in df.columns:
        valid_mask = (
            (df["valid_from"].isna() | (df["valid_from"] <= when_ts))
            & (df["valid_to"].isna() | (df["valid_to"] >= when_ts))
        )
        sub_df = df[valid_mask]
        if sub_df.empty:
            sub_df = df
    else:
        sub_df = df

    # Build dictionary
    stations: dict[str, StationMetadata] = {}
    for _, row in sub_df.iterrows():
        sid = str(row["station_id"])
        stations[sid] = StationMetadata(
            station_id=sid,
            name=str(row["name"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            is_active=True,
        )
    return stations


def read_rainfall(start: datetime, end: datetime) -> pd.DataFrame:
    """
    Long-format readings with ``start <= timestamp <= end`` (inclusive, SGT).

    Columns: ``station_id``, ``timestamp`` (tz-aware SGT), ``rainfall_mm``. Sorted by timestamp,
    then station_id. No duplicate ``(station_id, timestamp)`` pairs.
    """
    if not settings.rainfall_readings_dir.exists():
        raise FileNotFoundError(f"Missing readings directory at {settings.rainfall_readings_dir}")

    start_sgt = to_sgt(start)
    end_sgt = to_sgt(end)

    start_year = start_sgt.year
    end_year = end_sgt.year

    # Load from parquet dataset with year partition filtering
    dataset = ds.dataset(settings.rainfall_readings_dir, format="parquet", partitioning="hive")
    
    # Filter by timestamp range
    table = dataset.to_table(
        filter=(ds.field("year") >= start_year) & (ds.field("year") <= end_year),
        columns=["station_id", "timestamp", "rainfall_mm"]
    )
    df = table.to_pandas()

    if df.empty:
        return pd.DataFrame(columns=["station_id", "timestamp", "rainfall_mm"])

    # Ensure timezone is Asia/Singapore
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(settings.tzinfo)
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert(settings.tzinfo)

    # Filter to exact [start_sgt, end_sgt]
    mask = (df["timestamp"] >= pd.Timestamp(start_sgt)) & (df["timestamp"] <= pd.Timestamp(end_sgt))
    df = df[mask].copy()

    # Drop duplicates if any and sort
    df = df.drop_duplicates(subset=["station_id", "timestamp"])
    df = df.sort_values(by=["timestamp", "station_id"]).reset_index(drop=True)
    return df[["station_id", "timestamp", "rainfall_mm"]]


def load_snapshots(start: datetime, end: datetime) -> list[RainfallSnapshot]:
    """
    One ``RainfallSnapshot`` per 5-minute step in ``[start, end]``, oldest first.

    Equivalent to what ``NEAPoller.fetch_range`` returns from the API for the same window.
    Each snapshot's ``stations`` holds the metadata valid at that time.
    """
    start_sgt = to_sgt(start)
    end_sgt = to_sgt(end)

    # Align start and end to 5-minute boundaries
    start_aligned = start_sgt.replace(
        minute=(start_sgt.minute // 5) * 5, second=0, microsecond=0
    )
    end_aligned = end_sgt.replace(
        minute=(end_sgt.minute // 5) * 5, second=0, microsecond=0
    )

    # Generate contiguous 5-minute timestamp sequence
    grid = pd.date_range(start_aligned, end_aligned, freq="5min", tz=settings.tzinfo)

    # Read readings from parquet
    df = read_rainfall(start_aligned, end_aligned)

    # Pre-index readings by timestamp
    readings_by_ts: dict[datetime, dict[str, float]] = {}
    if not df.empty:
        for ts, group in df.groupby("timestamp"):
            ts_dt = ts.to_pydatetime()
            readings_by_ts[ts_dt] = dict(zip(group["station_id"], group["rainfall_mm"], strict=False))

    # Station metadata cache
    station_table = load_station_table()
    all_stations = {
        str(row["station_id"]): StationMetadata(
            station_id=str(row["station_id"]),
            name=str(row["name"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            is_active=True,
        )
        for _, row in station_table.iterrows()
    }

    snapshots: list[RainfallSnapshot] = []
    for ts_val in grid:
        ts_dt = ts_val.to_pydatetime()
        readings = readings_by_ts.get(ts_dt, {})
        snapshots.append(
            RainfallSnapshot(
                timestamp=ts_dt,
                readings=readings,
                stations=all_stations,
            )
        )

    return snapshots
