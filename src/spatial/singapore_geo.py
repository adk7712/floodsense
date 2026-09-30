"""
FloodSense - Singapore Geospatial Reference Data.
Contains official definitions for 55 URA Planning Areas and ~55 NEA Automated Weather Stations.
"""

from typing import Dict, List, Tuple
from shapely.geometry import Point, Polygon
import json

# 55 URA Planning Areas with Centroids (lat, lon) and Region
URA_PLANNING_AREAS: Dict[str, Dict] = {
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
    "CENTRAL WATER CATCHMENT": {"lat": 1.3734, "lon": 103.8078, "region": "North", "pub_monitored": 1},
    "LIM CHU KANG": {"lat": 1.4342, "lon": 103.7013, "region": "North", "pub_monitored": 0},
    "MANDAI": {"lat": 1.4080, "lon": 103.7863, "region": "North", "pub_monitored": 1},
    "NORTH-EASTERN ISLANDS": {"lat": 1.4117, "lon": 103.9870, "region": "North", "pub_monitored": 0},
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
    "WESTERN WATER CATCHMENT": {"lat": 1.3912, "lon": 103.6886, "region": "West", "pub_monitored": 0},
}

# ~55 NEA Automated Weather Stations across Singapore
NEA_WEATHER_STATIONS: Dict[str, Dict] = {
    "S06": {"name": "Paya Lebar", "lat": 1.3584, "lon": 103.9057},
    "S07": {"name": "Macritchie Reservoir", "lat": 1.3417, "lon": 103.8338},
    "S08": {"name": "Lower Peirce Reservoir", "lat": 1.3701, "lon": 103.8271},
    "S11": {"name": "Choa Chu Kang (Central)", "lat": 1.3819, "lon": 103.7386},
    "S24": {"name": "Changi Climate Station", "lat": 1.3678, "lon": 103.9826},
    "S29": {"name": "Pasir Ris (West)", "lat": 1.3863, "lon": 103.9412},
    "S33": {"name": "Jurong Pier Road", "lat": 1.3081, "lon": 103.7100},
    "S35": {"name": "Ulu Pandan", "lat": 1.3329, "lon": 103.7800},
    "S36": {"name": "Mandai", "lat": 1.4130, "lon": 103.7900},
    "S40": {"name": "Mandai Road", "lat": 1.4044, "lon": 103.7894},
    "S43": {"name": "Kim Chuan Road", "lat": 1.3399, "lon": 103.8878},
    "S44": {"name": "Nanyang Avenue", "lat": 1.3458, "lon": 103.6817},
    "S46": {"name": "Dhoby Ghaut", "lat": 1.2994, "lon": 103.8461},
    "S50": {"name": "Clementi Road", "lat": 1.3337, "lon": 103.7768},
    "S60": {"name": "Sentosa", "lat": 1.2500, "lon": 103.8279},
    "S64": {"name": "Ang Mo Kio Ave 5", "lat": 1.3764, "lon": 103.8492},
    "S66": {"name": "Kranji Way", "lat": 1.4387, "lon": 103.7363},
    "S69": {"name": "Upper Peirce Reservoir", "lat": 1.3700, "lon": 103.8050},
    "S71": {"name": "Kent Ridge", "lat": 1.2923, "lon": 103.7815},
    "S77": {"name": "Queenstown", "lat": 1.2937, "lon": 103.8125},
    "S78": {"name": "Tanjong Katong", "lat": 1.3070, "lon": 103.8906},
    "S79": {"name": "Somerset Road", "lat": 1.3004, "lon": 103.8372},
    "S81": {"name": "Punggol Central", "lat": 1.4029, "lon": 103.9094},
    "S84": {"name": "Simei", "lat": 1.3437, "lon": 103.9534},
    "S88": {"name": "Toa Payoh North", "lat": 1.3422, "lon": 103.8489},
    "S89": {"name": "Tuas South Ave 3", "lat": 1.3199, "lon": 103.6350},
    "S90": {"name": "Bukit Panjang", "lat": 1.3746, "lon": 103.7600},
    "S92": {"name": "Kallang Basin", "lat": 1.3133, "lon": 103.8622},
    "S94": {"name": "Holland Road", "lat": 1.3175, "lon": 103.7981},
    "S100": {"name": "Jurong West St 42", "lat": 1.3517, "lon": 103.7198},
    "S104": {"name": "Admiralty", "lat": 1.4438, "lon": 103.7853},
    "S106": {"name": "Pulau Ubin", "lat": 1.4166, "lon": 103.9673},
    "S107": {"name": "East Coast Parkway", "lat": 1.3135, "lon": 103.9619},
    "S108": {"name": "Marina Barrage", "lat": 1.2799, "lon": 103.8703},
    "S109": {"name": "Ang Mo Kio Ave 8", "lat": 1.3606, "lon": 103.8542},
    "S111": {"name": "Scotts Road", "lat": 1.3087, "lon": 103.8344},
    "S112": {"name": "Lim Chu Kang Rd", "lat": 1.4385, "lon": 103.7014},
    "S113": {"name": "Sembawang", "lat": 1.4556, "lon": 103.8250},
    "S114": {"name": "Yishun Ave 7", "lat": 1.4330, "lon": 103.8390},
    "S115": {"name": "Tuas South St 7", "lat": 1.2935, "lon": 103.6184},
    "S116": {"name": "West Coast Highway", "lat": 1.2810, "lon": 103.7540},
    "S117": {"name": "Banyan Road (Jurong Is)", "lat": 1.2560, "lon": 103.6790},
    "S118": {"name": "Pasir Panjang Rd", "lat": 1.2790, "lon": 103.7910},
    "S119": {"name": "Zion Road", "lat": 1.2917, "lon": 103.8300},
    "S120": {"name": "Old Choa Chu Kang", "lat": 1.3712, "lon": 103.7225},
    "S121": {"name": "Old Toh Tuck Rd", "lat": 1.3411, "lon": 103.7580},
    "S122": {"name": "Sengkang East Way", "lat": 1.3900, "lon": 103.8967},
    "S123": {"name": "Bukit Timah Road", "lat": 1.3250, "lon": 103.7850},
}


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

        features.append({
            "type": "Feature",
            "properties": {
                "name": zone_name,
                "region": data["region"],
                "pub_monitored": data["pub_monitored"]
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [points]
            }
        })

    return {
        "type": "FeatureCollection",
        "features": features
    }
