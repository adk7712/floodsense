"""
FloodSense - The pandas core that the Databricks (Lakeflow) pipeline calls.

Phase 5 runs ingestion and scoring on Databricks, but every step that turns readings into risk
lives HERE, in plain pandas, so training, the app, the backtest and the pipeline can't drift apart.
The Spark side only moves data: read landing files, call these functions (directly on a small
pandas frame, or through ``applyInPandas``), write tables. See docs/phase5-handoff.md.

    payload (API JSON)  --payloads_to_readings-->  readings + stations   (silver)
    readings + stations --score_window-------->    predictions           (gold)

CLI helpers for the handoff:

    python -m floodsense.serving.pipeline_core export-replay --out <dir>
        Write the 17 Apr 2021 replay as one API-shaped JSON file per 5-minute step, to drop into
        the landing volume, so replay runs through exactly the live path.
    python -m floodsense.serving.pipeline_core expected --out <file.csv>
        The predictions the pipeline must reproduce for that replay (the parity reference).
"""

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from floodsense.common.config import settings
from floodsense.common.schemas import RainfallSnapshot, StationMetadata
from floodsense.data.replay import load_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.ingestion.poller import parse_rainfall_payload
from floodsense.models.artifact import load_model
from floodsense.models.scoring import score_zone_features

SGT = "Asia/Singapore"

READING_COLUMNS = ["station_id", "timestamp", "rainfall_mm"]
STATION_COLUMNS = ["station_id", "name", "latitude", "longitude"]
# The gold table's schema. The parity test compares these columns.
PREDICTION_COLUMNS = [
    "ura_planning_area",
    "timestamp",
    "rain_5m",
    "rain_15m",
    "rain_30m",
    "rain_60m",
    "rain_120m",
    "rain_decay_72h",
    "reporting_stations",
    "flood_probability",
    "risk_tier",
]
# History a prediction needs: the 72-hour wet-ground feature. Score a window at least this long
# before the first timestamp you emit.
WARMUP = pd.Timedelta(hours=72)


def snapshot_to_payload(snap: RainfallSnapshot) -> dict[str, Any]:
    """One snapshot as a data.gov.sg v2 rainfall response (what the live poller lands)."""
    return {
        "code": 0,
        "errorMsg": "",
        "data": {
            "stations": [
                {
                    "id": s.station_id,
                    "deviceId": s.station_id,
                    "name": s.name,
                    "location": {"latitude": s.latitude, "longitude": s.longitude},
                }
                for s in snap.stations.values()
            ],
            "readings": [
                {
                    "timestamp": snap.timestamp.isoformat(),
                    "data": [{"stationId": k, "value": v} for k, v in snap.readings.items()],
                }
            ],
            "readingType": "TB1 Rainfall 5 Minute Total F",
            "readingUnit": "mm",
        },
    }


def payloads_to_readings(payloads: Iterable[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Parse API payloads (v1 or v2 shape) into long readings and a station table (silver).

    Uses the same parser as the live app (``parse_rainfall_payload``), including its validation:
    readings outside 0-100 mm are dropped and logged, never clipped. Duplicate (station, time)
    readings keep the last one seen. A station missing at a step stays missing; nothing is filled.
    """
    rows: list[tuple[str, pd.Timestamp, float]] = []
    stations: dict[str, StationMetadata] = {}
    for payload in payloads:
        for snap in parse_rainfall_payload(payload):
            ts = pd.Timestamp(snap.timestamp).tz_convert(SGT)
            rows.extend((sid, ts, float(mm)) for sid, mm in snap.readings.items())
            stations.update(snap.stations)
    readings = pd.DataFrame(rows, columns=READING_COLUMNS)
    if readings.empty:
        readings["timestamp"] = pd.Series(dtype=f"datetime64[ns, {SGT}]")
    readings = (
        readings.drop_duplicates(["station_id", "timestamp"], keep="last")
        .sort_values(["timestamp", "station_id"])
        .reset_index(drop=True)
    )
    station_table = pd.DataFrame(
        [(s.station_id, s.name, s.latitude, s.longitude) for s in stations.values()],
        columns=STATION_COLUMNS,
    )
    return readings, station_table


def _snapshots(readings: pd.DataFrame, stations: pd.DataFrame) -> list[RainfallSnapshot]:
    meta = {
        r.station_id: StationMetadata(
            station_id=r.station_id, name=r.name, latitude=r.latitude, longitude=r.longitude
        )
        for r in stations.itertuples(index=False)
    }
    ts = pd.to_datetime(readings["timestamp"])
    ts = ts.dt.tz_localize(SGT) if ts.dt.tz is None else ts.dt.tz_convert(SGT)
    frame = readings.assign(timestamp=ts)
    return [
        RainfallSnapshot.model_construct(
            timestamp=t.to_pydatetime(),
            readings=dict(
                zip(g["station_id"].astype(str), g["rainfall_mm"].astype(float), strict=True)
            ),
            stations=meta,
        )
        for t, g in frame.groupby("timestamp", sort=True)
    ]


def score_window(
    readings: pd.DataFrame,
    stations: pd.DataFrame,
    model: Any | None = None,
    emit_from: datetime | pd.Timestamp | None = None,
    emit_to: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Zone features and risk for every 5-minute step in ``readings`` (gold).

    ``readings`` must include at least ``WARMUP`` of history before ``emit_from`` for the
    wet-ground feature to be complete. Only rows with ``emit_from <= timestamp <= emit_to`` are
    returned. ``model`` defaults to the committed trained model (``load_model()``). Returns
    ``PREDICTION_COLUMNS``; probabilities are unrounded.
    """
    if readings.empty:
        raise ValueError("No readings to score")
    model = load_model() if model is None else model
    snaps = _snapshots(readings, stations)
    table = compute_zone_feature_table(snaps, snaps[0].stations)
    scored = score_zone_features(table, model)
    stamps = pd.to_datetime(scored["timestamp"])
    keep = pd.Series(True, index=scored.index)
    if emit_from is not None:
        keep &= stamps >= pd.Timestamp(emit_from).tz_convert(SGT)
    if emit_to is not None:
        keep &= stamps <= pd.Timestamp(emit_to).tz_convert(SGT)
    out = scored.loc[keep, PREDICTION_COLUMNS].copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    return out.sort_values(["timestamp", "ura_planning_area"]).reset_index(drop=True)


def replay_predictions(replay_path: Path | None = None, model: Any | None = None) -> pd.DataFrame:
    """The reference gold rows for a replay's display window, computed locally."""
    replay = load_replay(replay_path or settings.replay_file)
    readings, stations = payloads_to_readings(snapshot_to_payload(s) for s in replay.snapshots)
    return score_window(readings, stations, model, replay.display_start, replay.display_end)


def export_replay_payloads(out_dir: Path, replay_path: Path | None = None) -> list[Path]:
    """Write a replay as API-shaped JSON files, named like the live poller's landing files."""
    replay = load_replay(replay_path or settings.replay_file)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for snap in replay.snapshots:
        path = out_dir / f"rainfall_{snap.timestamp:%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(snapshot_to_payload(snap)))
        paths.append(path)
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("export-replay", help="replay -> API-shaped landing files")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("expected", help="reference predictions for the replay (CSV)")
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "export-replay":
        paths = export_replay_payloads(args.out)
        print(f"Wrote {len(paths)} files to {args.out}")
    else:
        df = replay_predictions()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False)
        print(f"Wrote {len(df)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
