"""
FloodSense - Feature store builder.

Turns the historical rainfall store (``floodsense.data.rainfall_store``) into per-zone feature rows,
one Parquet file per calendar month (Singapore time):

    data/processed/features/year=YYYY/month=MM.parquet

Each month is computed from its own snapshots plus a 72-hour warm-up before the month starts, so
the wet-ground decay feature is continuous across month boundaries; the warm-up rows are dropped
before writing. Rows failing the active-rain gate are pruned (see ``prune_dry_rows``); the labelling
and evaluation code treats absent rows as "no alert", which is what the scorer would give them.

    python -m floodsense.features.build_features --start-year 2017 --end-year 2026

There is no synthetic fallback: if the rainfall store is missing the build fails.
"""

import argparse
import logging
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pandas as pd

from floodsense.common.config import settings
from floodsense.common.schemas import RainfallSnapshot, StationMetadata
from floodsense.data import rainfall_store
from floodsense.features.zone_features import compute_zone_feature_table

logger = logging.getLogger("FloodSense.BuildFeatures")

WARMUP = timedelta(hours=72)
DATA_DOC = "README.md, section Data"


class DataUnavailableError(RuntimeError):
    """A Phase 3 data product (rainfall store, flood events, feature store) is not available."""


def default_store_dir() -> Path:
    return settings.root_dir / "data" / "processed" / "features"


def prune_dry_rows(features: pd.DataFrame) -> pd.DataFrame:
    """Keep only rows that pass the active-rain gate (``settings.active_rain_min_mm_120m``).

    The scoring layer (``floodsense.models.scoring.predict_probabilities``) gives gated rows a
    probability of 0, so an absent row and a scored dry row mean the same thing: no alert. On real
    data this keeps roughly a fifth of rows; the earlier "dry and decay < 1" rule kept nearly all
    of them, because Singapore's 72-hour wetness rarely falls that low.
    """
    active = features["rain_120m"].to_numpy(dtype=float) >= settings.active_rain_min_mm_120m
    return features[active].reset_index(drop=True)


def _month_bounds(year: int, month: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """First instant of the month and its last 5-minute step, in SGT."""
    start = pd.Timestamp(year=year, month=month, day=1, tz=settings.tzinfo)
    next_start = start + pd.offsets.MonthBegin(1)
    return start, next_start - pd.Timedelta(minutes=settings.step_minutes)


def _station_union(snapshots: Sequence[RainfallSnapshot]) -> dict[str, StationMetadata]:
    """Station metadata across the window; later snapshots win."""
    stations: dict[str, StationMetadata] = {}
    for snap in snapshots:
        stations.update(snap.stations)
    return stations


def build_month(year: int, month: int) -> pd.DataFrame | None:
    """Feature rows for one SGT calendar month, or None when the store has no snapshots for it."""
    start, last_step = _month_bounds(year, month)
    window_start = start - WARMUP
    try:
        snapshots = rainfall_store.load_snapshots(
            window_start.to_pydatetime(), last_step.to_pydatetime()
        )
    except NotImplementedError as exc:
        raise DataUnavailableError(
            f"The historical rainfall store could not be read ({exc}). See {DATA_DOC}."
        ) from exc
    except FileNotFoundError as exc:
        raise DataUnavailableError(
            f"Historical rainfall store not found at {settings.rainfall_dir} ({exc}). "
            f"See {DATA_DOC}."
        ) from exc
    if not snapshots:
        return None

    table = compute_zone_feature_table(snapshots, _station_union(snapshots))
    table = table[table["timestamp"] >= start]  # drop the warm-up
    return prune_dry_rows(table)


def build_feature_store(start_year: int, end_year: int, out_dir: Path | None = None) -> Path:
    """Write monthly feature files for ``start_year..end_year`` (inclusive); returns the root."""
    if end_year < start_year:
        raise ValueError("end_year must not be before start_year")
    out_dir = out_dir or default_store_dir()
    written = 0
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            features = build_month(year, month)
            if features is None:
                logger.warning("No snapshots for %d-%02d; month skipped", year, month)
                continue
            path = out_dir / f"year={year}" / f"month={month:02d}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            features.to_parquet(tmp, index=False)
            tmp.replace(path)  # overwrites a previous build of the same month
            logger.info("%d-%02d: %d rows -> %s", year, month, len(features), path)
            written += 1
    if not written:
        raise DataUnavailableError(
            f"No rainfall snapshots found for {start_year}-{end_year} "
            f"(rainfall store: {settings.rainfall_dir}, exists: {settings.rainfall_dir.exists()}). "
            f"See {DATA_DOC}."
        )
    return out_dir


def load_feature_store(years: list[int] | None = None, root: Path | None = None) -> pd.DataFrame:
    """Read the feature store back (tz-aware SGT timestamps), sorted by zone then time."""
    root = root or default_store_dir()
    if years is None:
        files = sorted(root.glob("year=*/month=*.parquet")) if root.exists() else []
    else:
        files = sorted(f for y in years for f in root.glob(f"year={y}/month=*.parquet"))
    if not files:
        raise DataUnavailableError(
            f"No feature store at {root}. Build it first: "
            f"python -m floodsense.features.build_features --start-year 2017 --end-year 2026 "
            f"(needs the rainfall store; see {DATA_DOC})."
        )
    frame = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    return frame.sort_values(["ura_planning_area", "timestamp"]).reset_index(drop=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the monthly zone feature store.")
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        out = build_feature_store(args.start_year, args.end_year, args.out_dir)
    except DataUnavailableError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("Feature store written to %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
