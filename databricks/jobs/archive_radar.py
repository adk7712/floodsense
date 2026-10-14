"""
FloodSense - Archive NEA rain-area radar frames (data.gov.sg ``weather-radar-images``).

The API keeps only about 3 months of history (checked 14 Oct 2026: frames from 16 Jul 2026; earlier
dates return 404), so frames must be archived before they expire. Each frame is a rain-intensity
PNG (every 5 minutes) plus a bounding box in EPSG:4326. It is not Doppler data: motion has to be
estimated from consecutive frames.

One Parquet file per day and size is written to ``<out-dir>/<size>/date=YYYY-MM-DD/frames.parquet``
with the PNG bytes and the metadata of every frame. Days already archived are skipped, so the job
is safe to re-run and a missed day is caught up on the next run.

    # backfill locally (no Databricks compute), then upload the folder to the radar volume
    python databricks/jobs/archive_radar.py --out-dir data/raw/radar --start 2026-07-16
    # daily Databricks job: the last few days into the volume
    python archive_radar.py --out-dir /Volumes/workspace/floodsense/radar --days-back 3

Self-contained (requests, pandas, pyarrow) so the Databricks job needs no project wheel.
"""

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

log = logging.getLogger("floodsense.archive_radar")

API = "https://api-open.data.gov.sg/v2/real-time/api/weather-radar-images/{size}"
SIZES = ("70km", "240km")
SGT = timezone(timedelta(hours=8))
EXPECTED_FRAMES_PER_DAY = 288
PAGE_DELAY_SEC = 0.3


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


def _get(session: requests.Session, url: str, params: dict[str, str]) -> dict[str, Any]:
    for attempt in range(5):
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        return body
    raise RuntimeError(f"{url} {params}: still rate-limited after 5 attempts")


def day_records(
    session: requests.Session, size: str, day: date
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every frame record for ``day`` (SGT), plus the day's projection and bounding box."""
    url, records, meta, token = API.format(size=size), [], {}, None
    while True:
        params = {"date": day.isoformat()}
        if token:
            params["paginationToken"] = token
        data = _get(session, url, params).get("data") or {}
        records.extend(data.get("records") or [])
        meta = {"projection": data.get("projection"), "boundaryBox": data.get("boundaryBox")}
        token = data.get("paginationToken")
        if not token:
            return records, meta
        time.sleep(PAGE_DELAY_SEC)


def frames_table(
    records: list[dict[str, Any]], meta: dict[str, Any], size: str, images: list[bytes]
) -> pd.DataFrame:
    """One row per frame: SGT and UTC timestamps, the PNG bytes and the georeference."""
    bbox = meta.get("boundaryBox") or {}
    centre = (meta.get("projection") or {}).get("center") or {}
    rows = []
    for rec, png in zip(records, images, strict=True):
        ts = datetime.fromisoformat(rec["timestamp"])
        img = rec.get("image") or {}
        rows.append(
            {
                "timestamp_sgt": ts.astimezone(SGT).isoformat(),
                "timestamp_utc": ts.astimezone(UTC).isoformat(),
                "size": size,
                "png": png,
                "png_bytes": len(png),
                "label": img.get("label"),
                "range": str(img.get("range")) if img.get("range") is not None else None,
                "format": img.get("format"),
                "bbox_ul_lon": (bbox.get("upperLeft") or {}).get("longitude"),
                "bbox_ul_lat": (bbox.get("upperLeft") or {}).get("latitude"),
                "bbox_lr_lon": (bbox.get("lowerRight") or {}).get("longitude"),
                "bbox_lr_lat": (bbox.get("lowerRight") or {}).get("latitude"),
                "bbox_crs": bbox.get("crs"),
                "projection": (meta.get("projection") or {}).get("type"),
                "projection_centre_lon": centre.get("longitude"),
                "projection_centre_lat": centre.get("latitude"),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("timestamp_utc")
        .drop_duplicates("timestamp_utc")
        .reset_index(drop=True)
    )


def archive_day(
    session: requests.Session, size: str, day: date, out_dir: Path, workers: int
) -> int:
    """Archive one day; returns the number of frames written (0 if already archived or none)."""
    target = out_dir / size / f"date={day.isoformat()}" / "frames.parquet"
    if target.exists():
        return 0
    try:
        records, meta = day_records(session, size, day)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            log.warning("%s %s: not available (outside the API's history window)", size, day)
            return 0
        raise
    if not records:
        log.warning("%s %s: no frames", size, day)
        return 0

    def fetch(rec: dict[str, Any]) -> bytes:
        for attempt in range(4):
            try:
                resp = requests.get(rec["image"]["url"], timeout=30)
                resp.raise_for_status()
                return resp.content
            except requests.RequestException:
                time.sleep(1 + attempt)
        raise RuntimeError(f"could not download frame {rec['timestamp']}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        images = list(pool.map(fetch, records))
    table = frames_table(records, meta, size, images)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    table.to_parquet(tmp, index=False)
    tmp.rename(target)  # only complete days ever appear under their final name
    if len(table) < EXPECTED_FRAMES_PER_DAY * 0.9:
        log.warning("%s %s: only %d of %d frames", size, day, len(table), EXPECTED_FRAMES_PER_DAY)
    return len(table)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--start", help="first day (YYYY-MM-DD); default: --days-back before today")
    parser.add_argument("--end", help="last day (YYYY-MM-DD); default: yesterday (SGT)")
    parser.add_argument("--days-back", type=int, default=3)
    parser.add_argument("--sizes", default=",".join(SIZES))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--secret-scope", default="floodsense")
    parser.add_argument("--secret-key", default="data_gov_api_key")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    today = datetime.now(SGT).date()
    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
    start = (
        date.fromisoformat(args.start) if args.start else end - timedelta(days=args.days_back - 1)
    )
    session = requests.Session()
    session.headers["User-Agent"] = "floodsense-radar-archiver"
    if key := _api_key(args.secret_scope, args.secret_key):
        session.headers["x-api-key"] = key

    out_dir = Path(args.out_dir)
    total, day = 0, start
    while day <= end:
        for size in args.sizes.split(","):
            n = archive_day(session, size, day, out_dir, args.workers)
            if n:
                log.info("%s %s: %d frames", size, day, n)
            total += n
        day += timedelta(days=1)
    log.info("Archived %d new frames (%s to %s) into %s", total, start, end, out_dir)


if __name__ == "__main__":
    # No SystemExit: Databricks runs the file under IPython, which reports any SystemExit as a failure.
    main()
