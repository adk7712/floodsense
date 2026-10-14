"""
FloodSense - Score the radar nowcast against what the gauges then measured (plan item A1.3/A1.4).

Truth: each zone's gauge-interpolated rain over the next 30 and 60 minutes (the feature store's
``rain_30m``/``rain_60m`` at t+30/t+60, the quantity the flood model uses). No flood labels are used,
so the 2024-2026 test set is untouched.

Forecasts, issued at every storm time step t in the radar archive:
- **nowcast:** radar rain advected by the estimated motion (``floodsense.radar.nowcast``)
- **radar persistence:** the current radar rain, held still
- **gauge persistence:** the last 30/60 minutes of gauge rain, repeated (what the current model
  effectively assumes)
- **radar observed:** the radar's own later frames (an upper bound for any radar-based forecast,
  limited by radar-vs-gauge differences)

Storm time steps: some zone has ≥ 5 mm in the truth window, or ≥ 5 mm/h on radar at t.
Scores: correlation, MAE and bias over zone × step pairs, and hit / false-alarm / CSI for the
model's tier thresholds (Moderate from 29.5 mm and High from 44.5 mm in 60 minutes).

    uv run --extra radar python -m floodsense.radar.evaluate_nowcast --start 2026-07-16 --end 2026-09-30
"""

import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.radar.decode import BoundingBox, decode_levels, rate_table, zone_index
from floodsense.radar.nowcast import accumulation_mm, extrapolate, motion_field

logger = logging.getLogger("FloodSense.NowcastEval")

STEP = pd.Timedelta(minutes=5)
THRESHOLDS_60 = {"Moderate (29.5 mm)": 29.5, "High (44.5 mm)": 44.5, "Rain (10 mm)": 10.0}
THRESHOLDS_30 = {"15 mm in 30 min": 15.0, "Rain (5 mm)": 5.0}


def _frames(
    radar_dir: Path, start: date, end: date
) -> tuple[dict[pd.Timestamp, np.ndarray], BoundingBox]:
    out: dict[pd.Timestamp, np.ndarray] = {}
    bbox = None
    day = start
    while day <= end:
        f = radar_dir / "70km" / f"date={day.isoformat()}" / "frames.parquet"
        day += timedelta(days=1)
        if not f.exists():
            continue
        frames = pd.read_parquet(f)
        bbox = bbox or BoundingBox.from_row(frames.iloc[0])
        for fr in frames.itertuples():
            out[pd.Timestamp(fr.timestamp_sgt)] = decode_levels(fr.png)
    if bbox is None:
        raise FileNotFoundError(f"no radar frames under {radar_dir}")
    return out, bbox


def _truth(start: date, end: date) -> pd.DataFrame:
    parts = []
    for month in sorted({(d.year, d.month) for d in pd.date_range(start, end)}):
        f = (
            settings.root_dir
            / "data"
            / "processed"
            / "features"
            / f"year={month[0]}"
            / f"month={month[1]:02d}.parquet"
        )
        if f.exists():
            parts.append(
                pd.read_parquet(
                    f, columns=["ura_planning_area", "timestamp", "rain_30m", "rain_60m"]
                )
            )
    if not parts:
        raise FileNotFoundError("feature store missing; run floodsense.features.build_features")
    t = pd.concat(parts)
    t["timestamp"] = pd.to_datetime(t["timestamp"])
    return t.pivot_table(
        index="timestamp", columns="ura_planning_area", values=["rain_30m", "rain_60m"]
    )


def _zone_mean(field: np.ndarray, idx: np.ndarray, n: int) -> np.ndarray:
    inside = idx >= 0
    sums = np.bincount(idx[inside], weights=field[inside], minlength=n)
    counts = np.bincount(idx[inside], minlength=n)
    return sums / np.maximum(counts, 1)


