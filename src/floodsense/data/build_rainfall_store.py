"""
FloodSense - Build the historical rainfall store from official NEA data.

Sources (both published by NEA on data.gov.sg):
- 2017-2024: the "Historical Rainfall across Singapore" yearly CSVs (data.gov.sg collection 2279),
  one row per station per 5-minute step, about 1 GB per year.
- 2025 onward (not yet in the collection): the real-time rainfall API's ``?date=`` history, via
  ``NEAPoller`` - the same endpoint the live app and the committed replay use.

    python -m floodsense.data.build_rainfall_store download [--years 2017 2018 ...]
    python -m floodsense.data.build_rainfall_store convert  [--years ...] [--keep-raw]
    python -m floodsense.data.build_rainfall_store backfill [--start 2025-01-01] [--end YYYY-MM-DD]
    python -m floodsense.data.build_rainfall_store stations

Writes the layout described in ``floodsense.data.rainfall_store``. Every step is resumable, and
nothing here invents, fills or copies readings: a step with no reading from a station stays absent.
"""

import argparse
import hashlib
import json
import logging
import sys
import time
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pacsv
import requests

from floodsense.common.config import settings
from floodsense.common.schemas import RainfallSnapshot

logger = logging.getLogger("FloodSense.RainfallStore")

# data.gov.sg collection 2279 "Historical Rainfall across Singapore" (NEA).
BULK_DATASETS: dict[int, str] = {
    2017: "d_1990a5a1aeaf3dd243cf4dae294a61c4",
    2018: "d_024fb501ce7092b71bb713eaf54fa7eb",
    2019: "d_61995f092320e7155b7528050880b502",
    2020: "d_9e7de44094f876f6804b8b5bcee45c81",
    2021: "d_3b41598f74f1f11fc3430348fea51af5",
    2022: "d_42d64cc6c176ace1c52fbb40b9ede302",
    2023: "d_f864cc30d58b467db83659ad17c737bf",
    2024: "d_a0b69d3e02576a1fd0ab673e71f83507",
}
DOWNLOAD_API = "https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/{action}"
READING_TYPE = "TB1 Rainfall 5 Minute Total F"
MAX_5MIN_MM = 100.0
STEP = pd.Timedelta(minutes=5)
SGT = "Asia/Singapore"


def bulk_dir() -> Path:
    return settings.rainfall_dir / "bulk"


def sightings_dir() -> Path:
    """Per-source station sightings; ``stations`` combines them into ``stations.parquet``."""
    return settings.rainfall_dir / "station_sightings"


def _manifest_path() -> Path:
    return settings.rainfall_dir / "manifest.json"


def _update_manifest(key: str, entry: dict) -> None:
    path = _manifest_path()
    manifest = json.loads(path.read_text()) if path.exists() else {}
    manifest[key] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


# =============================================================================================
# download
# =============================================================================================


def _get_with_backoff(session: requests.Session, url: str, tries: int = 8) -> requests.Response:
    """GET that waits out data.gov.sg's rate limit (HTTP 429), which the API backfill shares."""
    for attempt in range(1, tries + 1):
        resp = session.get(url, timeout=30)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        time.sleep(10 * attempt)
    resp.raise_for_status()
    return resp


def _signed_url(dataset_id: str, session: requests.Session) -> str:
    _get_with_backoff(
        session, DOWNLOAD_API.format(dataset_id=dataset_id, action="initiate-download")
    )
    for _ in range(60):
        resp = _get_with_backoff(
            session, DOWNLOAD_API.format(dataset_id=dataset_id, action="poll-download")
        )
        data = resp.json().get("data") or {}
        if data.get("status") == "DOWNLOAD_SUCCESS" and data.get("url"):
            return str(data["url"])
        time.sleep(5)
    raise RuntimeError(f"{dataset_id}: download link not ready after 5 minutes")


