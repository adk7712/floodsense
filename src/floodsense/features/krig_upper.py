"""
FloodSense - Kriged upper rain feature (pre-registered test T2, docs/mentor_tests_2026-10.md).

``rain_60m_krig_upper`` per zone and 5-minute step: the ordinary-kriging mean plus one kriging
standard deviation (clipped at 0, per step), summed over the last 12 steps. The 60-minute sum adds
the per-step SDs linearly, i.e. as if the steps' errors were fully correlated (conservative upper
bound).

* Variogram: the fitted exponential one stored in ``docs/results/kriging_vs_idw.json`` (normalised
  units; ``range_km`` is the practical range). The kriging variance is scaled to mm^2 by the
  population variance (ddof=0) of the step's reporting gauge values.
* Built exactly like ``build_features.build_month`` (72 h warm-up, per-month station union, same
  contiguous 5-minute grid via ``interpolate_snapshots``, same zone reference points), with the
  kriging engine in place of IDW. Zone rain per step is rounded to 2 dp, as for IDW.
* Output is restricted (inner join) to the (zone, timestamp) rows present in the existing feature
  store for that month, so only the rain column differs. Missing gauges are left out of each
  step's system; they are never read as 0. Steps where no gauge reports give 0, as in the IDW
  pipeline (the store's gate decides whether such rows exist).
* Only 2017-2023: 2024+ is the held-out test set and is refused.

    python -m floodsense.features.krig_upper --start-year 2017 --end-year 2023 \\
        --out-dir data/processed/features_krig_upper
"""

import argparse
import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.data import rainfall_store
from floodsense.features.build_features import (
    WARMUP,
    _month_bounds,
    _station_union,
    default_store_dir,
)
from floodsense.features.zone_features import build_idw_engine, interpolate_snapshots
from floodsense.spatial.compare_interpolation import assert_training_window
from floodsense.spatial.idw_matrix import IDWMatrixEngine
from floodsense.spatial.kriging import KrigingUpperEngine, Variogram

logger = logging.getLogger("FloodSense.KrigUpper")

COLUMN = "rain_60m_krig_upper"
WINDOW_STEPS = 12
VARIOGRAM_JSON = settings.root_dir / "docs" / "results" / "kriging_vs_idw.json"


def load_variogram(path: Path = VARIOGRAM_JSON) -> Variogram:
    """The fitted exponential variogram from the 16 Oct kriging-vs-IDW result file."""
    v = json.loads(path.read_text())["variogram"]["exponential"]
    return Variogram(float(v["nugget"]), float(v["partial_sill"]), float(v["range_km"]), v["model"])


class _KrigAsIDW(IDWMatrixEngine):
    """IDW engine whose ``interpolate_matrix`` is the kriged upper rain (reuses the grid code)."""

    def __init__(self, base: IDWMatrixEngine, variogram: Variogram, upper_sd: float):
        super().__init__(zones=base.zones, stations=base.stations)
        lat = lambda d, k: np.array([d[k_]["lat"] for k_ in k])  # noqa: E731
        lon = lambda d, k: np.array([d[k_]["lon"] for k_ in k])  # noqa: E731
        self.kriger = KrigingUpperEngine(
            lat(self.stations, self.station_ids),
            lon(self.stations, self.station_ids),
            lat(self.zones, self.zone_names),
            lon(self.zones, self.zone_names),
            variogram,
            upper_sd,
        )

    def interpolate_matrix(self, station_values: np.ndarray) -> np.ndarray:
        return self.kriger.interpolate_matrix(station_values)


def build_month(
    year: int, month: int, variogram: Variogram | None = None, upper_sd: float = 1.0
) -> pd.DataFrame | None:
    """``rain_60m_krig_upper`` on the feature-store rows of one month, or None if no snapshots."""
    assert_training_window(year, year)
    variogram = variogram or load_variogram()
    start, last_step = _month_bounds(year, month)
    snapshots = rainfall_store.load_snapshots(
        (start - WARMUP).to_pydatetime(), last_step.to_pydatetime()
    )
    if not snapshots:
        return None
    stations = _station_union(snapshots)
    engine = _KrigAsIDW(build_idw_engine(stations), variogram, upper_sd)
    zone_rain = interpolate_snapshots(snapshots, stations, engine)

    n_zones = len(engine.zone_names)
    grid = zone_rain["timestamp"].to_numpy()[::n_zones]
    rain = zone_rain["rainfall_mm"].to_numpy().reshape(len(grid), n_zones)
    csum = np.vstack([np.zeros((1, n_zones)), np.cumsum(rain, axis=0)])
    lo = np.maximum(np.arange(1, len(grid) + 1) - WINDOW_STEPS, 0)
    rolled = csum[1:] - csum[lo]  # partial windows at the start, as the store (warm-up dropped)
    table = pd.DataFrame(
        {
            "ura_planning_area": np.tile(engine.zone_names, len(grid)),
            "timestamp": np.repeat(grid, n_zones),
            COLUMN: rolled.ravel(),
        }
    )
    table = table[table["timestamp"] >= start]

    store_path = default_store_dir() / f"year={year}" / f"month={month:02d}.parquet"
    keys = pd.read_parquet(store_path, columns=["ura_planning_area", "timestamp"])
    out = keys.merge(table, on=["ura_planning_area", "timestamp"], how="left")
    missing = int(out[COLUMN].isna().sum())
    if missing:
        raise RuntimeError(f"{year}-{month:02d}: {missing} store rows have no kriged value")
    return out


def build_krig_upper(start_year: int, end_year: int, out_dir: Path) -> Path:
    assert_training_window(start_year, end_year)
    variogram = load_variogram()
    logger.info("Variogram: %s", variogram)
    t_all = time.perf_counter()
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            t0 = time.perf_counter()
            if not (default_store_dir() / f"year={year}" / f"month={month:02d}.parquet").exists():
                logger.warning("%d-%02d: no feature-store month; skipped", year, month)
                continue
            frame = build_month(year, month, variogram)
            if frame is None:
                logger.warning("%d-%02d: no snapshots; skipped", year, month)
                continue
            path = out_dir / f"year={year}" / f"month={month:02d}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            frame.to_parquet(tmp, index=False)
            tmp.replace(path)
            logger.info(
                "%d-%02d: %d rows in %.1fs -> %s",
                year,
                month,
                len(frame),
                time.perf_counter() - t0,
                path,
            )
    logger.info("Done in %.1fs", time.perf_counter() - t_all)
    return out_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the kriged upper 60-minute rain column.")
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        assert_training_window(args.start_year, args.end_year)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2
    build_krig_upper(args.start_year, args.end_year, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
