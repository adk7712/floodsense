"""Build ``data/reference/zone_static.csv``: static terrain and land-cover features per zone.

Per URA planning area (Master Plan 2019 polygons):

- ``elevation_m_mean``: mean Copernicus GLO-30 pixel value inside the zone, in metres above the
  EGM2008 geoid. GLO-30 is a **digital surface model (DSM)**: it includes buildings and tree
  canopy, so built-up and wooded zones read higher than the bare ground. Treat it as a relative
  terrain index, not a survey-grade ground level.
- ``low_lying_pct``: % of the zone's DEM pixels below ``LOW_LYING_THRESHOLD_M``.
- ``built_up_pct``: % of the zone's valid ESA WorldCover 2021 pixels in class 50 (Built-up).
- ``built_up_land_pct``: % of the zone's valid WorldCover land pixels in class 50, where land is
  every valid pixel except class 80 (permanent water bodies). Pre-registered definition: it
  differs from ``built_up_pct`` only by dropping water from the denominator.

A pixel belongs to a zone when its centre lies inside the polygon. A zone with no valid pixel
raises; nothing is filled in. The rasters (about 80 MB) are downloaded by hand into
``data/raw/static/`` (gitignored) and are not fetched by this script.

    uv run --extra static python -m floodsense.spatial.build_zone_static --retrieved-at 2026-10-16
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

STATIC_DIR = settings.root_dir / "data" / "raw" / "static"
OUTPUT_FILE = settings.root_dir / "data" / "reference" / "zone_static.csv"

DEM_FILES = (
    "Copernicus_DSM_COG_10_N01_00_E103_00_DEM.tif",
    "Copernicus_DSM_COG_10_N01_00_E104_00_DEM.tif",
)
WORLDCOVER_FILE = "ESA_WorldCover_10m_2021_v200_N00E102_Map.tif"

# "Low-lying" = DEM pixel below 5 m above the EGM2008 geoid (roughly mean sea level). 5 m is a
# round, coastal-flood-style cut (Singapore's coastal planning guidance works in similar metres
# above Singapore Height Datum) and, as GLO-30 is a surface model, a deliberately generous one.
# It is a descriptive feature threshold, not tuned against flood labels.
LOW_LYING_THRESHOLD_M = 5.0
WORLDCOVER_BUILT_UP_CLASS = 50
WORLDCOVER_WATER_CLASS = 80
WORLDCOVER_NODATA_CLASS = 0

SOURCE_NAME = "Copernicus GLO-30 DEM; ESA WorldCover 2021 v200"
SOURCE_URL = (
    "https://registry.opendata.aws/copernicus-dem/ ; https://doi.org/10.5281/zenodo.7254221"
)
COLUMNS = [
    "ura_planning_area",
    "built_up_pct",
    "built_up_land_pct",
    "elevation_m_mean",
    "low_lying_pct",
    "source_name",
    "source_url",
    "retrieved_at",
]


def _zone_geometries() -> dict[str, object]:
    from shapely.geometry import shape

    data = json.loads(settings.zone_polygons_file.read_text())
    geoms = {f["properties"]["name"]: shape(f["geometry"]) for f in data["features"]}
    missing = set(URA_PLANNING_AREAS) - set(geoms)
    if missing or len(geoms) != len(URA_PLANNING_AREAS):
        raise ValueError(f"Polygon names do not match URA_PLANNING_AREAS: {sorted(missing)}")
    return geoms


def _zone_pixels(src, geom) -> np.ndarray:  # type: ignore[no-untyped-def]
    """Band-1 values of pixels whose centre is inside ``geom``, with nodata removed."""
    from rasterio.features import geometry_mask
    from rasterio.windows import Window, from_bounds

    left, bottom, right, top = geom.bounds
    b = src.bounds
    left, bottom = max(left, b.left), max(bottom, b.bottom)
    right, top = min(right, b.right), min(top, b.top)
    if left >= right or bottom >= top:
        return np.empty(0)
    win = (
        from_bounds(left, bottom, right, top, transform=src.transform)
        .round_offsets()
        .round_lengths()
    )
    win = win.intersection(Window(0, 0, src.width, src.height))
    if win.width < 1 or win.height < 1:
        return np.empty(0)
    arr = src.read(1, window=win)
    inside = ~geometry_mask(
        [geom], out_shape=arr.shape, transform=src.window_transform(win), all_touched=False
    )
    vals = arr[inside]
    if src.nodata is not None:
        vals = vals[vals != src.nodata]
    return vals[np.isfinite(vals)] if vals.dtype.kind == "f" else vals


def build(static_dir: Path = STATIC_DIR, retrieved_at: str | None = None) -> pd.DataFrame:
    import rasterio

    if retrieved_at is None:
        raise ValueError("retrieved_at (the download date, ISO) is required")
    date.fromisoformat(retrieved_at)
    geoms = _zone_geometries()
    rows = []
    with (
        rasterio.open(static_dir / WORLDCOVER_FILE) as wc,
        rasterio.open(static_dir / DEM_FILES[0]) as d1,
        rasterio.open(static_dir / DEM_FILES[1]) as d2,
    ):
        for zone in URA_PLANNING_AREAS:
            geom = geoms[zone]
            dem = np.concatenate([_zone_pixels(d1, geom), _zone_pixels(d2, geom)]).astype(float)
            lc = _zone_pixels(wc, geom)
            lc = lc[lc != WORLDCOVER_NODATA_CLASS]
            if dem.size == 0 or lc.size == 0:
                raise ValueError(f"{zone}: no valid pixels (DEM={dem.size}, WorldCover={lc.size})")
            land = lc[lc != WORLDCOVER_WATER_CLASS]
            if land.size == 0:
                raise ValueError(f"{zone}: no WorldCover land pixels")
            rows.append(
                {
                    "ura_planning_area": zone,
                    "built_up_pct": round(
                        100.0 * float(np.mean(lc == WORLDCOVER_BUILT_UP_CLASS)), 2
                    ),
                    "built_up_land_pct": round(
                        100.0 * float(np.mean(land == WORLDCOVER_BUILT_UP_CLASS)), 2
                    ),
                    "elevation_m_mean": round(float(dem.mean()), 2),
                    "low_lying_pct": round(100.0 * float(np.mean(dem < LOW_LYING_THRESHOLD_M)), 2),
                    "source_name": SOURCE_NAME,
                    "source_url": SOURCE_URL,
                    "retrieved_at": retrieved_at,
                }
            )
    return pd.DataFrame(rows, columns=COLUMNS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--retrieved-at", required=True, help="Download date of the rasters (ISO).")
    parser.add_argument("--static-dir", type=Path, default=STATIC_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    args = parser.parse_args()
    df = build(args.static_dir, args.retrieved_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"Wrote {len(df)} zones to {args.output}")


if __name__ == "__main__":
    main()
