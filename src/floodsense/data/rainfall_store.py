"""
FloodSense - Historical NEA station rainfall store.

PHASE 3 DELIVERABLE (a) - interface only; see docs/phase3-handoff.md and
tests/test_phase3_contract.py. Phase 4 (labels, training, backtests) calls these functions, so
keep the signatures and return types.

On-disk layout under ``settings.rainfall_dir`` (gitignored, never committed):

    data/raw/rainfall/
      readings/year=2017/part-*.parquet ... readings/year=2026/part-*.parquet
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

Zero-rain rows may be dropped to save space. ``load_snapshots`` must then fill them back in as
0.0 for stations that were reporting, so that missing data and dry weather stay distinguishable
(a station with no row in a given 5-minute step and no zero-fill is treated as not reporting).
"""

from datetime import datetime

import pandas as pd

from floodsense.common.schemas import RainfallSnapshot, StationMetadata


def read_rainfall(start: datetime, end: datetime) -> pd.DataFrame:
    """
    Long-format readings with ``start <= timestamp <= end`` (inclusive, SGT).

    Columns: ``station_id``, ``timestamp`` (tz-aware SGT), ``rainfall_mm``. Sorted by timestamp,
    then station_id. No duplicate ``(station_id, timestamp)`` pairs.
    """
    raise NotImplementedError("Phase 3 deliverable (a): see docs/phase3-handoff.md")


def load_station_table() -> pd.DataFrame:
    """The ``stations.parquet`` table described in the module docstring."""
    raise NotImplementedError("Phase 3 deliverable (a): see docs/phase3-handoff.md")


def stations_at(when: datetime) -> dict[str, StationMetadata]:
    """Station metadata valid at ``when`` (stations have moved, e.g. S119 and S215 by ~1 km)."""
    raise NotImplementedError("Phase 3 deliverable (a): see docs/phase3-handoff.md")


def load_snapshots(start: datetime, end: datetime) -> list[RainfallSnapshot]:
    """
    One ``RainfallSnapshot`` per 5-minute step in ``[start, end]``, oldest first.

    Equivalent to what ``NEAPoller.fetch_range`` returns from the API for the same window, so
    ``compute_zone_feature_table`` works on either. Each snapshot's ``stations`` holds the
    metadata valid at that time.
    """
    raise NotImplementedError("Phase 3 deliverable (a): see docs/phase3-handoff.md")
