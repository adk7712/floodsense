"""
FloodSense - Calibrate the radar colour scale (level → mm/h) against NEA's rain gauges.

MSS publishes the colour order (light → heavy) but no numbers. Each archived 70 km frame is paired
with every gauge's 5-minute reading at the same timestamp (zero lag correlated best), reading the
radar level at the gauge's pixel. The product is in dBR, so the rate should grow exponentially with
level; ``log10(R) = a + b·level`` is fitted on levels 12–32, where 5-minute gauge totals resolve
the rate well (one 0.2 mm tip = 2.4 mm/h). Levels 1–8 use the measured gauge mean instead, because
the curve overestimates light rain there.

    uv run python -m floodsense.radar.calibrate --radar-dir data/raw/radar --start 2026-07-16 --end 2026-09-30

Writes ``data/reference/radar_colour_scale.csv`` and prints the agreement checks.
"""

import argparse
import logging
from datetime import date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.data.rainfall_store import load_station_table, read_rainfall
from floodsense.radar.decode import COLOUR_SCALE, N_LEVELS, SCALE_FILE, BoundingBox, decode_levels

logger = logging.getLogger("FloodSense.RadarCalibrate")

FIT_LEVELS = range(12, 33)
EMPIRICAL_LEVELS = range(1, 9)


def radar_at_gauges(radar_dir: Path, start: date, end: date) -> pd.DataFrame:
    """The radar level at every gauge's pixel, for every archived 70 km frame in the window."""
    stations = (
        load_station_table().drop_duplicates("station_id", keep="last").set_index("station_id")
    )
    parts = []
    day = start
    while day <= end:
        f = radar_dir / "70km" / f"date={day.isoformat()}" / "frames.parquet"
        day = date.fromordinal(day.toordinal() + 1)
        if not f.exists():
            continue
        frames = pd.read_parquet(f)
        bbox = BoundingBox.from_row(frames.iloc[0])
        for frame in frames.itertuples():
            levels = decode_levels(frame.png)
            pix = [
                bbox.pixel(lat, lon, levels.shape)
                for lat, lon in zip(stations.latitude, stations.longitude, strict=True)
            ]
            inside = [
                (r, c) for r, c in pix if 0 <= r < levels.shape[0] and 0 <= c < levels.shape[1]
            ]
            if len(inside) != len(pix):
                raise ValueError("a gauge falls outside the 70 km frame")
            parts.append(
                pd.DataFrame(
                    {
                        "timestamp": pd.Timestamp(frame.timestamp_sgt),
                        "station_id": stations.index,
                        "level": [levels[r, c] for r, c in pix],
                    }
                )
            )
    return pd.concat(parts, ignore_index=True)


def fit_scale(pairs: pd.DataFrame) -> tuple[pd.DataFrame, float, float]:
    """Per-level gauge statistics and the calibrated rate; returns (table, a, b)."""
    wet = pairs[pairs.level > 0]
    stats = wet.groupby("level")["gauge_mm_h"].agg(n_pairs="size", gauge_mean_mm_h="mean")
    stats = stats.reindex(range(1, N_LEVELS + 1))
    fit = stats.loc[list(FIT_LEVELS)].dropna()
    b, a = np.polyfit(fit.index, np.log10(fit["gauge_mean_mm_h"]), 1, w=np.sqrt(fit["n_pairs"]))
    rows = []
    for level in range(1, N_LEVELS + 1):
        empirical = level in EMPIRICAL_LEVELS
        rate = stats.loc[level, "gauge_mean_mm_h"] if empirical else 10 ** (a + b * level)
        r, g, bl = COLOUR_SCALE[level - 1]
        rows.append(
            {
                "level": level,
                "r": r,
                "g": g,
                "b": bl,
                "rate_mm_h": round(float(rate), 3),
                "gauge_mean_mm_h": round(float(stats.loc[level, "gauge_mean_mm_h"]), 3),
                "n_pairs": int(stats.loc[level, "n_pairs"])
                if pd.notna(stats.loc[level, "n_pairs"])
                else 0,
                "method": "gauge mean" if empirical else "dBR fit",
            }
        )
    return pd.DataFrame(rows), float(a), float(b)


def agreement(pairs: pd.DataFrame, rates: np.ndarray) -> dict[str, float]:
    """5-minute and hourly agreement between radar-estimated and gauge rain."""
    df = pairs.assign(radar_mm_h=rates[pairs.level.to_numpy()])
    wet = df[(df.level > 0) | (df.gauge_mm > 0)]
    df["hour"] = df.timestamp.dt.floor("h")
    hourly = df.groupby(["station_id", "hour"])[["gauge_mm", "radar_mm_h"]].sum()
    hourly["radar_mm"] = hourly.radar_mm_h / 12
    hw = hourly[(hourly.gauge_mm > 0) | (hourly.radar_mm > 0)]
    heavy = hw[hw.gauge_mm >= 20]
    return {
        "corr_5min": float(np.corrcoef(wet.radar_mm_h, wet.gauge_mm_h)[0, 1]),
        "corr_hourly": float(np.corrcoef(hw.radar_mm, hw.gauge_mm)[0, 1]),
        "total_ratio": float(hw.radar_mm.sum() / hw.gauge_mm.sum()),
        "heavy_hours": float(len(heavy)),
        "heavy_ratio": float(heavy.radar_mm.sum() / heavy.gauge_mm.sum()),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radar-dir", default=str(settings.root_dir / "data" / "raw" / "radar"))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--holdout-from", help="calibrate before this date, report agreement after it"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    radar = radar_at_gauges(Path(args.radar_dir), start, end)
    gauges = read_rainfall(datetime.combine(start, time.min), datetime.combine(end, time(23, 55)))
    gauges = gauges.dropna(subset=["rainfall_mm"]).rename(columns={"rainfall_mm": "gauge_mm"})
    pairs = radar.merge(gauges, on=["timestamp", "station_id"])
    pairs["gauge_mm_h"] = pairs.gauge_mm * 12
    logger.info("%d radar-gauge pairs", len(pairs))

    if args.holdout_from:
        cut = pd.Timestamp(args.holdout_from, tz=settings.tzinfo)
        table, _, _ = fit_scale(pairs[pairs.timestamp < cut])
        rates = np.concatenate([[0.0], table.rate_mm_h.to_numpy()])
        print("holdout agreement:", agreement(pairs[pairs.timestamp >= cut], rates))

    table, a, b = fit_scale(pairs)
    rates = np.concatenate([[0.0], table.rate_mm_h.to_numpy()])
    print(f"fit: log10(R) = {a:.4f} + {b:.5f}·level ({10 * b:.2f} dB per level)")
    print("in-sample agreement:", agreement(pairs, rates))
    SCALE_FILE.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(SCALE_FILE, index=False)
    print(f"wrote {SCALE_FILE}")


if __name__ == "__main__":
    main()
