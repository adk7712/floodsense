"""
FloodSense - Historical storm replay files.

A replay file holds real NEA 5-minute station readings for a storm, the station metadata that was
valid at the time, and a warm-up period before the displayed window so that rolling and 72-hour
wet-ground features are fully formed when the replay starts.

File layout (JSON, column-oriented to stay small):
    {
      "event_name", "source", "retrieved_at",
      "display_start", "display_end",              # ISO 8601 with +08:00
      "stations": [{"id", "name", "latitude", "longitude"}],
      "timestamps": [...],                          # contiguous 5-minute steps
      "readings": {"S77": [mm or null, ...], ...}   # aligned with "timestamps"
    }
"""

import argparse
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from floodsense.common.config import settings
from floodsense.common.schemas import RainfallSnapshot, StationMetadata
from floodsense.common.timeutil import to_sgt

logger = logging.getLogger("FloodSense.Replay")


@dataclass(frozen=True)
class Replay:
    event_name: str
    source: str
    stations: dict[str, StationMetadata]
    snapshots: list[RainfallSnapshot]  # warm-up + display window, oldest first
    display_start: datetime
    display_end: datetime

    @property
    def display_timestamps(self) -> list[datetime]:
        return [
            s.timestamp
            for s in self.snapshots
            if self.display_start <= s.timestamp <= self.display_end
        ]


def load_replay(path: str | Path) -> Replay:
    raw = json.loads(Path(path).read_text())
    stations = {
        s["id"]: StationMetadata(
            station_id=s["id"], name=s["name"], latitude=s["latitude"], longitude=s["longitude"]
        )
        for s in raw["stations"]
    }
    snapshots = []
    for i, ts_raw in enumerate(raw["timestamps"]):
        readings = {
            sid: values[i] for sid, values in raw["readings"].items() if values[i] is not None
        }
        snapshots.append(
            RainfallSnapshot(
                timestamp=to_sgt(datetime.fromisoformat(ts_raw)),
                readings=readings,
                stations=stations,
            )
        )
    return Replay(
        event_name=raw["event_name"],
        source=raw["source"],
        stations=stations,
        snapshots=snapshots,
        display_start=to_sgt(datetime.fromisoformat(raw["display_start"])),
        display_end=to_sgt(datetime.fromisoformat(raw["display_end"])),
    )


def save_replay(
    path: str | Path,
    *,
    event_name: str,
    source: str,
    snapshots: Sequence[RainfallSnapshot],
    display_start: datetime,
    display_end: datetime,
) -> Path:
    """Write snapshots to a replay file. Station metadata is merged across snapshots, latest wins."""
    snapshots = sorted(snapshots, key=lambda s: s.timestamp)
    stations: dict[str, StationMetadata] = {}
    for snap in snapshots:
        stations.update(snap.stations)
    reporting = sorted({sid for s in snapshots for sid in s.readings})
    missing = [sid for sid in reporting if sid not in stations]
    if missing:
        raise ValueError(f"Readings from stations without metadata: {missing}")

    payload = {
        "event_name": event_name,
        "source": source,
        "retrieved_at": datetime.now(settings.tzinfo).isoformat(timespec="seconds"),
        "display_start": to_sgt(display_start).isoformat(),
        "display_end": to_sgt(display_end).isoformat(),
        "stations": [
            {"id": sid, "name": s.name, "latitude": s.latitude, "longitude": s.longitude}
            for sid, s in sorted(stations.items())
            if sid in reporting
        ],
        "timestamps": [s.timestamp.isoformat() for s in snapshots],
        "readings": {sid: [s.readings.get(sid) for s in snapshots] for sid in reporting},
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    return out


def build_replay(
    out_path: str | Path,
    *,
    event_name: str,
    display_start: datetime,
    display_end: datetime,
    warmup_hours: float = 72.0,
) -> Path:
    """Download real readings for a storm window (plus warm-up) and save a replay file."""
    from floodsense.ingestion.poller import NEAPoller

    display_start, display_end = to_sgt(display_start), to_sgt(display_end)
    snapshots = NEAPoller().fetch_range(display_start - timedelta(hours=warmup_hours), display_end)
    logger.info("Fetched %d snapshots for %s", len(snapshots), event_name)
    return save_replay(
        out_path,
        event_name=event_name,
        source="NEA 5-minute station rainfall via data.gov.sg real-time API (v2)",
        snapshots=snapshots,
        display_start=display_start,
        display_end=display_end,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="Build a FloodSense storm replay file.")
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--start", required=True, help="Display start, ISO 8601 (SGT if naive)")
    parser.add_argument("--end", required=True, help="Display end, ISO 8601 (SGT if naive)")
    parser.add_argument("--warmup-hours", type=float, default=72.0)
    parser.add_argument("--out", type=Path, default=settings.replay_file)
    args = parser.parse_args()
    path = build_replay(
        args.out,
        event_name=args.event_name,
        display_start=datetime.fromisoformat(args.start),
        display_end=datetime.fromisoformat(args.end),
        warmup_hours=args.warmup_hours,
    )
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
