"""
FloodSense - Download and normalise URA Master Plan 2019 Planning Area boundaries.

Source: data.gov.sg dataset 'Master Plan 2019 Planning Area Boundary (No Sea)'
Dataset ID: d_4765db0e87b9c86336792efe8a1f7a66
Target: data/reference/ura_planning_areas_mp2019.geojson
"""

import json
import logging
from pathlib import Path

import requests
from shapely.geometry import mapping, shape
from shapely.validation import make_valid

from floodsense.common.config import settings

logger = logging.getLogger("FloodSense.DownloadPolygons")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DATASET_INITIATE_URL = "https://api-open.data.gov.sg/v1/public/api/datasets/d_4765db0e87b9c86336792efe8a1f7a66/initiate-download"


def download_and_normalise_ura_polygons(out_path: Path = settings.zone_polygons_file) -> Path:
    """
    Downloads official URA Master Plan 2019 GeoJSON from data.gov.sg and normalises properties.
    Ensures properties.name is uppercase matching the 55 URA planning area keys and geometries are valid.
    """
    logger.info("Initiating download for Master Plan 2019 Planning Area Boundary from data.gov.sg...")
    resp = requests.get(DATASET_INITIATE_URL, timeout=15)
    resp.raise_for_status()
    init_data = resp.json()

    if init_data.get("code") != 0 or "data" not in init_data or "url" not in init_data["data"]:
        raise RuntimeError(f"Unexpected initiate-download response: {init_data}")

    download_url = init_data["data"]["url"]
    logger.info("Downloading raw GeoJSON payload...")
    geo_resp = requests.get(download_url, timeout=30)
    geo_resp.raise_for_status()

    raw_geojson = geo_resp.json()
    features = raw_geojson.get("features", [])
    logger.info("Downloaded %d raw features.", len(features))

    normalised_features = []
    seen_names = set()

    for feat in features:
        props = feat.get("properties", {})
        raw_name = props.get("PLN_AREA_N", props.get("name", "")).strip().upper()
        if not raw_name:
            continue

        geom = shape(feat["geometry"])
        if not geom.is_valid:
            geom = make_valid(geom)

        new_props = {
            "name": raw_name,
            "region": props.get("REGION_N", "").replace(" REGION", "").title(),
            "planning_area_code": props.get("PLN_AREA_C", ""),
            "object_id": props.get("OBJECTID", 0),
        }

        normalised_features.append({
            "type": "Feature",
            "properties": new_props,
            "geometry": mapping(geom)
        })
        seen_names.add(raw_name)

    normalised_geojson = {
        "type": "FeatureCollection",
        "source": "URA Master Plan 2019 Planning Area Boundary (No Sea) via data.gov.sg (d_4765db0e87b9c86336792efe8a1f7a66)",
        "features": normalised_features
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(normalised_geojson, indent=2))
    logger.info("Saved %d normalised planning area features to %s", len(normalised_features), out_path)
    return out_path


if __name__ == "__main__":
    download_and_normalise_ura_polygons()
