"""
FloodSense - Radar for the app: the frames at a given time, the nowcast, map images and zone forecasts.

Frames come from the local archive (``data/raw/radar``, written by ``databricks/jobs/archive_radar.py``)
when it has the day, then from the Databricks radar volume (``settings.radar_volume``, kept current by
the daily archiver; how the Databricks App gets them), otherwise from data.gov.sg's date history. That only reaches back about 3 months,
so older times (e.g. the 17 Apr 2021 storm) have no radar.

The forecast risk (plan A1.6) feeds the nowcast's next-hour rain into the existing flood model,
without retraining. It reads as "the risk once this rain has fallen". Its flood skill can't be
validated yet (radar starts 16 Jul 2026), so the app labels it experimental.
"""

import base64
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
from PIL import Image

from floodsense.common.config import settings
from floodsense.common.timeutil import to_sgt
from floodsense.radar.decode import COLOUR_SCALE, BoundingBox, decode_levels, rate_table, zone_index

logger = logging.getLogger("FloodSense.RadarLive")

ARCHIVE_DIR = settings.root_dir / "data" / "raw" / "radar"
API = "https://api-open.data.gov.sg/v2/real-time/api/weather-radar-images/70km"
API_HISTORY_DAYS = 90
STEP = timedelta(minutes=5)
MIN_DISPLAY_MM_H = 0.5  # don't paint drizzle-level advection noise


@dataclass
class RadarView:
    timestamp: datetime
    bbox: BoundingBox
    zones: pd.DataFrame  # ura_planning_area, radar_now_mm_h, nowcast_30_mm, nowcast_60_mm
    images: dict[str, bytes]  # "Now", "+30 min", "+60 min" → transparent PNG

    def corners(self) -> list[list[float]]:
        """Image corners for a map image layer: upper-left, upper-right, lower-right, lower-left."""
        b = self.bbox
        return [
            [b.ul_lon, b.ul_lat],
            [b.lr_lon, b.ul_lat],
            [b.lr_lon, b.lr_lat],
            [b.ul_lon, b.lr_lat],
        ]

    def data_uri(self, key: str) -> str:
        return "data:image/png;base64," + base64.b64encode(self.images[key]).decode()


def _archive_day(day: str, archive: Path) -> pd.DataFrame | None:
    f = archive / "70km" / f"date={day}" / "frames.parquet"
    return pd.read_parquet(f) if f.exists() else None


# Days read from the volume, kept for the life of the process: archived days are complete and never
# change (about 2 MB each). Misses aren't kept, so a day archived later is picked up.
_VOLUME_DAYS: dict[str, pd.DataFrame] = {}
_volume_client: Any = None


def _volume_day(day: str) -> pd.DataFrame | None:
    """A day's frames from the Databricks radar volume, or None (not configured, not archived, or
    unreachable). Uses the Files API, so it also works where /Volumes isn't mounted (Databricks Apps)."""
    global _volume_client
    if not settings.radar_volume:
        return None
    if day in _VOLUME_DAYS:
        return _VOLUME_DAYS[day]
    try:
        if _volume_client is None:
            from databricks.sdk import WorkspaceClient

            p = settings.databricks_profile
            _volume_client = WorkspaceClient(profile=p) if p else WorkspaceClient()
        path = f"{settings.radar_volume.rstrip('/')}/70km/date={day}/frames.parquet"
        data = _volume_client.files.download(path).contents.read()
        df = pd.read_parquet(io.BytesIO(data))
    except Exception as exc:  # NotFound for a day not archived yet, auth, network, missing SDK
        logger.info("Radar volume has no %s (%s)", day, type(exc).__name__)
        return None
    _VOLUME_DAYS[day] = df
    return df


def _api_day(day: str, until: datetime) -> pd.DataFrame | None:
    """Frames for ``day`` from data.gov.sg (newest page first), stopping once 3 before ``until`` are in."""
    headers = {"User-Agent": "floodsense"}
    if settings.data_gov_api_key:
        headers["x-api-key"] = settings.data_gov_api_key
    rows: list[dict[str, Any]] = []
    token = None
    try:
        while True:
            params = {"date": day}
            if token:
                params["paginationToken"] = token
            resp = requests.get(
                API, params=params, headers=headers, timeout=settings.api_timeout_sec
            )
            if resp.status_code != 200:
                return None
            data = resp.json().get("data") or {}
            bbox = data.get("boundaryBox") or {}
            for rec in data.get("records") or []:
                ts = to_sgt(datetime.fromisoformat(rec["timestamp"]))
                if ts > until or ts < until - 3 * STEP:
                    continue
                png = requests.get(rec["image"]["url"], timeout=settings.api_timeout_sec).content
                rows.append(
                    {
                        "timestamp_sgt": ts.isoformat(),
                        "png": png,
                        "bbox_ul_lon": bbox["upperLeft"]["longitude"],
                        "bbox_ul_lat": bbox["upperLeft"]["latitude"],
                        "bbox_lr_lon": bbox["lowerRight"]["longitude"],
                        "bbox_lr_lat": bbox["lowerRight"]["latitude"],
                    }
                )
            records = data.get("records") or []
            oldest = min(
                (to_sgt(datetime.fromisoformat(r["timestamp"])) for r in records), default=None
            )
            token = data.get("paginationToken")
            if not token or (oldest is not None and oldest <= until - 3 * STEP):
                break
    except (requests.RequestException, KeyError, ValueError) as exc:
        logger.warning("Radar API unavailable for %s: %s", day, exc)
        return None
    return pd.DataFrame(rows) if rows else None


