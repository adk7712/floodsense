"""
FloodSense - Historical Dataset & Replay Generator.
Synthesizes high-fidelity 9-year (2017-2026) rainfall and flood event time series
with zero-rain stream pruning optimization, and packages the 17 April 2021 Replay Demo Slice.
"""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Generator, List, Tuple
import numpy as np
import pandas as pd

from src.common.schemas import ZoneRainfall
from src.spatial.singapore_geo import URA_PLANNING_AREAS, NEA_WEATHER_STATIONS
from src.features.ground_truth_extractor import HISTORICAL_FLOOD_EVENTS_BENCHMARK


def generate_april_2021_replay_slice(output_path: str = "data/replay/april_2021_storm.json") -> Path:
    """
    Generates a high-resolution 5-minute replay slice for the 17 April 2021 Singapore flash flood event.
    Time range: 13:00 to 18:00 SGT (60 five-minute steps).
    Major impact zones: BUKIT TIMAH, JURONG EAST, JURONG WEST, BOON LAY, CLEMENTI.
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    start_time = datetime(2021, 4, 17, 13, 0, 0)
    steps = 60  # 5 hours at 5-min intervals
    replay_timeline = []

    # Epicenter of the intense convective cell in Western Singapore
    center_lat, center_lon = 1.332, 103.765  # Bukit Timah / Ulu Pandan catchment

    for step in range(steps):
        current_time = start_time + timedelta(minutes=step * 5)
        # Intensity curve: peaks around step 20-35 (14:40 to 15:55)
        t_peak = 26
        sigma = 8.0
        time_factor = math.exp(-((step - t_peak) ** 2) / (2 * (sigma ** 2)))
        peak_intensity = 28.0 * time_factor  # Max ~28mm in 5 min at peak

        station_readings = []
        for s_id, meta in NEA_WEATHER_STATIONS.items():
            dist = math.sqrt((meta["lat"] - center_lat) ** 2 + (meta["lon"] - center_lon) ** 2)
            # Distance decay from storm core
            spatial_factor = max(0.0, 1.0 - (dist / 0.12))
            rain = round(peak_intensity * spatial_factor + (0.5 * time_factor if spatial_factor > 0 else 0.0), 2)
            station_readings.append({
                "station_id": s_id,
                "value": rain
            })

        replay_timeline.append({
            "step_index": step,
            "timestamp": current_time.isoformat(),
            "event_phase": "Approaching Storm" if step < 15 else ("Peak Downpour & Inundation" if step < 40 else "Receding Floodwaters"),
            "readings": station_readings
        })

    with open(out_file, "w") as f:
        json.dump({
            "event_name": "17 April 2021 Western Singapore Flash Flood",
            "description": "Severe afternoon monsoon surge producing >170mm localized rainfall across Bukit Timah & Jurong catchments.",
            "total_steps": steps,
            "timeline": replay_timeline
        }, f, indent=2)

    return out_file


def generate_historical_dataset(
    start_year: int = 2017,
    end_year: int = 2026,
    num_storm_days_per_year: int = 40
) -> pd.DataFrame:
    """
    Generates a realistic multi-year historical dataset of active rain bursts and baseline weather.
    Applies zero-rain pruning to maintain efficiency.
    """
    records = []
    np.random.seed(42)

    # Collect dates from historical flood benchmarks to ensure exact coverage
    known_flood_dates = set()
    for ev in HISTORICAL_FLOOD_EVENTS_BENCHMARK:
        dt = datetime.fromisoformat(ev["timestamp_start"])
        known_flood_dates.add(dt.date())

    # Build sequence of dates
    cur_date = datetime(start_year, 1, 1).date()
    end_date = datetime(end_year, 9, 30).date()

    while cur_date <= end_date:
        is_known_flood = cur_date in known_flood_dates
        is_storm_day = is_known_flood or (np.random.rand() < (num_storm_days_per_year / 365.0))

        if is_storm_day:
            # 6-hour active convective window (e.g. 13:00 to 19:00)
            storm_center_zone = np.random.choice(list(URA_PLANNING_AREAS.keys()))
            center_lat = URA_PLANNING_AREAS[storm_center_zone]["lat"]
            center_lon = URA_PLANNING_AREAS[storm_center_zone]["lon"]

            if is_known_flood:
                # Find matching zone from benchmark
                for ev in HISTORICAL_FLOOD_EVENTS_BENCHMARK:
                    if datetime.fromisoformat(ev["timestamp_start"]).date() == cur_date:
                        center_lat = URA_PLANNING_AREAS[ev["ura_planning_area"]]["lat"]
                        center_lon = URA_PLANNING_AREAS[ev["ura_planning_area"]]["lon"]
                        break

            # 72 steps (6 hours)
            base_dt = datetime.combine(cur_date, datetime.min.time()) + timedelta(hours=13)
            peak_step = 25 + np.random.randint(-5, 10)
            max_intensity = 32.0 if is_known_flood else np.random.uniform(8.0, 22.0)

            for step in range(72):
                ts = base_dt + timedelta(minutes=step * 5)
                time_decay = math.exp(-((step - peak_step) ** 2) / 30.0)

                for zone_name, zmeta in URA_PLANNING_AREAS.items():
                    d = math.sqrt((zmeta["lat"] - center_lat) ** 2 + (zmeta["lon"] - center_lon) ** 2)
                    spatial_factor = max(0.0, 1.0 - (d / 0.10))
                    rain = round(max_intensity * time_decay * spatial_factor + np.random.uniform(0, 0.2) * (1 if spatial_factor > 0 else 0), 2)

                    if rain > 0.0 or step % 12 == 0:  # Prune zero rain except periodic sync
                        records.append({
                            "ura_planning_area": zone_name,
                            "timestamp": ts,
                            "rainfall_mm": rain
                        })

        cur_date += timedelta(days=1)

    df = pd.DataFrame(records)
    return df


if __name__ == "__main__":
    replay_path = generate_april_2021_replay_slice()
    print(f"Generated Replay Demo Slice: {replay_path}")
    hist_df = generate_historical_dataset(start_year=2017, end_year=2026, num_storm_days_per_year=30)
    print(f"Synthesized Historical Rainfall: {len(hist_df)} rows across 55 planning zones")
