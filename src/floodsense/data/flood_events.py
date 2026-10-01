"""
FloodSense - Sourced flood events (training labels).

PHASE 3 DELIVERABLE (c) - implementation.
Parses data/reference/flood_events.csv into validated FloodEvent objects.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd

from floodsense.common.config import settings
from floodsense.common.schemas import FloodEvent
from floodsense.common.timeutil import to_sgt


def load_flood_events(path: Path | None = None) -> list[FloodEvent]:
    """Parse and validate ``flood_events.csv`` into ``FloodEvent`` objects (tz-aware, SGT)."""
    file_path = path or settings.flood_events_file
    if not file_path.exists():
        raise FileNotFoundError(f"Flood events file missing: {file_path}")

    df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
    events: list[FloodEvent] = []

    for _, row in df.iterrows():
        start_ts = to_sgt(datetime.fromisoformat(row["timestamp_start"]))
        end_ts = (
            to_sgt(datetime.fromisoformat(row["timestamp_end"]))
            if row["timestamp_end"].strip()
            else None
        )

        event = FloodEvent(
            event_id=row["event_id"].strip(),
            timestamp_start=start_ts,
            timestamp_end=end_ts,
            time_precision=row["time_precision"].strip(),
            location_raw=row["location_raw"].strip(),
            ura_planning_area=row["ura_planning_area"].strip().upper(),
            severity=row["severity"].strip(),
            cause=row["cause"].strip(),
            source_url=row["source_url"].strip(),
            source_name=row["source_name"].strip(),
            notes=row["notes"].strip(),
            geocoding_confidence=1.0,
        )
        events.append(event)

    return events