def download_year(year: int, force: bool = False) -> Path:
    """Download one year's CSV into ``bulk_dir()``; skipped when a complete copy exists."""
    dataset_id = BULK_DATASETS[year]
    dest = bulk_dir() / f"rainfall_{year}.csv"
    session = requests.Session()
    url = _signed_url(dataset_id, session)
    size = int(session.head(url, timeout=60).headers.get("Content-Length", -1))
    if dest.exists() and dest.stat().st_size == size and not force:
        logger.info("%d: already downloaded (%d bytes)", year, size)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part")
    sha = hashlib.sha256()
    with session.get(url, stream=True, timeout=120) as resp, part.open("wb") as fh:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=1 << 20):
            fh.write(chunk)
            sha.update(chunk)
    if size >= 0 and part.stat().st_size != size:
        raise RuntimeError(f"{year}: got {part.stat().st_size} bytes, expected {size}")
    part.replace(dest)
    _update_manifest(
        f"bulk_{year}",
        {
            "source": f"data.gov.sg {dataset_id} (Historical Rainfall across Singapore {year})",
            "bytes": dest.stat().st_size,
            "sha256": sha.hexdigest(),
            "downloaded_at": datetime.now(settings.tzinfo).isoformat(),
        },
    )
    logger.info("%d: downloaded %.0f MB", year, dest.stat().st_size / 1e6)
    return dest


# =============================================================================================
# convert
# =============================================================================================

_CSV_COLUMNS = {
    "timestamp": pa.string(),
    "station_id": pa.string(),
    "station_name": pa.string(),
    "location_longitude": pa.float64(),
    "location_latitude": pa.float64(),
    "reading_value": pa.float64(),
    "reading_type": pa.string(),
    "reading_unit": pa.string(),
}


def snap_to_grid(ts: pd.Series) -> pd.Series:
    """Parse ISO timestamps to SGT and snap to the 5-minute grid (early records end in :59 s)."""
    return pd.to_datetime(ts, utc=True, format="ISO8601").dt.tz_convert(SGT).dt.round("5min")


