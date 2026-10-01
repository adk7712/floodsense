"""
FloodSense - Replay any day in the rainfall store (logic for the app, kept free of Streamlit).

A day is replayed from the committed NEA rainfall store (``floodsense.data.rainfall_store``) with
72 hours of readings before it, so the wet-ground feature is complete, exactly as in training.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from floodsense.common.config import settings
from floodsense.common.schemas import FloodEvent, StationMetadata
from floodsense.data import rainfall_store
from floodsense.features.zone_features import compute_zone_feature_table

STEPS_PER_DAY = 288
SPARSE_SHARE = 0.9  # under this share of 5-minute steps with readings, warn that the day is patchy
WARMUP = timedelta(hours=72)
PINNED_STORM = date(2021, 4, 17)


@dataclass
class DayView:
    day: date
    features: pd.DataFrame  # every zone x 5-minute step of the day (empty if no readings)
    total_stations: int  # gauges that reported at least once in the window (incl. warm-up)
    step_share: float  # share of the day's 288 steps with at least one reading


def store_available() -> bool:
    return settings.rainfall_readings_dir.exists() and settings.rainfall_stations_file.exists()


def store_date_range() -> tuple[date, date]:
    """First and last day with readings (read from the first and last year partitions)."""
    years = sorted(
        int(p.name.split("=", 1)[1]) for p in settings.rainfall_readings_dir.glob("year=*")
    )
    if not years:
        raise FileNotFoundError(f"no readings in {settings.rainfall_readings_dir}")
    first = pd.read_parquet(
        settings.rainfall_readings_dir / f"year={years[0]}", columns=["timestamp"]
    )
    last = pd.read_parquet(
        settings.rainfall_readings_dir / f"year={years[-1]}", columns=["timestamp"]
    )
    return first["timestamp"].min().date(), last["timestamp"].max().date()


def day_view(day: date) -> DayView:
    """Zone features for every step of ``day`` (SGT), computed with 72 h of warm-up readings."""
    tz = settings.tzinfo
    start = datetime.combine(day, time(0, 0), tz)
    end = start + timedelta(days=1) - timedelta(minutes=settings.step_minutes)
    snaps = rainfall_store.load_snapshots(start - WARMUP, end)
    in_day = [s for s in snaps if s.timestamp >= start]
    if not in_day:
        return DayView(day, pd.DataFrame(), 0, 0.0)
    stations: dict[str, StationMetadata] = {}
    for s in snaps:
        stations.update(s.stations)
    table = compute_zone_feature_table(snaps, stations)
    stamps = pd.to_datetime(table["timestamp"])
    table = table[(stamps >= pd.Timestamp(start)) & (stamps <= pd.Timestamp(end))]
    return DayView(
        day=day,
        features=table.reset_index(drop=True),
        total_stations=len({sid for s in snaps for sid in s.readings}),
        step_share=len({s.timestamp for s in in_day}) / STEPS_PER_DAY,
    )


def default_time(features: pd.DataFrame) -> pd.Timestamp:
    """The step with the heaviest island-wide 30-minute rain (mean over zones): the storm's peak."""
    by_step = features.groupby("timestamp")["rain_30m"].mean()
    return pd.Timestamp(by_step.idxmax())


def events_on(day: date, events: Sequence[FloodEvent]) -> list[FloodEvent]:
    """Reported floods that started on ``day`` (SGT), in time order."""
    return sorted(
        (e for e in events if e.timestamp_start.date() == day), key=lambda e: e.timestamp_start
    )


def featured_storms(events: Sequence[FloodEvent], n: int = 6) -> list[tuple[date, str]]:
    """
    Days worth replaying, with a short label. 17 Apr 2021 first, then the days with the most
    reported floods (ties: more severe, then more events with a known time, then more recent).
    """
    by_day: dict[date, list[FloodEvent]] = {}
    for e in events:
        by_day.setdefault(e.timestamp_start.date(), []).append(e)

    def key(d: date) -> tuple[int, int, int, int, date]:
        evs = by_day[d]
        return (
            len(evs),
            sum(e.severity == "Severe" for e in evs),
            sum(e.severity == "Moderate" for e in evs),
            sum(e.time_precision != "day_only" for e in evs),
            d,
        )

    days = sorted(by_day, key=key, reverse=True)
    ordered = ([PINNED_STORM] if PINNED_STORM in by_day else []) + [
        d for d in days if d != PINNED_STORM
    ]
    out = []
    for d in ordered[:n]:
        zones = sorted({e.ura_planning_area.title() for e in by_day[d]})
        out.append((d, f"{d:%d %b %Y} · {', '.join(zones)}"))
    return out
