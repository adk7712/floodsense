"""
FloodSense - Singapore Geospatial Reference Data.
Planning-area reference points for the 55 URA Planning Areas derived from the official URA Master Plan
2019 polygon boundaries (representative_point), and NEA rainfall stations loaded from the reference
snapshot of data.gov.sg station metadata.
"""

import json
from pathlib import Path

from floodsense.common.config import settings

# 55 URA Planning Areas with polygon-derived reference points (lat, lon) and Region
URA_PLANNING_AREAS: dict[str, dict] = {
    # Central Region
    "BISHAN": {"lat": 1.3552, "lon": 103.8377, "region": "Central", "pub_monitored": 1},
    "BUKIT MERAH": {"lat": 1.2745, "lon": 103.8216, "region": "Central", "pub_monitored": 1},
    "BUKIT TIMAH": {"lat": 1.3281, "lon": 103.7934, "region": "Central", "pub_monitored": 1},
    "DOWNTOWN CORE": {"lat": 1.2866, "lon": 103.8561, "region": "Central", "pub_monitored": 1},
    "GEYLANG": {"lat": 1.3212, "lon": 103.8899, "region": "Central", "pub_monitored": 1},
    "KALLANG": {"lat": 1.3123, "lon": 103.8651, "region": "Central", "pub_monitored": 1},
    "MARINA EAST": {"lat": 1.2882, "lon": 103.8717, "region": "Central", "pub_monitored": 0},
    "MARINA SOUTH": {"lat": 1.2808, "lon": 103.8660, "region": "Central", "pub_monitored": 0},
    "MARINE PARADE": {"lat": 1.2991, "lon": 103.8985, "region": "Central", "pub_monitored": 1},
    "MUSEUM": {"lat": 1.2960, "lon": 103.8475, "region": "Central", "pub_monitored": 0},
    "NEWTON": {"lat": 1.3085, "lon": 103.8410, "region": "Central", "pub_monitored": 1},
    "NOVENA": {"lat": 1.3261, "lon": 103.8372, "region": "Central", "pub_monitored": 1},
    "ORCHARD": {"lat": 1.3040, "lon": 103.8341, "region": "Central", "pub_monitored": 1},
    "OUTRAM": {"lat": 1.2816, "lon": 103.8437, "region": "Central", "pub_monitored": 1},
    "QUEENSTOWN": {"lat": 1.2869, "lon": 103.7851, "region": "Central", "pub_monitored": 1},
    "RIVER VALLEY": {"lat": 1.2979, "lon": 103.8364, "region": "Central", "pub_monitored": 1},
    "ROCHOR": {"lat": 1.3050, "lon": 103.8543, "region": "Central", "pub_monitored": 1},
    "SINGAPORE RIVER": {"lat": 1.2909, "lon": 103.8405, "region": "Central", "pub_monitored": 1},
    "SOUTHERN ISLANDS": {"lat": 1.2489, "lon": 103.8343, "region": "Central", "pub_monitored": 0},
    "STRAITS VIEW": {"lat": 1.2714, "lon": 103.8593, "region": "Central", "pub_monitored": 0},
    "TANGLIN": {"lat": 1.3076, "lon": 103.8151, "region": "Central", "pub_monitored": 1},
    "TOA PAYOH": {"lat": 1.3365, "lon": 103.8625, "region": "Central", "pub_monitored": 1},
    # East Region
    "BEDOK": {"lat": 1.3250, "lon": 103.9309, "region": "East", "pub_monitored": 1},
    "CHANGI": {"lat": 1.3518, "lon": 103.9972, "region": "East", "pub_monitored": 1},
    "CHANGI BAY": {"lat": 1.2915, "lon": 104.0712, "region": "East", "pub_monitored": 0},
    "PASIR RIS": {"lat": 1.3769, "lon": 103.9535, "region": "East", "pub_monitored": 1},
    "PAYA LEBAR": {"lat": 1.3606, "lon": 103.9174, "region": "East", "pub_monitored": 1},
    "TAMPINES": {"lat": 1.3450, "lon": 103.9491, "region": "East", "pub_monitored": 1},
    # North Region
    "CENTRAL WATER CATCHMENT": {
        "lat": 1.3761,
        "lon": 103.8017,
        "region": "North",
        "pub_monitored": 1,
    },
    "LIM CHU KANG": {"lat": 1.4310, "lon": 103.7192, "region": "North", "pub_monitored": 0},
    "MANDAI": {"lat": 1.4271, "lon": 103.8128, "region": "North", "pub_monitored": 1},
    "NORTH-EASTERN ISLANDS": {
        "lat": 1.3891,
        "lon": 104.0526,
        "region": "North",
        "pub_monitored": 0,
    },
    "SEMBAWANG": {"lat": 1.4530, "lon": 103.8187, "region": "North", "pub_monitored": 1},
    "SIMPANG": {"lat": 1.4425, "lon": 103.8497, "region": "North", "pub_monitored": 0},
    "SUNGEI KADUT": {"lat": 1.4176, "lon": 103.7561, "region": "North", "pub_monitored": 1},
    "WOODLANDS": {"lat": 1.4418, "lon": 103.7885, "region": "North", "pub_monitored": 1},
    "YISHUN": {"lat": 1.4190, "lon": 103.8434, "region": "North", "pub_monitored": 1},
    # North-East Region
    "ANG MO KIO": {"lat": 1.3767, "lon": 103.8426, "region": "North-East", "pub_monitored": 1},
    "HOUGANG": {"lat": 1.3609, "lon": 103.8888, "region": "North-East", "pub_monitored": 1},
    "PUNGGOL": {"lat": 1.4037, "lon": 103.9092, "region": "North-East", "pub_monitored": 1},
    "SELETAR": {"lat": 1.4163, "lon": 103.8793, "region": "North-East", "pub_monitored": 0},
    "SENGKANG": {"lat": 1.3886, "lon": 103.8955, "region": "North-East", "pub_monitored": 1},
    "SERANGOON": {"lat": 1.3660, "lon": 103.8676, "region": "North-East", "pub_monitored": 1},
    # West Region
    "BOON LAY": {"lat": 1.3154, "lon": 103.7078, "region": "West", "pub_monitored": 0},
    "BUKIT BATOK": {"lat": 1.3560, "lon": 103.7526, "region": "West", "pub_monitored": 1},
    "BUKIT PANJANG": {"lat": 1.3662, "lon": 103.7730, "region": "West", "pub_monitored": 1},
    "CHOA CHU KANG": {"lat": 1.3875, "lon": 103.7485, "region": "West", "pub_monitored": 1},
    "CLEMENTI": {"lat": 1.3165, "lon": 103.7604, "region": "West", "pub_monitored": 1},
    "JURONG EAST": {"lat": 1.3251, "lon": 103.7384, "region": "West", "pub_monitored": 1},
    "JURONG WEST": {"lat": 1.3440, "lon": 103.7048, "region": "West", "pub_monitored": 1},
    "PIONEER": {"lat": 1.3099, "lon": 103.6693, "region": "West", "pub_monitored": 0},
    "TENGAH": {"lat": 1.3621, "lon": 103.7252, "region": "West", "pub_monitored": 0},
    "TUAS": {"lat": 1.2817, "lon": 103.6342, "region": "West", "pub_monitored": 0},
    "WESTERN ISLANDS": {"lat": 1.2600, "lon": 103.6702, "region": "West", "pub_monitored": 0},
    "WESTERN WATER CATCHMENT": {
        "lat": 1.3808,
        "lon": 103.6956,
        "region": "West",
        "pub_monitored": 0,
    },
}


def load_station_snapshot(path: Path | None = None) -> dict[str, dict]:
    """
    NEA rainfall stations from the reference snapshot of data.gov.sg station metadata.

    Returns ``{station_id: {"name", "lat", "lon"}}``. Live and replay data carry their own station
    metadata (see ``RainfallSnapshot.stations``); this snapshot is the fallback for code that needs
    a fixed station set, such as exporting IDW weights for the Databricks pipeline.
    """
    raw = json.loads((path or settings.station_snapshot_file).read_text())
    return {
        s["id"]: {"name": s["name"], "lat": s["latitude"], "lon": s["longitude"]}
        for s in raw["stations"]
    }


def create_singapore_geojson() -> dict:
    """
    Load official URA Master Plan 2019 Planning Area GeoJSON feature collection.
    """
    if settings.zone_polygons_file.exists():
        return json.loads(settings.zone_polygons_file.read_text())
    raise FileNotFoundError(f"Missing {settings.zone_polygons_file}")
