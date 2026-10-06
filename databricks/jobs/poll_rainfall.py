"""
FloodSense - Land recent NEA rainfall in the landing volume (Databricks job task).

Fetches the data.gov.sg ``?date=`` pages that cover the last ``--lookback-minutes`` and writes each
page, unchanged, to the landing volume, where the pipeline's Auto Loader picks it up. Pages overlap
between runs; silver deduplicates readings, so a run every 15-60 minutes loses no 5-minute steps
(the latest-only endpoint would). Use a lookback of 96 h on the first run so gold has its 72 h
warm-up.

The API key is read from the secret ``floodsense/data_gov_api_key`` when it exists; otherwise the
anonymous (rate-limited) API is used.
"""

import argparse
import logging
import time
from datetime import datetime, timedelta

from floodsense.common.config import settings
from floodsense.ingestion.poller import NEAPoller, parse_rainfall_payload

log = logging.getLogger("floodsense.poll_rainfall")

ANONYMOUS_PAGE_DELAY_SEC = 4.0


def _api_key(scope: str, key: str) -> str | None:
    try:
        from databricks.sdk.runtime import dbutils

        return str(dbutils.secrets.get(scope=scope, key=key))
    except Exception as exc:  # no scope, no key, or not on Databricks
        log.warning("No API key from secret %s/%s (%s); using the anonymous API", scope, key, exc)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--landing-dir", default="/Volumes/workspace/floodsense/landing/rainfall/live"
    )
    parser.add_argument("--lookback-minutes", type=int, default=40)
    parser.add_argument("--secret-scope", default="floodsense")
    parser.add_argument("--secret-key", default="data_gov_api_key")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    api_key = _api_key(args.secret_scope, args.secret_key)
    poller = NEAPoller(api_key=api_key, landing_dir=args.landing_dir)
    # Anonymous use allows only a few calls per ~10 s, so pace the pages.
    page_delay = settings.api_page_delay_sec if api_key else ANONYMOUS_PAGE_DELAY_SEC
    now = datetime.now(settings.tzinfo)
    start = now - timedelta(minutes=args.lookback_minutes)
    staged, newest = 0, None
    day = now.date()
    while day >= start.date():
        token: str | None = None
        page = 0
        while True:  # pages come newest first
            params = {"date": day.isoformat()}
            if token:
                params["paginationToken"] = token
            payload = poller._get_json(settings.nea_api_primary, params)
            snapshots = parse_rainfall_payload(payload)
            if snapshots:
                poller.stage_payload_to_volume(
                    payload, f"rainfall_{day:%Y%m%d}_p{page:02d}_fetched_{now:%Y%m%d_%H%M%S}.json"
                )
                staged += 1
                newest = max(newest or snapshots[-1].timestamp, snapshots[-1].timestamp)
            token = (payload.get("data") or {}).get("paginationToken")
            if not token or (snapshots and snapshots[0].timestamp < start):
                break
            page += 1
            time.sleep(page_delay)
        day -= timedelta(days=1)

    if not staged:
        raise RuntimeError("The rainfall API returned no readings; nothing landed")
    log.info("Landed %d page(s) in %s; newest reading %s", staged, args.landing_dir, newest)
    return 0


if __name__ == "__main__":
    # No SystemExit: Databricks runs the file under IPython, which reports any SystemExit as a failure.
    main()
