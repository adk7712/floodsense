"""
FloodSense - SYNTHETIC multi-year training dataset (placeholder).

``generate_historical_dataset`` invents zone rainfall and gives known flood days heavier storms, so
models trained on it learn the generator, not Singapore. It is kept only until training moves to
the real NEA gauge record; do not report metrics from it as real-world skill.

Real historical storm replays live in ``floodsense.data.replay``.
"""

import logging
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from floodsense.data.flood_events import load_flood_events
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

logger = logging.getLogger("FloodSense.DataLoader")


def generate_historical_dataset(
    start_year: int = 2017, end_year: int = 2026, num_storm_days_per_year: int = 35
) -> pd.DataFrame:
    """
    Generates multi-year historical training dataset anchored on known flood dates.
    Applies zero-rain pruning to maintain efficiency (>85% data reduction).
    """
    records = []
    np.random.seed(42)

    known_events = load_flood_events()
    known_flood_dates = {ev.timestamp_start.date() for ev in known_events}

    cur_date = datetime(start_year, 1, 1).date()
    end_date = datetime(end_year, 9, 30).date()

    while cur_date <= end_date:
        is_known_flood = cur_date in known_flood_dates
        is_storm_day = is_known_flood or (np.random.rand() < (num_storm_days_per_year / 365.0))

        if is_storm_day:
            storm_center_zone = np.random.choice(list(URA_PLANNING_AREAS.keys()))
            center_lat = URA_PLANNING_AREAS[storm_center_zone]["lat"]
            center_lon = URA_PLANNING_AREAS[storm_center_zone]["lon"]

            if is_known_flood:
                for ev in known_events:
                    if ev.timestamp_start.date() == cur_date:
                        center_lat = URA_PLANNING_AREAS[ev.ura_planning_area]["lat"]
                        center_lon = URA_PLANNING_AREAS[ev.ura_planning_area]["lon"]
                        break

            base_dt = datetime.combine(cur_date, datetime.min.time()) + timedelta(hours=13)
            peak_step = 25 + np.random.randint(-5, 10)
            max_intensity = 32.0 if is_known_flood else np.random.uniform(8.0, 22.0)

            for step in range(72):
                ts = base_dt + timedelta(minutes=step * 5)
                time_decay = math.exp(-((step - peak_step) ** 2) / 30.0)

                for zone_name, zmeta in URA_PLANNING_AREAS.items():
                    d = math.sqrt(
                        (zmeta["lat"] - center_lat) ** 2 + (zmeta["lon"] - center_lon) ** 2
                    )
                    spatial_factor = max(0.0, 1.0 - (d / 0.10))
                    rain = round(
                        max_intensity * time_decay * spatial_factor
                        + np.random.uniform(0, 0.2) * (1 if spatial_factor > 0 else 0),
                        2,
                    )

                    if rain > 0.0 or step % 12 == 0:
                        records.append(
                            {
                                "ura_planning_area": zone_name,
                                "timestamp": ts,
                                "rainfall_mm": rain,
                            }
                        )

        cur_date += timedelta(days=1)

    return pd.DataFrame(records)
