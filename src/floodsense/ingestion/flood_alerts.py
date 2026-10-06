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
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from floodsense.common.config import settings
from floodsense.common.timeutil import to_sgt
from floodsense.ingestion.poller import NEAPoller

logger = logging.getLogger("FloodSense.FloodAlerts")


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


def parse_flood_alerts(payload: dict[str, Any]) -> list[FloodAlert]:
    """All alert readings in an API payload, oldest first. Empty observations are skipped."""
    alerts = []
    for record in (payload.get("data") or {}).get("records") or []:
        item = record.get("item") or {}
        for reading in item.get("readings") or []:
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
                    issued_at=to_sgt(datetime.fromisoformat(record["datetime"])),
                    headline=str(reading.get("headline", "")),
                    description=str(reading.get("description", "")),
                    area_desc=str((reading.get("area") or {}).get("areaDesc", "")),
                    latitude=lat,
                    longitude=lon,
                    radius_km=radius,
                    severity=str(reading.get("severity", "")),
                    zone=zone_for_point(lat, lon) if lat is not None and lon is not None else None,
                )
            )
    return sorted(alerts, key=lambda a: a.issued_at)


def active_alerts(alerts: list[FloodAlert]) -> list[FloodAlert]:
    """Alerts with no later Cancel referencing them."""
    cancelled = {
        a.identifier
        for a in alerts
        for c in alerts
        if c.msg_type == "Cancel" and a.identifier and a.identifier in c.references
    }
    return [a for a in alerts if a.msg_type == "Alert" and a.identifier not in cancelled]


def fetch_flood_alert_payloads(
    day: date | None = None, poller: NEAPoller | None = None, since: datetime | None = None
) -> list[dict[str, Any]]:
    """The API's pages for ``day`` (default: today, SGT), newest first; stops once a page reaches
    back past ``since``."""
    poller = poller or NEAPoller()
    day = day or datetime.now(settings.tzinfo).date()
    pages: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        params = {"date": day.isoformat()}
        if token:
            params["paginationToken"] = token
        payload = poller._get_json(settings.pub_flood_alerts_url, params)
        pages.append(payload)
        records = (payload.get("data") or {}).get("records") or []
        token = (payload.get("data") or {}).get("paginationToken")
        oldest = min((datetime.fromisoformat(r["datetime"]) for r in records), default=None)
        if not token or (since is not None and oldest is not None and oldest < since):
            return pages


def fetch_flood_alerts(
    day: date | None = None, poller: NEAPoller | None = None, since: datetime | None = None
) -> list[FloodAlert]:
    """Alerts issued on ``day`` (from ``since``, when given), oldest first."""
    pages = fetch_flood_alert_payloads(day, poller, since)
    alerts = [a for p in pages for a in parse_flood_alerts(p)]
    if since is not None:
        alerts = [a for a in alerts if a.issued_at >= since]
    return sorted(alerts, key=lambda a: a.issued_at)
