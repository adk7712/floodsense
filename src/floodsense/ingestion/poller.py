"""
FloodSense - NEA 5-minute rainfall client for data.gov.sg.

Fetches station rainfall from the data.gov.sg real-time API (v2, with the legacy v1 endpoint as a
fallback), normalises both response shapes into ``RainfallSnapshot`` objects, and stages raw
payloads into the landing volume for the Lakeflow pipeline.

The client never fabricates data: when the API is unreachable or rate-limited it raises
``LiveFeedUnavailable`` and the caller decides what to show.
"""

import json
import logging
import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from floodsense.common.config import settings
from floodsense.common.schemas import RainfallReading, RainfallSnapshot, StationMetadata
from floodsense.common.timeutil import to_sgt

logger = logging.getLogger("FloodSense.Poller")


class LiveFeedUnavailable(RuntimeError):
    """The rainfall API could not be reached, refused the request, or returned no readings."""


def _parse_stations(raw_stations: list[dict[str, Any]]) -> dict[str, StationMetadata]:
    stations: dict[str, StationMetadata] = {}
    for s in raw_stations:
        loc = s.get("location") or {}
        try:
            stations[s["id"]] = StationMetadata(
                station_id=s["id"],
                name=s.get("name") or s["id"],
                latitude=loc["latitude"],
                longitude=loc["longitude"],
            )
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping station with unusable metadata %s: %s", s.get("id"), exc)
    return stations


def _parse_readings(timestamp: datetime, raw: list[tuple[str, Any]]) -> dict[str, float]:
    readings: dict[str, float] = {}
    for station_id, value in raw:
        if value is None:
            continue
        try:
            reading = RainfallReading(
                station_id=station_id, timestamp=timestamp, rainfall_mm=float(value)
            )
        except ValueError as exc:
            logger.warning(
                "Dropping invalid reading station=%s value=%s: %s", station_id, value, exc
            )
            continue
        readings[station_id] = reading.rainfall_mm
    return readings


def parse_rainfall_payload(payload: dict[str, Any]) -> list[RainfallSnapshot]:
    """
    Normalise a data.gov.sg rainfall response into chronologically ordered snapshots.

    Accepts both the v2 shape (``data.stations`` / ``data.readings[].data[].stationId``) and the
    legacy v1 shape (``metadata.stations`` / ``items[].readings[].station_id``).
    """
    data = payload.get("data")
    if isinstance(data, dict) and "readings" in data:
        stations = _parse_stations(data.get("stations") or [])
        blocks = [
            (b["timestamp"], [(r.get("stationId"), r.get("value")) for r in b.get("data") or []])
            for b in data.get("readings") or []
        ]
    elif "items" in payload:
        stations = _parse_stations((payload.get("metadata") or {}).get("stations") or [])
        blocks = [
            (
                b["timestamp"],
                [(r.get("station_id"), r.get("value")) for r in b.get("readings") or []],
            )
            for b in payload.get("items") or []
        ]
    else:
        raise ValueError("Unrecognised rainfall payload shape")

    snapshots = []
    for raw_ts, raw_readings in blocks:
        ts = to_sgt(datetime.fromisoformat(raw_ts.replace("Z", "+00:00")))
        snapshots.append(
            RainfallSnapshot(
                timestamp=ts, readings=_parse_readings(ts, raw_readings), stations=stations
            )
        )
    return sorted(snapshots, key=lambda s: s.timestamp)


