"""
FloodSense - Historical NEA station rainfall store.

Built by ``floodsense.data.build_rainfall_store`` from NEA's official data (data.gov.sg bulk CSVs
for 2017-2024, the rainfall API after that). Accepted by tests/test_phase3_contract.py; Phase 4
(labels, training, backtests) calls these functions.

On-disk layout under ``settings.rainfall_dir`` (readings and stations.parquet are committed, about
18 MB; the raw CSVs, sightings and manifest stay local):

    data/raw/rainfall/
      readings/year=2017/part-*.parquet ... readings/year=2026/part-*.parquet
          (part-bulk.parquet from the yearly CSV, part-api-YYYY-MM.parquet from the API)
          station_id   string
          timestamp    timestamp[ns, tz=Asia/Singapore]   aligned to 5-minute boundaries
          rainfall_mm  float64                            5-minute total, 0 <= x <= 100
      stations.parquet                                    (outside readings/ so the dataset
          station_id   string                             reads as one schema)
          name         string
          latitude     float64
          longitude    float64
          valid_from   timestamp[ns, tz=Asia/Singapore]   null = since the start of the record
          valid_to     timestamp[ns, tz=Asia/Singapore]   null = still current

Zero-rain readings are stored as rows like any other. Missing is not dry: a station with no row
in a given 5-minute step did not report, and nothing here fills it in as 0.0 - the IDW layer
rebalances over the stations that did report.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads

from floodsense.common.config import settings
from floodsense.common.schemas import RainfallSnapshot, StationMetadata
from floodsense.common.timeutil import to_sgt

SGT = "Asia/Singapore"
_TS_TYPE = pa.timestamp("ns", tz=SGT)


def _year_files(year: int) -> list[Path]:
    """Bulk part first, so it wins over an API part covering the same readings."""
    files = sorted((settings.rainfall_readings_dir / f"year={year}").glob("part-*.parquet"))
    return sorted(files, key=lambda f: f.name != "part-bulk.parquet")


def read_rainfall(start: datetime, end: datetime) -> pd.DataFrame:
    """
    Long-format readings with ``start <= timestamp <= end`` (inclusive, SGT).

    Columns: ``station_id``, ``timestamp`` (tz-aware SGT), ``rainfall_mm``. Sorted by timestamp,
    then station_id. No duplicate ``(station_id, timestamp)`` pairs. Raises ``FileNotFoundError``
    when the store has not been built.
    """
    if not settings.rainfall_readings_dir.exists():
        raise FileNotFoundError(f"rainfall store not built: {settings.rainfall_readings_dir}")
    lo, hi = pd.Timestamp(to_sgt(start)), pd.Timestamp(to_sgt(end))
    files = [f for y in range(lo.year, hi.year + 1) for f in _year_files(y)]
    columns = ["station_id", "timestamp", "rainfall_mm"]
    if not files:
        return _empty_readings()
    ts = pads.field("timestamp")
    table = pads.dataset([str(f) for f in files], format="parquet").to_table(
        columns=columns,
        filter=(ts >= pa.scalar(lo, type=_TS_TYPE)) & (ts <= pa.scalar(hi, type=_TS_TYPE)),
    )
    df = table.to_pandas()
    df["station_id"] = df["station_id"].astype(str)
    df["timestamp"] = df["timestamp"].dt.tz_convert(SGT)
    df = df.drop_duplicates(["station_id", "timestamp"], keep="first")
    return df.sort_values(["timestamp", "station_id"], kind="stable").reset_index(drop=True)


def _empty_readings() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "station_id": pd.Series(dtype=str),
            "timestamp": pd.Series(dtype=f"datetime64[ns, {SGT}]"),
            "rainfall_mm": pd.Series(dtype="float64"),
        }
    )


def load_station_table() -> pd.DataFrame:
    """The ``stations.parquet`` table described in the module docstring."""
    if not settings.rainfall_stations_file.exists():
        raise FileNotFoundError(f"station table not built: {settings.rainfall_stations_file}")
    table = pd.read_parquet(settings.rainfall_stations_file)
    table["station_id"] = table["station_id"].astype(str)
    table["name"] = table["name"].astype(str)
    return table


def _stations_at(table: pd.DataFrame, when: pd.Timestamp) -> dict[str, StationMetadata]:
    started = table["valid_from"].isna() | (table["valid_from"] <= when)
    not_ended = table["valid_to"].isna() | (when < table["valid_to"])
    current = table[started & not_ended]
    return {
        row.station_id: StationMetadata(
            station_id=row.station_id, name=row.name, latitude=row.latitude, longitude=row.longitude
        )
        for row in current.itertuples(index=False)
    }


def stations_at(when: datetime) -> dict[str, StationMetadata]:
    """Station metadata valid at ``when`` (stations have moved, e.g. S119 and S215 by ~1 km)."""
    return _stations_at(load_station_table(), pd.Timestamp(to_sgt(when)))


def load_snapshots(start: datetime, end: datetime) -> list[RainfallSnapshot]:
    """
    One ``RainfallSnapshot`` per 5-minute step in ``[start, end]`` that has any readings, oldest
    first.

    Equivalent to what ``NEAPoller.fetch_range`` returns from the API for the same window, so
    ``compute_zone_feature_table`` works on either. Each snapshot's ``stations`` holds the
    metadata valid at that time (one shared dict per station-location period, not a copy per step).
    """
    readings = read_rainfall(start, end)
    if readings.empty:
        return []
    table = load_station_table()
    # Metadata only changes where a location period starts or ends; resolve it once per segment.
    stamps = readings["timestamp"].drop_duplicates()
    changes = pd.concat([table["valid_from"], table["valid_to"]]).dropna()
    changes = sorted(set(changes[(changes > stamps.iloc[0]) & (changes <= stamps.iloc[-1])]))
    bounds = [stamps.iloc[0], *changes]
    metas = [_stations_at(table, b) for b in bounds]

    snapshots: list[RainfallSnapshot] = []
    seg = 0
    for ts, group in readings.groupby("timestamp", sort=True):
        while seg + 1 < len(bounds) and ts >= bounds[seg + 1]:
            seg += 1
        snapshots.append(
            RainfallSnapshot.model_construct(
                timestamp=ts.to_pydatetime(),
                readings=dict(
                    zip(group["station_id"], group["rainfall_mm"].astype(float), strict=True)
                ),
                stations=metas[seg],
            )
        )
    return snapshots
