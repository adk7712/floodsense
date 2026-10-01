"""
FloodSense - Sourced flood events (training labels).

Events live in ``settings.flood_events_file`` (``data/reference/flood_events.csv``, committed):

    event_id            unique, e.g. "2021-04-17-bukit-timah-dunearn"
    timestamp_start     ISO 8601 with offset, e.g. 2021-04-17T14:15:00+08:00 (when flooding began)
    timestamp_end       same format, or empty if unknown
    time_precision      exact | approx_15min | approx_hour | day_only  (how precise the start is)
    location_raw        place as written in the source
    ura_planning_area   one of the 55 keys in URA_PLANNING_AREAS (upper case)
    severity            Minor | Moderate | Severe
    cause               rain | rain_tide | other
    source_url          http(s) URL of the article / PUB post the event was taken from
    source_name         e.g. "PUB press release", "The Straits Times", "CNA"
    evidence_quote      a sentence copied from the source that names the place and the time
    verified_by         who opened source_url and confirmed the row; empty = not yet checked
    notes               optional

One row per (event, planning area): a storm that flooded two planning areas is two rows.
Every row must have a working ``source_url``; events that cannot be sourced are dropped, not kept
with a placeholder. Rows nobody has checked yet (empty ``verified_by``) stay in the file as
candidates but are not used for training unless ``include_unverified=True``.
"""

import logging
from pathlib import Path

import pandas as pd

from floodsense.common.config import settings
from floodsense.common.schemas import FloodEvent
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

logger = logging.getLogger("FloodSense.FloodEvents")

EVENT_COLUMNS = [
    "event_id",
    "timestamp_start",
    "timestamp_end",
    "time_precision",
    "location_raw",
    "ura_planning_area",
    "severity",
    "cause",
    "source_url",
    "source_name",
    "evidence_quote",
    "verified_by",
    "notes",
]
RECORD_START = pd.Timestamp("2017-01-01T00:00:00+08:00")


def read_events_csv(path: Path | None = None) -> pd.DataFrame:
    """The raw CSV as strings (empty cells are ``""``), with the column set checked."""
    path = path or settings.flood_events_file
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != EVENT_COLUMNS:
        raise ValueError(f"{path.name}: columns must be {EVENT_COLUMNS}, got {list(df.columns)}")
    return df


def _row_problems(row: pd.Series) -> list[str]:
    problems = []
    if not row["event_id"].strip():
        problems.append("empty event_id")
    if row["ura_planning_area"] not in URA_PLANNING_AREAS:
        problems.append(f"unknown planning area {row['ura_planning_area']!r}")
    if not row["source_url"].startswith(("http://", "https://")) or " " in row["source_url"]:
        problems.append("source_url is not an http(s) URL")
    if not row["evidence_quote"].strip():
        problems.append("no evidence_quote")
    for col in ("timestamp_start", "timestamp_end"):
        if row[col] and not row[col].endswith("+08:00"):
            problems.append(f"{col} must carry the +08:00 offset")
    if (
        row["timestamp_start"].endswith("+08:00")
        and pd.Timestamp(row["timestamp_start"]) < RECORD_START
    ):
        problems.append("starts before the rainfall record (2017)")
    return problems


def load_flood_events(
    path: Path | None = None, include_unverified: bool = False
) -> list[FloodEvent]:
    """
    Parse and validate ``flood_events.csv`` into ``FloodEvent`` objects (tz-aware, SGT).

    Raises ``ValueError`` listing every invalid row. Rows with an empty ``verified_by`` are left
    out (and counted in the log) unless ``include_unverified`` is set.
    """
    df = read_events_csv(path)
    problems = [f"{r.event_id or i}: {p}" for i, r in df.iterrows() for p in _row_problems(r)]
    dupes = df[df.duplicated(["event_id", "ura_planning_area"], keep=False)]["event_id"]
    problems += [f"{e}: duplicate (event_id, ura_planning_area)" for e in sorted(set(dupes))]
    if problems:
        raise ValueError("invalid flood events:\n  " + "\n  ".join(problems))

    unverified = df["verified_by"].str.strip() == ""
    if unverified.any() and not include_unverified:
        logger.warning(
            "%d of %d flood events not yet verified; left out", unverified.sum(), len(df)
        )
        df = df[~unverified]
    return [
        FloodEvent(
            event_id=r.event_id,
            timestamp_start=pd.Timestamp(r.timestamp_start).to_pydatetime(),
            timestamp_end=pd.Timestamp(r.timestamp_end).to_pydatetime()
            if r.timestamp_end
            else None,
            time_precision=r.time_precision,
            location_raw=r.location_raw,
            ura_planning_area=r.ura_planning_area,
            severity=r.severity,
            cause=r.cause,
            source_url=r.source_url,
            source_name=r.source_name,
            evidence_quote=r.evidence_quote,
            verified_by=r.verified_by,
            notes=r.notes,
        )
        for r in df.itertuples(index=False)
    ]