def _clean_readings(df: pd.DataFrame, counts: dict[str, int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalise one CSV batch. Returns (readings, station sightings)."""
    keep = (df["reading_type"] == READING_TYPE) & (df["reading_unit"] == "mm")
    counts["other_reading_type"] += int((~keep).sum())
    df = df.loc[keep]
    value = df["reading_value"]
    bad = value.isna() | (value < 0) | (value > MAX_5MIN_MM)
    counts["out_of_range"] += int(bad.sum())
    df = df.loc[~bad]

    ts = snap_to_grid(df["timestamp"])
    readings = pd.DataFrame(
        {"station_id": df["station_id"], "timestamp": ts, "rainfall_mm": df["reading_value"]}
    ).reset_index(drop=True)
    sightings = pd.DataFrame(
        {
            "station_id": df["station_id"],
            "name": df["station_name"],
            "latitude": df["location_latitude"].round(4),
            "longitude": df["location_longitude"].round(4),
            "timestamp": ts,
        }
    )
    return readings, _summarise_sightings(sightings)


def _summarise_sightings(s: pd.DataFrame) -> pd.DataFrame:
    """Collapse per-reading station rows to (station, name, location) with first/last seen."""
    if s.empty:
        return pd.DataFrame(
            columns=["station_id", "name", "latitude", "longitude", "first_seen", "last_seen"]
        )
    return s.groupby(["station_id", "name", "latitude", "longitude"], as_index=False)[
        "timestamp"
    ].agg(first_seen="min", last_seen="max")


def _dedupe_sorted(readings: pd.DataFrame, counts: dict[str, int]) -> pd.DataFrame:
    before = len(readings)
    readings = readings.drop_duplicates(["station_id", "timestamp"], keep="last")
    counts["duplicates"] += before - len(readings)
    return readings.sort_values(["timestamp", "station_id"], kind="stable").reset_index(drop=True)


def _write_year(readings: pd.DataFrame, year: int, source: str) -> Path:
    path = settings.rainfall_readings_dir / f"year={year}" / f"part-{source}.parquet"
    out = readings.astype({"station_id": "string", "rainfall_mm": "float64"})
    _write_atomic(out[["station_id", "timestamp", "rainfall_mm"]], path)
    return path


def convert_year(year: int, keep_raw: bool = True) -> dict[str, int]:
    """Convert one downloaded CSV into ``readings/year=YYYY/part-bulk.parquet``."""
    src = bulk_dir() / f"rainfall_{year}.csv"
    if not src.exists():
        raise FileNotFoundError(f"{src} missing: run the download step first")
    counts = {
        "rows": 0,
        "other_reading_type": 0,
        "out_of_range": 0,
        "duplicates": 0,
        "other_year": 0,
    }
    reader = pacsv.open_csv(
        src,
        read_options=pacsv.ReadOptions(block_size=64 << 20),
        convert_options=pacsv.ConvertOptions(
            column_types=_CSV_COLUMNS, include_columns=list(_CSV_COLUMNS)
        ),
    )
    parts: list[pd.DataFrame] = []
    sightings: list[pd.DataFrame] = []
    for batch in reader:
        df = batch.to_pandas()
        counts["rows"] += len(df)
        readings, seen = _clean_readings(df, counts)
        readings["station_id"] = readings["station_id"].astype("category")
        parts.append(readings)
        sightings.append(seen)

    readings = pd.concat(parts, ignore_index=True)
    readings["station_id"] = readings["station_id"].astype(str)
    in_year = readings["timestamp"].dt.year == year
    counts["other_year"] = int((~in_year).sum())
    readings = _dedupe_sorted(readings.loc[in_year], counts)
    counts["written"] = len(readings)
    _write_year(readings, year, "bulk")

    seen = (
        pd.concat(sightings, ignore_index=True)
        .groupby(["station_id", "name", "latitude", "longitude"], as_index=False)
        .agg(first_seen=("first_seen", "min"), last_seen=("last_seen", "max"))
    )
    _write_atomic(seen, sightings_dir() / f"bulk-{year}.parquet")
    _update_manifest(f"readings_{year}_bulk", {"source": f"bulk_{year}", **counts})
    if not keep_raw:
        src.unlink()
    logger.info("%d: %s", year, counts)
    return counts


# =============================================================================================
# backfill (API) - years the bulk collection doesn't cover yet
# =============================================================================================


def _snapshots_to_frames(snaps: Iterable[RainfallSnapshot]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[tuple[str, pd.Timestamp, float]] = []
    seen: list[tuple[str, str, float, float, pd.Timestamp]] = []
    for s in snaps:
        ts = pd.Timestamp(s.timestamp).tz_convert(SGT).round("5min")
        for sid, mm in s.readings.items():
            rows.append((sid, ts, float(mm)))
            meta = s.stations.get(sid)
            if meta is not None:
                seen.append((sid, meta.name, round(meta.latitude, 4), round(meta.longitude, 4), ts))
    readings = pd.DataFrame(rows, columns=["station_id", "timestamp", "rainfall_mm"])
    sightings = pd.DataFrame(
        seen, columns=["station_id", "name", "latitude", "longitude", "timestamp"]
    )
    return readings, _summarise_sightings(sightings)


def _fetch_day(poller, day: date, max_tries: int = 6) -> list[RainfallSnapshot]:  # noqa: ANN001
    from floodsense.ingestion.poller import LiveFeedUnavailable

    for attempt in range(1, max_tries + 1):
        try:
            snaps: list[RainfallSnapshot] = []
            for page in poller._iter_day_pages(day):
                snaps.extend(page)
            return snaps
        except LiveFeedUnavailable as exc:
            wait = 15 * attempt
            logger.warning("%s: %s; retrying in %ds", day, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"{day}: rainfall API kept failing")


def backfill(start: date, end: date, day_delay_sec: float = 1.0) -> None:
    """Fetch ``[start, end]`` from the API one month at a time; finished months are skipped."""
    from floodsense.ingestion.poller import NEAPoller

    poller = NEAPoller()
    month = date(start.year, start.month, 1)
    while month <= end:
        nxt = (month + timedelta(days=32)).replace(day=1)
        last = min(nxt - timedelta(days=1), end)
        path = (
            settings.rainfall_readings_dir
            / f"year={month.year}"
            / f"part-api-{month:%Y-%m}.parquet"
        )
        complete = nxt - timedelta(days=1) <= end
        if path.exists() and complete:
            month = nxt
            continue
        counts = {"duplicates": 0}
        day, snaps = max(month, start), []
        while day <= last:
            snaps.extend(_fetch_day(poller, day))
            day += timedelta(days=1)
            time.sleep(day_delay_sec)
        readings, seen = _snapshots_to_frames(snaps)
        lo = pd.Timestamp(max(month, start), tz=SGT)
        hi = pd.Timestamp(last + timedelta(days=1), tz=SGT)
        readings = readings[(readings["timestamp"] >= lo) & (readings["timestamp"] < hi)]
        readings = _dedupe_sorted(readings, counts)
        _write_atomic(
            readings[["station_id", "timestamp", "rainfall_mm"]].astype({"station_id": "string"}),
            path,
        )
        _write_atomic(seen, sightings_dir() / f"api-{month:%Y-%m}.parquet")
        _update_manifest(
            f"readings_{month:%Y-%m}_api",
            {
                "source": settings.nea_api_primary,
                "rows": len(readings),
                "complete_month": complete,
                **counts,
            },
        )
        logger.info("%s: %d readings from the API", f"{month:%Y-%m}", len(readings))
        month = nxt


# =============================================================================================
# stations
# =============================================================================================


def build_station_table() -> pd.DataFrame:
    """
    Combine all sightings into ``stations.parquet``: one row per (station, location) period.

    A station's location periods are runs of consecutive sightings at the same rounded coordinates.
    The first period has a null ``valid_from`` and the last a null ``valid_to`` so that
    ``stations_at`` always resolves a station that has ever reported.
    """
    files = sorted(sightings_dir().glob("*.parquet"))
    if not files:
        raise FileNotFoundError(
            f"no station sightings in {sightings_dir()}: convert or backfill first"
        )
    seen = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    seen = seen.sort_values(["station_id", "first_seen"]).reset_index(drop=True)

    rows = []
    for sid, grp in seen.groupby("station_id", sort=True):
        loc = list(zip(grp["latitude"], grp["longitude"], strict=True))
        run_id = np.cumsum([True] + [a != b for a, b in zip(loc[:-1], loc[1:], strict=True)])
        periods = grp.assign(run=run_id).groupby("run", sort=True)
        for _, p in periods:
            rows.append(
                {
                    "station_id": sid,
                    "name": p.sort_values("last_seen")["name"].iloc[-1],
                    "latitude": float(p["latitude"].iloc[0]),
                    "longitude": float(p["longitude"].iloc[0]),
                    "valid_from": p["first_seen"].min(),
                    "valid_to": p["last_seen"].max(),
                }
            )
    table = pd.DataFrame(rows)
    # Periods of one station meet where the next begins; the outer ends are open.
    for col in ("valid_from", "valid_to"):
        table[col] = pd.to_datetime(table[col]).dt.tz_convert(SGT)
    nxt_from = table.groupby("station_id")["valid_from"].shift(-1)
    table["valid_to"] = nxt_from
    first = table.groupby("station_id").cumcount() == 0
    table.loc[first, "valid_from"] = pd.NaT
    table = table.astype({"station_id": "string", "name": "string"})
    _write_atomic(table, settings.rainfall_stations_file)
    logger.info(
        "stations.parquet: %d stations, %d location periods",
        table["station_id"].nunique(),
        len(table),
    )
    return table


# =============================================================================================
# CLI
# =============================================================================================


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("download", "convert"):
        p = sub.add_parser(name)
        p.add_argument("--years", type=int, nargs="+", default=sorted(BULK_DATASETS))
        if name == "convert":
            p.add_argument("--keep-raw", action="store_true", help="keep the CSVs after converting")
        else:
            p.add_argument("--force", action="store_true")
    p = sub.add_parser("backfill")
    p.add_argument("--start", type=date.fromisoformat, default=date(max(BULK_DATASETS) + 1, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=None, help="default: yesterday (SGT)")
    sub.add_parser("stations")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "download":
        for year in args.years:
            for attempt in range(1, 4):
                try:
                    download_year(year, force=args.force)
                    break
                except (requests.ConnectionError, requests.Timeout) as exc:
                    if attempt == 3:
                        raise
                    logger.warning("%d: %s; retrying (%d/3)", year, exc, attempt)
                    time.sleep(30)
    elif args.cmd == "convert":
        for year in args.years:
            convert_year(year, keep_raw=args.keep_raw)
        build_station_table()
    elif args.cmd == "backfill":
        end = args.end or (datetime.now(settings.tzinfo).date() - timedelta(days=1))
        backfill(args.start, end)
        build_station_table()
    else:
        build_station_table()
    return 0


if __name__ == "__main__":
    sys.exit(main())
