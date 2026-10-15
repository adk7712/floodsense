"""
FloodSense - Archive PUB flash-flood alerts (data.gov.sg ``v2/real-time/api/weather/flood-alerts``).

The API keeps no history: past dates come back as records with no readings (30 Sep 2026, a day with
a sourced PUB flood alert, returned 720 empty records), so an alert can only be saved while it is
recent. The API publishes a record about every 2 minutes, 25 to a page, newest first. This job runs
every 15 minutes, reads back ``--lookback-minutes`` (default 20, so consecutive runs overlap) and
lands each page that holds at least one alert, unchanged, in the pipeline's flood-alert landing
folder. ``pub_flood_alerts_silver`` deduplicates on (identifier, msg_type), so an alert seen by
several runs, or by the live poller too, is kept once. Pages with no alert are not written: the
job's run history and its log show each check.

    python archive_flood_alerts.py                       # Databricks job, every 15 min
    python databricks/jobs/archive_flood_alerts.py --out-dir <dir>/alerts --state-dir <dir>/state

Self-contained (requests only) so the Databricks job needs no project wheel.
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("floodsense.archive_flood_alerts")

API = "https://api-open.data.gov.sg/v2/real-time/api/weather/flood-alerts"
SGT = timezone(timedelta(hours=8))
PAGE_DELAY_SEC = 0.5


def _api_key(scope: str, key: str) -> str | None:
    """The data.gov.sg key: a Databricks secret when on Databricks, else the environment/.env."""
    try:
        from databricks.sdk.runtime import dbutils

        return str(dbutils.secrets.get(scope=scope, key=key))
    except Exception:
        pass
    if os.environ.get("FLOODSENSE_DATA_GOV_API_KEY"):
        return os.environ["FLOODSENSE_DATA_GOV_API_KEY"]
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("FLOODSENSE_DATA_GOV_API_KEY="):
                return line.split("=", 1)[1].strip()
    log.warning("No data.gov.sg API key found; using the anonymous (rate-limited) API")
    return None


def _get(session: requests.Session, params: dict[str, str]) -> dict[str, Any] | None:
    """One page, or None when the API has nothing for that date (404)."""
    for attempt in range(6):
        resp = session.get(API, params=params, timeout=30)
        if resp.status_code == 404:
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(3 * (attempt + 1))
            continue
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        if body.get("code", 0) != 0:  # v2 reports errors in-band
            raise RuntimeError(f"{API} {params}: API error {body.get('code')}: {body.get('errorMsg')}")
        return body
    raise RuntimeError(f"{API} {params}: still failing after 6 attempts")


def alert_count(payload: dict[str, Any]) -> int:
    """Alert readings on a page (records with an empty ``readings`` list mean no alert)."""
    return sum(
        len((r.get("item") or {}).get("readings") or [])
        for r in (payload.get("data") or {}).get("records") or []
    )


def recent_pages(session: requests.Session, now: datetime, since: datetime) -> list[dict[str, Any]]:
    """The pages covering ``since`` to ``now``, newest first (yesterday's too, just after midnight)."""
    pages: list[dict[str, Any]] = []
    day = now.date()
    while day >= since.date():
        token: str | None = None
        while True:
            params = {"date": day.isoformat()}
            if token:
                params["paginationToken"] = token
            payload = _get(session, params)
            if payload is None:
                break
            pages.append(payload)
            records = (payload.get("data") or {}).get("records") or []
            oldest = min((datetime.fromisoformat(r["datetime"]) for r in records), default=None)
            token = (payload.get("data") or {}).get("paginationToken")
            if not token or oldest is None or oldest < since:
                break
            time.sleep(PAGE_DELAY_SEC)
        day -= timedelta(days=1)
    return pages


def land(pages: list[dict[str, Any]], now: datetime, out_dir: Path, state_dir: Path) -> int:
    """Write the pages that hold an alert; returns the number of alert readings landed."""
    landed = 0
    for i, payload in enumerate(pages):
        n = alert_count(payload)
        if not n:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        name = f"flood_alerts_{now:%Y%m%d}_p{i:02d}_fetched_{now:%Y%m%d_%H%M%S}.json"
        tmp = state_dir / f"{name}.tmp"  # outside the Auto Loader path
        tmp.write_text(json.dumps(payload))
        tmp.replace(out_dir / name)  # Auto Loader only ever sees whole files
        landed += n
    return landed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="/Volumes/workspace/floodsense/landing/flood_alerts")
    parser.add_argument(
        "--state-dir", default="/Volumes/workspace/floodsense/landing/archive_state/flood_alerts"
    )
    parser.add_argument("--lookback-minutes", type=int, default=20)
    parser.add_argument("--secret-scope", default="floodsense")
    parser.add_argument("--secret-key", default="data_gov_api_key")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    now = datetime.now(SGT)
    since = now - timedelta(minutes=args.lookback_minutes)
    session = requests.Session()
    session.headers["User-Agent"] = "floodsense-flood-alert-archiver"
    if key := _api_key(args.secret_scope, args.secret_key):
        session.headers["x-api-key"] = key

    pages = recent_pages(session, now, since)
    if not pages:
        raise RuntimeError("The flood-alert API returned no pages")
    landed = land(pages, now, Path(args.out_dir), Path(args.state_dir))
    log.info(
        "Checked %d page(s) from %s; %d alert reading(s) landed in %s",
        len(pages),
        f"{since:%Y-%m-%d %H:%M} SGT",
        landed,
        args.out_dir,
    )


if __name__ == "__main__":
    # No SystemExit: Databricks runs the file under IPython, which reports any SystemExit as a failure.
    main()
