"""
FloodSense - PUB flash-flood alerts (data.gov.sg real-time API), a Track B2 dataset.

Each PUB alert is a CAP message: an ``Alert`` when a flash flood is observed at a location, and a
``Cancel`` (whose ``references`` names the alert) when it subsides. Each reading carries a circle
(lat, lon, radius km) and a plain-English description.

The API returns alerts for a given date, but past dates come back empty (30 Sep 2026, a day with a
sourced PUB flood alert, returned 720 empty records), so it can't supply training labels. FloodSense
shows the live alerts next to its own risk, and the Databricks poller archives them from now on.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from floodsense.common.config import settings
from floodsense.common.timeutil import to_sgt
from floodsense.ingestion.poller import LiveFeedUnavailable, NEAPoller

logger = logging.getLogger("FloodSense.FloodAlerts")

MAX_PAGES_PER_DAY = 50  # stops a repeating or endless paginationToken


@dataclass(frozen=True)
class FloodAlert:
    identifier: str
    msg_type: str  # "Alert" or "Cancel"
    references: str  # for a Cancel: names the alert it cancels
    issued_at: datetime
    headline: str
    description: str
    area_desc: str
    latitude: float | None
    longitude: float | None
    radius_km: float | None
    severity: str
    zone: str | None  # URA planning area containing the alert point


@lru_cache(maxsize=1)
def _zone_index() -> tuple[STRtree, list[str], list[Any]]:
    geo = json.loads(settings.zone_polygons_file.read_text())
    geoms = [shape(f["geometry"]) for f in geo["features"]]
    names = [str(f["properties"]["name"]).upper() for f in geo["features"]]
    return STRtree(geoms), names, geoms


def zone_for_point(latitude: float, longitude: float) -> str | None:
    """The URA planning area containing a point, or None (e.g. at sea)."""
    tree, names, geoms = _zone_index()
    pt = Point(longitude, latitude)
    for i in tree.query(pt):
        if geoms[int(i)].covers(pt):
            return names[int(i)]
    return None


def _record_time(record: Any) -> datetime | None:
    """A record's SGT ``datetime`` (naive read as SGT), or None (with a warning) when it is unusable."""
    try:
        when = datetime.fromisoformat(record["datetime"])
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Skipping flood-alert record with no usable datetime: %s", exc)
        return None
    return to_sgt(when)  # a naive time is read as SGT, as everywhere else


def parse_flood_alerts(payload: dict[str, Any]) -> list[FloodAlert]:
    """All alert readings in an API payload, oldest first. Empty observations are skipped, as are
    records that are malformed (logged)."""
    alerts = []
    for record in (payload.get("data") or {}).get("records") or []:
        issued_at = _record_time(record)
        if issued_at is None:
            continue
        item = record.get("item") or {}
        for reading in item.get("readings") or []:
            try:
                circle = (reading.get("area") or {}).get("circle") or []
                lat, lon, radius = (
                    (float(circle[0]), float(circle[1]), float(circle[2]))
                    if len(circle) >= 3
                    else (None, None, None)
                )
                alerts.append(
                    FloodAlert(
                        identifier=str(item.get("identifier", "")),
                        msg_type=str(item.get("msgType", "")),
                        references=str(item.get("references", "")),
                        issued_at=issued_at,
                        headline=str(reading.get("headline", "")),
                        description=str(reading.get("description", "")),
                        area_desc=str((reading.get("area") or {}).get("areaDesc", "")),
                        latitude=lat,
                        longitude=lon,
                        radius_km=radius,
                        severity=str(reading.get("severity", "")),
                        zone=(
                            zone_for_point(lat, lon)
                            if lat is not None and lon is not None
                            else None
                        ),
                    )
                )
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed flood-alert reading: %s", exc)
    return sorted(alerts, key=lambda a: a.issued_at)


def referenced_identifiers(references: str) -> set[str]:
    """The identifiers in a CAP ``references`` field: whitespace-separated "sender,identifier,sent"
    triples (some feeds put a space after each comma, so those are closed up first)."""
    identifiers = set()
    for triple in re.sub(r"\s*,\s*", ",", references.strip()).split():
        parts = triple.split(",")
        if len(parts) >= 2 and parts[1]:
            identifiers.add(parts[1])
    return identifiers


def active_alerts(alerts: list[FloodAlert]) -> list[FloodAlert]:
    """Alerts with no later Cancel referencing them (exact identifier match; a Cancel never
    cancels an alert sent after it)."""
    cancels = [
        (c.issued_at, referenced_identifiers(c.references))
        for c in alerts
        if c.msg_type == "Cancel"
    ]
    return [
        a
        for a in alerts
        if a.msg_type == "Alert"
        and not (
            a.identifier
            and any(a.issued_at <= when and a.identifier in ids for when, ids in cancels)
        )
    ]


def fetch_flood_alert_payloads(
    day: date | None = None, poller: NEAPoller | None = None, since: datetime | None = None
) -> list[dict[str, Any]]:
    """The API's pages, newest first. Without ``since``: ``day`` only (default: today, SGT). With
    ``since``: every date from ``since`` to ``day``, so a window that crosses midnight also gets the
    previous evening; each date stops once a page reaches back past ``since``."""
    poller = poller or NEAPoller()
    day = day or datetime.now(settings.tzinfo).date()
    if since is not None:
        since = to_sgt(since)  # naive is taken as SGT, so comparisons with record times can't raise
    first_day = since.date() if since is not None else day
    pages: list[dict[str, Any]] = []
    # NB: this file records that past dates come back empty, so whether the previous date's query
    # returns the late-evening alerts is unverified live.
    last_day = day
    while day >= first_day:
        token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(MAX_PAGES_PER_DAY):
            params = {"date": day.isoformat()}
            if token:
                params["paginationToken"] = token
            try:
                payload = poller._get_json(settings.pub_flood_alerts_url, params)
            except LiveFeedUnavailable as exc:
                if day == last_day:
                    raise
                # An earlier date may 404 (the archive job treats that as "no data"): keep the rest.
                logger.warning("Flood-alert query for %s failed, continuing: %s", day, exc)
                break
            pages.append(payload)
            records = (payload.get("data") or {}).get("records") or []
            token = (payload.get("data") or {}).get("paginationToken")
            times = [t for t in (_record_time(r) for r in records) if t is not None]
            oldest = min(times, default=None)
            if not token or token in seen_tokens:
                break
            seen_tokens.add(token)
            if since is not None and oldest is not None and oldest < since:
                break
        else:
            logger.warning(
                "Flood-alert pagination for %s hit the %d-page cap", day, MAX_PAGES_PER_DAY
            )
        day -= timedelta(days=1)
    return pages


def fetch_flood_alerts(
    day: date | None = None, poller: NEAPoller | None = None, since: datetime | None = None
) -> list[FloodAlert]:
    """Alerts issued on ``day`` (from ``since``, when given), oldest first."""
    if since is not None:
        since = to_sgt(since)
    pages = fetch_flood_alert_payloads(day, poller, since)
    alerts = [a for p in pages for a in parse_flood_alerts(p)]
    if since is not None:
        alerts = [a for a in alerts if a.issued_at >= since]
    return sorted(alerts, key=lambda a: a.issued_at)
