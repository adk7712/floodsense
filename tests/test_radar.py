import io

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from floodsense.radar.decode import (
    COLOUR_SCALE,
    N_LEVELS,
    BoundingBox,
    decode_levels,
    decode_rates,
    rate_table,
    zone_rain,
)

# The 70 km frame's box, as the API returns it.
BOX = BoundingBox(ul_lon=103.342685, ul_lat=1.97854, lr_lon=104.602315, lr_lat=0.719515)


def _png(pixels: dict[tuple[int, int], tuple[int, int, int]], size: int = 480) -> bytes:
    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    for (row, col), rgb in pixels.items():
        img.putpixel((col, row), (*rgb, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_scale_file_is_complete_and_increasing():
    rates = rate_table()
    assert len(rates) == N_LEVELS + 1 and rates[0] == 0
    assert np.all(np.diff(rates[1:]) > 0)  # heavier colour, heavier rain
    scale = pd.read_csv("data/reference/radar_colour_scale.csv")
    assert [tuple(x) for x in scale[["r", "g", "b"]].to_numpy()] == list(COLOUR_SCALE)


def test_colours_decode_to_levels_and_rates():
    png = _png({(0, 0): COLOUR_SCALE[0], (1, 1): COLOUR_SCALE[-1], (2, 2): COLOUR_SCALE[16]})
    levels = decode_levels(png)
    assert levels[0, 0] == 1 and levels[1, 1] == N_LEVELS and levels[2, 2] == 17
    assert levels[3, 3] == 0  # transparent = no echo, not dry-by-default anywhere else
    rates = decode_rates(png)
    assert rates[3, 3] == 0 and rates[1, 1] > 100 > rates[0, 0]


def test_unknown_colour_raises():
    with pytest.raises(ValueError, match="outside the MSS scale"):
        decode_levels(_png({(0, 0): (12, 34, 56)}))


def test_georeference_puts_the_radar_centre_mid_frame():
    row, col = BOX.pixel(1.34911, 103.972583, (480, 480))  # projection centre from the API
    assert abs(row - 240) <= 1 and abs(col - 240) <= 1


def test_zone_rain_finds_rain_over_its_zone():
    lat, lon = 1.4382, 103.7890  # Woodlands
    r, c = BOX.pixel(lat, lon, (480, 480))
    heavy = COLOUR_SCALE[-1]
    png = _png({(r + dr, c + dc): heavy for dr in range(-3, 4) for dc in range(-3, 4)})
    z = zone_rain(png, BOX).set_index("ura_planning_area")
    assert len(z) == 55
    assert z.loc["WOODLANDS", "radar_max_mm_h"] > 100
    assert z.loc["BEDOK", "radar_max_mm_h"] == 0