class NEAPoller:
    """Client for NEA 5-minute station rainfall on data.gov.sg."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_sec: float | None = None,
        max_attempts: int | None = None,
        landing_dir: str | Path | None = None,
    ):
        self.api_key = api_key if api_key is not None else settings.data_gov_api_key
        self.timeout_sec = timeout_sec if timeout_sec is not None else settings.api_timeout_sec
        self.max_attempts = max_attempts if max_attempts is not None else settings.api_max_attempts
        self.landing_dir = Path(landing_dir) if landing_dir is not None else settings.landing_dir

    # -- HTTP -------------------------------------------------------------------------------

    def _get_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        last_error = "no attempts made"
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=self.timeout_sec)
            except requests.RequestException as exc:
                last_error = str(exc)
            else:
                if resp.status_code == 200:
                    body = resp.json()
                    # v2 reports errors in-band via a non-zero "code"; v1 has no such field.
                    if body.get("code", 0) == 0:
                        return body
                    last_error = f"API error {body.get('code')}: {body.get('errorMsg')}"
                else:
                    last_error = f"HTTP {resp.status_code}"
            logger.warning(
                "Rainfall API attempt %d/%d failed: %s", attempt, self.max_attempts, last_error
            )
            if attempt < self.max_attempts:
                time.sleep(settings.api_backoff_sec * attempt)
        raise LiveFeedUnavailable(f"{url}: {last_error}")

    def _get_payload(self, params: dict[str, str], legacy_params: dict[str, str]) -> dict[str, Any]:
        """Try the v2 endpoint, then the legacy v1 endpoint."""
        try:
            return self._get_json(settings.nea_api_primary, params)
        except LiveFeedUnavailable as primary_error:
            logger.warning(
                "Primary rainfall endpoint unavailable, trying legacy: %s", primary_error
            )
            try:
                return self._get_json(settings.nea_api_fallback, legacy_params)
            except LiveFeedUnavailable as fallback_error:
                raise LiveFeedUnavailable(f"{primary_error}; {fallback_error}") from fallback_error

    # -- public API -------------------------------------------------------------------------

    def fetch_latest(self) -> RainfallSnapshot:
        """Return the most recent 5-minute snapshot. Raises ``LiveFeedUnavailable``."""
        snapshots = parse_rainfall_payload(self._get_payload({}, {}))
        if not snapshots or not snapshots[-1].readings:
            raise LiveFeedUnavailable("Rainfall API returned no readings")
        return snapshots[-1]

    def _iter_day_pages(self, day: date) -> Iterator[list[RainfallSnapshot]]:
        """Yield one day's snapshots page by page, newest page first (v2 pagination)."""
        token: str | None = None
        while True:
            params = {"date": day.isoformat()}
            if token:
                params["paginationToken"] = token
            payload = self._get_json(settings.nea_api_primary, params)
            yield parse_rainfall_payload(payload)
            token = (payload.get("data") or {}).get("paginationToken")
            if not token:
                return
            time.sleep(settings.api_page_delay_sec)

    def fetch_range(self, start: datetime, end: datetime) -> list[RainfallSnapshot]:
        """All snapshots with ``start <= timestamp <= end`` (inclusive), oldest first."""
        start, end = to_sgt(start), to_sgt(end)
        collected: dict[datetime, RainfallSnapshot] = {}
        day = end.date()
        while day >= start.date():
            for page in self._iter_day_pages(day):
                collected.update({s.timestamp: s for s in page if start <= s.timestamp <= end})
                if page and page[0].timestamp < start:
                    break
            day -= timedelta(days=1)
        return [collected[t] for t in sorted(collected)]

    def fetch_history(self, hours: float, now: datetime | None = None) -> list[RainfallSnapshot]:
        """The last ``hours`` of snapshots up to ``now`` (default: current time in SGT)."""
        end = to_sgt(now) if now is not None else datetime.now(settings.tzinfo)
        snapshots = self.fetch_range(end - timedelta(hours=hours), end)
        if not snapshots:
            raise LiveFeedUnavailable("Rainfall API returned no readings for the requested window")
        return snapshots

    def stage_payload_to_volume(self, payload: dict[str, Any], filename: str | None = None) -> Path:
        """Persist a raw payload to the landing volume for Auto Loader to pick up."""
        self.landing_dir.mkdir(parents=True, exist_ok=True)
        if not filename:
            filename = f"rainfall_{datetime.now(settings.tzinfo):%Y%m%d_%H%M%S}.json"
        target_file = self.landing_dir / filename
        target_file.write_text(json.dumps(payload, indent=2))
        logger.info("Staged rainfall payload to landing volume: %s", target_file)
        return target_file


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    poller = NEAPoller()
    payload = poller._get_payload({}, {})
    snapshot = parse_rainfall_payload(payload)[-1]
    staged_path = poller.stage_payload_to_volume(payload)
    print(f"{snapshot.timestamp}: {len(snapshot.readings)} station readings -> {staged_path}")
