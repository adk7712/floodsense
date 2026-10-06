"""
FloodSense - MRT/LRT stations exposed to flood risk (Track B2 stretch goal: cascading impact).

Source: data.gov.sg dataset 'LTA MRT Station Exit (GEOJSON)' (LTA), dataset ID
d_b39d3a0871985372d7e1637193335da5. Target: data/reference/lta_mrt_station_exits.geojson, written by

    uv run python -m floodsense.data.transport

A station is "at risk" when one of its exits lies in a planning area FloodSense rates Moderate or
High, or inside an active PUB flood-alert circle. That is exposure, not an observed disruption:
LTA's service-disruption feeds are not used.
"""

import json
import logging
import math
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

from floodsense.common.config import settings
from floodsense.ingestion.flood_alerts import FloodAlert, zone_for_point

logger = logging.getLogger("FloodSense.Transport")

DATASET_ID = "d_b39d3a0871985372d7e1637193335da5"
SOURCE_NAME = "LTA MRT Station Exit (data.gov.sg)"
MRT_EXITS_FILE = settings.root_dir / "data" / "reference" / "lta_mrt_station_exits.geojson"
_API = f"https://api-open.data.gov.sg/v1/public/api/datasets/{DATASET_ID}"
TIER_ORDER = {"High": 0, "Moderate": 1, "PUB alert": 2}


def download_mrt_exits(out_path: Path = MRT_EXITS_FILE) -> Path:
    """Fetch the station-exit GeoJSON from data.gov.sg and write it unchanged."""
    headers = {"User-Agent": "floodsense"}
    requests.get(f"{_API}/initiate-download", headers=headers, timeout=15).raise_for_status()
    poll = requests.get(f"{_API}/poll-download", headers=headers, timeout=15)
    poll.raise_for_status()
    geo = requests.get(poll.json()["data"]["url"], timeout=30)
    geo.raise_for_status()
    features = geo.json().get("features") or []
    if not features:
        raise RuntimeError(f"{DATASET_ID} returned no features")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(geo.text)
    logger.info("Wrote %d MRT/LRT station exits to %s", len(features), out_path)
    return out_path


def _station_name(raw: str) -> str:
    name = raw.strip().title()
    for suffix in (" Mrt Station", " Lrt Station"):
        if name.endswith(suffix):
            return name[: -len(suffix)] + suffix.replace(" Station", "").upper()
    return name


@lru_cache(maxsize=1)
def load_station_exits(path: Path | None = None) -> pd.DataFrame:
    """One row per exit: station, latitude, longitude, zone. Raises when the file is missing."""
    target = path or MRT_EXITS_FILE
    if not target.exists():
        raise FileNotFoundError(
            f"{target} is missing; run `python -m floodsense.data.transport` to download it"
        )
    rows = []
    for feature in json.loads(target.read_text())["features"]:
        lon, lat = feature["geometry"]["coordinates"][:2]
        rows.append(
            {
                "station": _station_name(str(feature["properties"]["STATION_NA"])),
                "latitude": float(lat),
                "longitude": float(lon),
                "zone": zone_for_point(float(lat), float(lon)),
            }
        )
    return pd.DataFrame(rows)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def stations_at_risk(
    zone_risk: pd.DataFrame,
    alerts: list[FloodAlert] | None = None,
    exits: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Stations with an exit in a Moderate/High zone or inside an active PUB alert circle.

    ``zone_risk`` has ``ura_planning_area``, ``risk_tier`` and ``flood_probability``. Returns one
    row per station: station, zone, tier ("High", "Moderate" or "PUB alert"), probability and
    reason, most urgent first.
    """
    exits = load_station_exits() if exits is None else exits
    risky = zone_risk[zone_risk["risk_tier"].isin(["High", "Moderate"])]
    by_zone = risky.set_index("ura_planning_area")
    rows = []
    for exit_row in exits.itertuples(index=False):
        if exit_row.zone in by_zone.index:
            z = by_zone.loc[exit_row.zone]
            rows.append(
                {
                    "station": exit_row.station,
                    "zone": exit_row.zone,
                    "tier": z["risk_tier"],
                    "probability": float(z["flood_probability"]),
                    "reason": f"{str(exit_row.zone).title()} is at {z['risk_tier']} risk",
                }
            )
        for alert in alerts or []:
            if alert.latitude is None or alert.longitude is None:
                continue
            radius = alert.radius_km or 0.5
            if (
                _km(exit_row.latitude, exit_row.longitude, alert.latitude, alert.longitude)
                <= radius
            ):
                rows.append(
                    {
                        "station": exit_row.station,
                        "zone": exit_row.zone,
                        "tier": "PUB alert",
                        "probability": float("nan"),
                        "reason": f"Within {radius:g} km of a PUB flood alert: {alert.area_desc}",
                    }
                )
    if not rows:
        return pd.DataFrame(columns=["station", "zone", "tier", "probability", "reason"])
    out = pd.DataFrame(rows)
    out["order"] = out["tier"].map(TIER_ORDER)
    out = out.sort_values(["order", "probability"], ascending=[True, False])
    return out.drop_duplicates("station").drop(columns="order").reset_index(drop=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    print(download_mrt_exits())