def recent_frames(when: datetime, archive: Path = ARCHIVE_DIR) -> list[pd.Series] | None:
    """The last three consecutive frames at or before ``when`` (within 5 minutes), oldest first."""
    when = to_sgt(when)
    if (
        when < datetime.now(settings.tzinfo) - timedelta(days=API_HISTORY_DAYS)
        and not (archive / "70km" / f"date={when.date().isoformat()}").exists()
        and not settings.radar_volume
    ):
        return None
    days = sorted({(when - k * STEP).date().isoformat() for k in range(4)})
    parts = []
    for day in days:
        df = _archive_day(day, archive)
        if df is None:
            df = _volume_day(day)
        if df is None:
            df = _api_day(day, when + STEP)
        if df is not None:
            parts.append(df)
    if not parts:
        return None
    frames = pd.concat(parts).drop_duplicates("timestamp_sgt")
    frames["t"] = pd.to_datetime(frames["timestamp_sgt"])
    frames = frames[frames.t <= pd.Timestamp(when) + pd.Timedelta(minutes=2)].sort_values("t")
    if len(frames) < 3:
        return None
    last = frames.tail(3)
    gaps = last.t.diff().dropna()
    if (gaps != pd.Timedelta(STEP)).any() or pd.Timestamp(when) - last.t.iloc[-1] > pd.Timedelta(
        STEP
    ):
        return None
    return [row for _, row in last.iterrows()]


def render_png(rates: np.ndarray) -> bytes:
    """Rain rates as a transparent PNG in MSS's own colours (nearest calibrated level)."""
    table = rate_table()[1:]
    edges = np.sqrt(table[:-1] * table[1:])  # geometric midpoints between levels
    level = np.searchsorted(edges, rates)  # 0..32 → colour index
    rgba = np.zeros((*rates.shape, 4), dtype=np.uint8)
    colours = np.array(COLOUR_SCALE, dtype=np.uint8)
    show = rates >= MIN_DISPLAY_MM_H
    rgba[show, :3] = colours[level[show]]
    rgba[show, 3] = 255
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def radar_view(when: datetime, archive: Path = ARCHIVE_DIR) -> RadarView | None:
    """Radar now and the nowcast at ``when``, or None when no radar exists for that time."""
    from floodsense.radar.nowcast import accumulation_mm, extrapolate, motion_field

    frames = recent_frames(when, archive)
    if frames is None:
        return None
    levels = [decode_levels(f["png"]) for f in frames]
    rates_now = rate_table()[levels[-1]]
    fields = extrapolate(rates_now, motion_field(levels), 12)
    bbox = BoundingBox.from_row(frames[-1])
    idx, names = zone_index(bbox, rates_now.shape)

    def zone_mean(field: np.ndarray) -> np.ndarray:
        inside = idx >= 0
        sums = np.bincount(idx[inside], weights=field[inside], minlength=len(names))
        return sums / np.maximum(np.bincount(idx[inside], minlength=len(names)), 1)

    zones = pd.DataFrame(
        {
            "ura_planning_area": names,
            "radar_now_mm_h": zone_mean(rates_now),
            "nowcast_30_mm": zone_mean(accumulation_mm(fields[:6])),
            "nowcast_60_mm": zone_mean(accumulation_mm(fields)),
        }
    )
    images = {
        "Now": render_png(rates_now),
        "+30 min": render_png(fields[5]),
        "+60 min": render_png(fields[11]),
    }
    return RadarView(frames[-1]["t"].to_pydatetime(), bbox, zones, images)


def forecast_features(features: pd.DataFrame, zones: pd.DataFrame) -> pd.DataFrame:
    """Zone features with the next hour's nowcast rain in place of the last hour's (A1.6)."""
    f = features.merge(
        zones[["ura_planning_area", "nowcast_60_mm"]], on="ura_planning_area", how="left"
    )
    nc = f["nowcast_60_mm"].fillna(0.0)
    f["rain_120m"] = f["rain_60m"] + nc  # observed hour + forecast hour
    f["rain_60m"] = nc
    return f.drop(columns="nowcast_60_mm")
