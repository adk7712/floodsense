"""
FloodSense - Singapore Geospatial Reference Data.
Planning-area centroids for the 55 URA Planning Areas, and NEA rainfall stations loaded from the
reference snapshot of data.gov.sg station metadata.
"""

import json
from pathlib import Path

from floodsense.common.config import settings

# 55 URA Planning Areas with Centroids (lat, lon) and Region
URA_PLANNING_AREAS: dict[str, dict] = {
    # Central Region
    "BISHAN": {"lat": 1.3508, "lon": 103.8485, "region": "Central", "pub_monitored": 1},
    "BUKIT MERAH": {"lat": 1.2819, "lon": 103.8239, "region": "Central", "pub_monitored": 1},
    "BUKIT TIMAH": {"lat": 1.3294, "lon": 103.7763, "region": "Central", "pub_monitored": 1},
    "DOWNTOWN CORE": {"lat": 1.2868, "lon": 103.8545, "region": "Central", "pub_monitored": 1},
    "GEYLANG": {"lat": 1.3182, "lon": 103.8871, "region": "Central", "pub_monitored": 1},
    "KALLANG": {"lat": 1.3108, "lon": 103.8647, "region": "Central", "pub_monitored": 1},
    "MARINA EAST": {"lat": 1.2882, "lon": 103.8744, "region": "Central", "pub_monitored": 0},
    "MARINA SOUTH": {"lat": 1.2721, "lon": 103.8624, "region": "Central", "pub_monitored": 0},
    "MARINE PARADE": {"lat": 1.3020, "lon": 103.9073, "region": "Central", "pub_monitored": 1},
    "MUSEUM": {"lat": 1.2966, "lon": 103.8492, "region": "Central", "pub_monitored": 0},
    "NEWTON": {"lat": 1.3130, "lon": 103.8378, "region": "Central", "pub_monitored": 1},
    "NOVENA": {"lat": 1.3204, "lon": 103.8436, "region": "Central", "pub_monitored": 1},
    "ORCHARD": {"lat": 1.3048, "lon": 103.8318, "region": "Central", "pub_monitored": 1},
    "OUTRAM": {"lat": 1.2827, "lon": 103.8392, "region": "Central", "pub_monitored": 1},
    "QUEENSTOWN": {"lat": 1.2942, "lon": 103.7861, "region": "Central", "pub_monitored": 1},
    "RIVER VALLEY": {"lat": 1.2938, "lon": 103.8344, "region": "Central", "pub_monitored": 1},
    "ROCHOR": {"lat": 1.3039, "lon": 103.8557, "region": "Central", "pub_monitored": 1},
    "SINGAPORE RIVER": {"lat": 1.2891, "lon": 103.8433, "region": "Central", "pub_monitored": 1},
    "SOUTHERN ISLANDS": {"lat": 1.2464, "lon": 103.8430, "region": "Central", "pub_monitored": 0},
    "STRAITS VIEW": {"lat": 1.2678, "lon": 103.8569, "region": "Central", "pub_monitored": 0},
    "TANGLIN": {"lat": 1.3060, "lon": 103.8126, "region": "Central", "pub_monitored": 1},
    "TOA PAYOH": {"lat": 1.3343, "lon": 103.8563, "region": "Central", "pub_monitored": 1},
    # East Region
    "BEDOK": {"lat": 1.3236, "lon": 103.9273, "region": "East", "pub_monitored": 1},
    "CHANGI": {"lat": 1.3595, "lon": 103.9892, "region": "East", "pub_monitored": 1},
    "CHANGI BAY": {"lat": 1.3160, "lon": 104.0200, "region": "East", "pub_monitored": 0},
    "PASIR RIS": {"lat": 1.3721, "lon": 103.9474, "region": "East", "pub_monitored": 1},
    "PAYA LEBAR": {"lat": 1.3582, "lon": 103.8914, "region": "East", "pub_monitored": 1},
    "TAMPINES": {"lat": 1.3541, "lon": 103.9439, "region": "East", "pub_monitored": 1},
    # North Region
    "CENTRAL WATER CATCHMENT": {
        "lat": 1.3734,
        "lon": 103.8078,
        "region": "North",
        "pub_monitored": 1,
    },
    "LIM CHU KANG": {"lat": 1.4342, "lon": 103.7013, "region": "North", "pub_monitored": 0},
    "MANDAI": {"lat": 1.4080, "lon": 103.7863, "region": "North", "pub_monitored": 1},
    "NORTH-EASTERN ISLANDS": {
        "lat": 1.4117,
        "lon": 103.9870,
        "region": "North",
        "pub_monitored": 0,
    },
    "SEMBAWANG": {"lat": 1.4491, "lon": 103.8185, "region": "North", "pub_monitored": 1},
    "SIMPANG": {"lat": 1.4312, "lon": 103.8402, "region": "North", "pub_monitored": 0},
    "SUNGEI KADUT": {"lat": 1.4153, "lon": 103.7466, "region": "North", "pub_monitored": 1},
    "WOODLANDS": {"lat": 1.4382, "lon": 103.7890, "region": "North", "pub_monitored": 1},
    "YISHUN": {"lat": 1.4304, "lon": 103.8354, "region": "North", "pub_monitored": 1},
    # North-East Region
    "ANG MO KIO": {"lat": 1.3691, "lon": 103.8454, "region": "North-East", "pub_monitored": 1},
    "HOUGANG": {"lat": 1.3708, "lon": 103.8893, "region": "North-East", "pub_monitored": 1},
    "PUNGGOL": {"lat": 1.4011, "lon": 103.9073, "region": "North-East", "pub_monitored": 1},
    "SELETAR": {"lat": 1.4098, "lon": 103.8714, "region": "North-East", "pub_monitored": 0},
    "SENGKANG": {"lat": 1.3868, "lon": 103.8914, "region": "North-East", "pub_monitored": 1},
    "SERANGOON": {"lat": 1.3554, "lon": 103.8679, "region": "North-East", "pub_monitored": 1},
    # West Region
    "BOON LAY": {"lat": 1.3175, "lon": 103.7025, "region": "West", "pub_monitored": 0},
    "BUKIT BATOK": {"lat": 1.3590, "lon": 103.7637, "region": "West", "pub_monitored": 1},
    "BUKIT PANJANG": {"lat": 1.3774, "lon": 103.7719, "region": "West", "pub_monitored": 1},
    "CHOA CHU KANG": {"lat": 1.3840, "lon": 103.7470, "region": "West", "pub_monitored": 1},
    "CLEMENTI": {"lat": 1.3162, "lon": 103.7649, "region": "West", "pub_monitored": 1},
    "JURONG EAST": {"lat": 1.3329, "lon": 103.7436, "region": "West", "pub_monitored": 1},
    "JURONG WEST": {"lat": 1.3404, "lon": 103.7090, "region": "West", "pub_monitored": 1},
    "PIONEER": {"lat": 1.3142, "lon": 103.6840, "region": "West", "pub_monitored": 0},
    "TENGAH": {"lat": 1.3644, "lon": 103.7314, "region": "West", "pub_monitored": 0},
    "TUAS": {"lat": 1.2988, "lon": 103.6360, "region": "West", "pub_monitored": 0},
    "WESTERN ISLANDS": {"lat": 1.2405, "lon": 103.7220, "region": "West", "pub_monitored": 0},
    "WESTERN WATER CATCHMENT": {
        "lat": 1.3912,
        "lon": 103.6886,
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


NEA_WEATHER_STATIONS: dict[str, dict] = load_station_snapshot()


def create_singapore_geojson() -> dict:
    """
    Generate synthetic polygon GeoJSON feature collection for the 55 URA Planning Areas.
    Uses circular/approximated polygonal bounds around each centroid.
    """
    features = []
    import math

    for zone_name, data in URA_PLANNING_AREAS.items():
        lat, lon = data["lat"], data["lon"]
        radius_km = 1.8  # ~2km radius cell approximation
        points = []
        for angle in range(0, 360, 30):
            rad = math.radians(angle)
            # 1 deg lat ~ 111 km, 1 deg lon ~ 111 * cos(lat) km
            dlat = (radius_km / 111.0) * math.cos(rad)
            dlon = (radius_km / (111.0 * math.cos(math.radians(lat)))) * math.sin(rad)
            points.append([lon + dlon, lat + dlat])
        points.append(points[0])  # close ring

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": zone_name,
                    "region": data["region"],
                    "pub_monitored": data["pub_monitored"],
                },
                "geometry": {"type": "Polygon", "coordinates": [points]},
            }
        )

    return {"type": "FeatureCollection", "features": features}
