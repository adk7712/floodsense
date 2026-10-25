"""
FloodSense - Decode NEA rain-area radar frames into rain rates per pixel and per planning area.

Each frame (data.gov.sg ``weather-radar-images``, product ``dpsri``: surface rainfall intensity,
in dBR) is a PNG drawing rain intensity with a fixed 33-colour scale. The colour order comes from
MSS's own legend (weather.gov.sg rain-intensity scale, light → heavy). The rain rate for each colour
comes from calibrating against NEA's gauges (``floodsense.radar.calibrate``) and is stored in
``data/reference/radar_colour_scale.csv``.

Georeference: the API gives the frame's bounding box in EPSG:4326. Over the 70 km frame
(about 140 km square) a linear lon/lat mapping of pixels is accurate to well under a pixel.
"""

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

from floodsense.common.config import settings

# MSS rain-intensity legend, light → heavy. Level 0 is "no echo" (transparent).
COLOUR_SCALE: tuple[tuple[int, int, int], ...] = (
    (0, 255, 255), (0, 239, 239), (0, 209, 213), (0, 186, 191), (0, 151, 154), (0, 131, 125),
    (0, 128, 69), (0, 137, 56), (0, 162, 53), (0, 183, 41), (0, 202, 17), (0, 218, 13),
    (0, 245, 7), (0, 255, 0), (67, 255, 65), (72, 255, 70),
    (255, 255, 59), (255, 255, 0), (255, 240, 0), (255, 220, 0),
    (255, 198, 0), (255, 178, 0), (255, 165, 0), (255, 138, 0), (255, 114, 0), (255, 73, 0),
    (255, 31, 0), (229, 0, 0), (193, 0, 0),
    (182, 0, 106), (210, 0, 165), (212, 0, 170), (255, 0, 255),
)  # fmt: skip
N_LEVELS = len(COLOUR_SCALE)
SCALE_FILE = settings.root_dir / "data" / "reference" / "radar_colour_scale.csv"

_KEYS = np.array([(r << 16) | (g << 8) | b for r, g, b in COLOUR_SCALE], dtype=np.int64)
_SORTED_KEYS = np.sort(_KEYS)
_LEVEL_OF_SORTED = (np.argsort(_KEYS) + 1).astype(np.int16)  # level for each sorted key


@dataclass(frozen=True)
class BoundingBox:
    """Frame extent in EPSG:4326 (the API's ``boundaryBox``)."""

    ul_lon: float
    ul_lat: float
    lr_lon: float
    lr_lat: float

    @classmethod
    def from_row(cls, row: pd.Series) -> "BoundingBox":
        return cls(
            float(row["bbox_ul_lon"]),
            float(row["bbox_ul_lat"]),
            float(row["bbox_lr_lon"]),
            float(row["bbox_lr_lat"]),
        )

    def pixel(self, lat: float, lon: float, shape: tuple[int, int]) -> tuple[int, int]:
        """(row, col) of the pixel containing a point; may fall outside the frame."""
        h, w = shape
        col = int((lon - self.ul_lon) / (self.lr_lon - self.ul_lon) * w)
        row = int((self.ul_lat - lat) / (self.ul_lat - self.lr_lat) * h)
        return row, col

    def centres(self, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """Latitude and longitude of every pixel centre, each of ``shape``."""
        h, w = shape
        lons = self.ul_lon + (np.arange(w) + 0.5) / w * (self.lr_lon - self.ul_lon)
        lats = self.ul_lat - (np.arange(h) + 0.5) / h * (self.ul_lat - self.lr_lat)
        lon_grid, lat_grid = np.meshgrid(lons, lats)
        return lat_grid, lon_grid


def decode_levels(png: bytes) -> np.ndarray:
    """Intensity level per pixel: 0 = no echo, 1..33 = light → heavy. Unknown colours raise."""
    rgba = np.array(Image.open(io.BytesIO(png)).convert("RGBA"))
    key = (
        (rgba[..., 0].astype(np.int64) << 16) | (rgba[..., 1].astype(np.int64) << 8) | rgba[..., 2]
    )
    painted = rgba[..., 3] > 0
    idx = np.clip(np.searchsorted(_SORTED_KEYS, key), 0, N_LEVELS - 1)
    matched = _SORTED_KEYS[idx] == key
    if (painted & ~matched).any():
        bad = sorted({int(k) for k in key[painted & ~matched]})[:5]
        raise ValueError(
            "Radar frame has colours outside the MSS scale: "
            + ", ".join(f"({k >> 16}, {(k >> 8) & 255}, {k & 255})" for k in bad)
        )
    levels = np.zeros(key.shape, dtype=np.int16)
    levels[painted] = _LEVEL_OF_SORTED[idx[painted]]
    return levels


@lru_cache(maxsize=1)
def rate_table(path: Path | None = None) -> np.ndarray:
    """Rain rate (mm/h) indexed by level; index 0 (no echo) is 0."""
    target = path or SCALE_FILE
    if not target.exists():
        raise FileNotFoundError(
            f"{target} is missing; run `python -m floodsense.radar.calibrate` to build it"
        )
    scale = pd.read_csv(target).sort_values("level")
    if list(scale["level"]) != list(range(1, N_LEVELS + 1)):
        raise ValueError(f"{target} must have levels 1..{N_LEVELS}")
    return np.concatenate([[0.0], scale["rate_mm_h"].to_numpy(dtype=float)])


@lru_cache(maxsize=4)
def zone_index(bbox: BoundingBox, shape: tuple[int, int]) -> tuple[np.ndarray, tuple[str, ...]]:
    """Per pixel, the index of the URA planning area containing it (-1 = outside every zone)."""
    import json

    import shapely
    from shapely.geometry import shape as to_shape

    geo = json.loads(settings.zone_polygons_file.read_text())
    names = tuple(str(f["properties"]["name"]).upper() for f in geo["features"])
    lat, lon = bbox.centres(shape)
    out = np.full(shape, -1, dtype=np.int16)
    for i, feature in enumerate(geo["features"]):
        poly = to_shape(feature["geometry"])
        out[shapely.contains_xy(poly, lon, lat)] = i
    return out, names