def run(radar_dir: Path, start: date, end: date) -> pd.DataFrame:
    frames, bbox = _frames(radar_dir, start, end)
    rates_of = rate_table()
    idx, names = zone_index(bbox, next(iter(frames.values())).shape)
    n = len(names)
    truth = _truth(start, end)
    rows = []
    for t in sorted(frames):
        past = [frames.get(t - 2 * STEP), frames.get(t - STEP), frames[t]]
        future_ok = all((t + k * STEP) in frames for k in range(1, 13))
        if (
            any(p is None for p in past)
            or (t + 12 * STEP) not in truth.index
            or (t + 6 * STEP) not in truth.index
        ):
            continue
        rate_now = rates_of[frames[t]]
        obs60 = truth.loc[t + 12 * STEP, "rain_60m"].reindex(names).to_numpy(dtype=float)
        radar_now_zone = _zone_mean(rate_now, idx, n)
        if np.nanmax(obs60) < 5 and radar_now_zone.max() < 5:
            continue  # not a storm step
        motion = motion_field([p for p in past if p is not None])
        fields = extrapolate(rate_now, motion, 12)
        nc30 = _zone_mean(accumulation_mm(fields[:6]), idx, n)
        nc60 = _zone_mean(accumulation_mm(fields), idx, n)
        if future_ok:
            later = [rates_of[frames[t + k * STEP]] for k in range(1, 13)]
            ro30 = _zone_mean(accumulation_mm(later[:6]), idx, n)
            ro60 = _zone_mean(accumulation_mm(later), idx, n)
        else:
            ro30 = ro60 = np.full(n, np.nan)
        rows.append(
            pd.DataFrame(
                {
                    "t": t,
                    "zone": names,
                    "obs30": truth.loc[t + 6 * STEP, "rain_30m"]
                    .reindex(names)
                    .to_numpy(dtype=float),
                    "obs60": obs60,
                    "nowcast30": nc30,
                    "nowcast60": nc60,
                    "radar_persist30": radar_now_zone * 0.5,
                    "radar_persist60": radar_now_zone,
                    "gauge_persist30": truth.loc[t, "rain_30m"].reindex(names).to_numpy(dtype=float)
                    if t in truth.index
                    else np.nan,
                    "gauge_persist60": truth.loc[t, "rain_60m"].reindex(names).to_numpy(dtype=float)
                    if t in truth.index
                    else np.nan,
                    "radar_observed30": ro30,
                    "radar_observed60": ro60,
                }
            )
        )
    return pd.concat(rows, ignore_index=True).dropna(subset=["obs30", "obs60"])


def scores(pairs: pd.DataFrame, lead: int) -> pd.DataFrame:
    obs = pairs[f"obs{lead}"]
    thresholds = THRESHOLDS_60 if lead == 60 else THRESHOLDS_30
    out = []
    for method in ["nowcast", "radar_persist", "gauge_persist", "radar_observed"]:
        f = pairs[f"{method}{lead}"]
        ok = f.notna() & obs.notna()
        row = {
            "lead_min": lead,
            "method": method,
            "pairs": int(ok.sum()),
            "corr": float(np.corrcoef(f[ok], obs[ok])[0, 1]),
            "mae_mm": float((f[ok] - obs[ok]).abs().mean()),
            "bias": float(f[ok].sum() / obs[ok].sum()),
        }
        for label, thr in thresholds.items():
            hit = ((f >= thr) & (obs >= thr))[ok].sum()
            miss = ((f < thr) & (obs >= thr))[ok].sum()
            fa = ((f >= thr) & (obs < thr))[ok].sum()
            row[f"{label}: POD"] = hit / max(hit + miss, 1)
            row[f"{label}: FAR"] = fa / max(hit + fa, 1)
            row[f"{label}: CSI"] = hit / max(hit + miss + fa, 1)
            row[f"{label}: events"] = int(hit + miss)
        out.append(row)
    return pd.DataFrame(out)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radar-dir", default=str(settings.root_dir / "data" / "raw" / "radar"))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--out", default=str(settings.root_dir / "data" / "processed" / "nowcast_eval.parquet")
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    pairs = run(Path(args.radar_dir), date.fromisoformat(args.start), date.fromisoformat(args.end))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pairs.to_parquet(args.out)
    print(f"{pairs.t.nunique()} storm time steps, {len(pairs)} zone x step pairs")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    for lead in (30, 60):
        print(scores(pairs, lead).round(3).T.to_string())


if __name__ == "__main__":
    main()
