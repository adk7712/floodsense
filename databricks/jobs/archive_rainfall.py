"""
FloodSense - Archive whole days of NEA rain-gauge readings (data.gov.sg ``v2/real-time/api/rainfall``).

The daily partner of ``archive_radar.py``. Each API page for a day is written unchanged to
``<out-dir>/YYYY-MM-DD/pNN.json``. By default that is a folder under the pipeline's rainfall landing
path, so Auto Loader takes the readings in on the pipeline's next update; this job doesn't run the
pipeline itself (it would cost compute every night). Silver deduplicates readings, so overlap with
the live poller does no harm.

Folders are named ``YYYY-MM-DD``, not ``date=YYYY-MM-DD``: under the Auto Loader path a ``key=value``
folder could be read as a partition column and change the bronze schema. Nothing but API pages goes
under that path: pages are written in ``--state-dir`` first and moved in whole, and a finished day's
marker (``YYYY-MM-DD.complete``) stays there. Marked days are skipped, so the job is safe to re-run
and a missed night is caught up on the next run.

    # daily Databricks job: the last few complete days
    python archive_rainfall.py --days-back 3
    # backfill a range (e.g. the gap while the poller was paused)
    python archive_rainfall.py --start 2026-10-01
    # locally, into a scratch folder
    python databricks/jobs/archive_rainfall.py --out-dir <dir>/daily --state-dir <dir>/state

Self-contained (requests only) so the Databricks job needs no project wheel.
"""

import argparse
import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("floodsense.archive_rainfall")

API = "https://api-open.data.gov.sg/v2/real-time/api/rainfall"
SGT = timezone(timedelta(hours=8))
EXPECTED_STEPS_PER_DAY = 288
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


class NoMoreData(Exception):
    """data.gov.sg answered 404: it has nothing (more) for this day."""


def _get(session: requests.Session, params: dict[str, str]) -> dict[str, Any]:
    for attempt in range(6):
        resp = session.get(API, params=params, timeout=30)
        if resp.status_code == 404:
            raise NoMoreData(str(params))
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(3 * (attempt + 1))
            continue
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        if body.get("code", 0) != 0:  # v2 reports errors in-band
            raise RuntimeError(f"{API} {params}: API error {body.get('code')}: {body.get('errorMsg')}")
        return body
    raise RuntimeError(f"{API} {params}: still failing after 6 attempts")


def day_pages(session: requests.Session, day: date) -> list[dict[str, Any]]:
    """Every API page for ``day`` (SGT), newest first, unchanged. A 404 part-way keeps the pages
    already fetched: NEA has nothing more for that day."""
    pages: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        params = {"date": day.isoformat()}
        if token:
            params["paginationToken"] = token
        try:
            payload = _get(session, params)
        except NoMoreData:
            if not pages:
                log.warning("%s: no rainfall data from the API", day)
            else:
                log.warning("%s: the API stopped after %d page(s)", day, len(pages))
            return pages
        pages.append(payload)
        token = (payload.get("data") or {}).get("paginationToken")
        if not token:
            return pages
        time.sleep(PAGE_DELAY_SEC)


def steps_in(pages: list[dict[str, Any]], day: date) -> int:
    """Distinct 5-minute timestamps on ``day`` across the pages."""
    stamps = {
        r["timestamp"]
        for p in pages
        for r in (p.get("data") or {}).get("readings") or []
        if str(r.get("timestamp", "")).startswith(day.isoformat())
    }
    return len(stamps)


def archive_day(session: requests.Session, day: date, out_dir: Path, state_dir: Path) -> int:
    """Archive one day; returns the number of 5-minute steps written (0 if already archived)."""
    marker = state_dir / f"{day.isoformat()}.complete"
    if marker.exists():
        return 0
    pages = day_pages(session, day)
    if not pages:
        return 0  # no marker: try again on the next run
    folder = out_dir / day.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    for i, payload in enumerate(pages):
        tmp = state_dir / f"{day.isoformat()}_p{i:02d}.json.tmp"  # outside the Auto Loader path
        tmp.write_text(json.dumps(payload))
        tmp.replace(folder / f"p{i:02d}.json")  # Auto Loader only ever sees whole files
    steps = steps_in(pages, day)
    marker.write_text(
        json.dumps({"pages": len(pages), "steps": steps, "archived_at": datetime.now(SGT).isoformat()})
    )
    if steps < EXPECTED_STEPS_PER_DAY * 0.9:
        log.warning("%s: only %d of %d steps", day, steps, EXPECTED_STEPS_PER_DAY)
    return steps


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="/Volumes/workspace/floodsense/landing/rainfall/daily")
    parser.add_argument(
        "--state-dir", default="/Volumes/workspace/floodsense/landing/archive_state/rainfall"
    )
    parser.add_argument("--start", help="first day (YYYY-MM-DD); default: --days-back before --end")
    parser.add_argument("--end", help="last day (YYYY-MM-DD); default: yesterday (SGT)")
    parser.add_argument("--days-back", type=int, default=3)
    parser.add_argument("--secret-scope", default="floodsense")
    parser.add_argument("--secret-key", default="data_gov_api_key")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    today = datetime.now(SGT).date()
    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    end = min(end, today - timedelta(days=1))  # only complete days
    start = (
        date.fromisoformat(args.start) if args.start else end - timedelta(days=args.days_back - 1)
    )
    session = requests.Session()
    session.headers["User-Agent"] = "floodsense-rainfall-archiver"
    if key := _api_key(args.secret_scope, args.secret_key):
        session.headers["x-api-key"] = key

    out_dir = Path(args.out_dir)
    days, day = 0, start
    while day <= end:
        steps = archive_day(session, day, out_dir, Path(args.state_dir))
        if steps:
            log.info("%s: %d steps", day, steps)
            days += 1
        day += timedelta(days=1)
    log.info("Archived %d new day(s) (%s to %s) into %s", days, start, end, out_dir)


if __name__ == "__main__":
    # No SystemExit: Databricks runs the file under IPython, which reports any SystemExit as a failure.
    main()
