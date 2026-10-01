"""
FloodSense - Sourced flood events (training labels).

PHASE 3 DELIVERABLE (c) - interface only; see docs/phase3-handoff.md and
tests/test_phase3_contract.py.

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
    source_name         e.g. "PUB press release", "The Straits Times", "PUB Telegram"
    notes               optional

One row per (event, planning area): a storm that flooded two planning areas is two rows.
Every row must have a working ``source_url``; events that cannot be sourced are dropped, not kept
with a placeholder.
"""

from pathlib import Path

from floodsense.common.schemas import FloodEvent


def load_flood_events(path: Path | None = None) -> list[FloodEvent]:
    """Parse and validate ``flood_events.csv`` into ``FloodEvent`` objects (tz-aware, SGT)."""
    raise NotImplementedError("Phase 3 deliverable (c): see docs/phase3-handoff.md")
