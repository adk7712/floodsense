import io
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from floodsense.common.config import settings
from floodsense.models.scoring import score_zone_features
from floodsense.radar.decode import COLOUR_SCALE, decode_levels, rate_table

pytest.importorskip("cv2")

from floodsense.radar.live import (  # noqa: E402
    forecast_features,
    radar_view,
    recent_frames,
    render_png,
)

BBOX = {
    "bbox_ul_lon": 103.342685,
    "bbox_ul_lat": 1.97854,
    "bbox_lr_lon": 104.602315,
    "bbox_lr_lat": 0.719515,
}


def _png(level_block: int, col0: int) -> bytes:
    img = Image.new("RGBA", (480, 480), (255, 255, 255, 0))
    for r in range(200, 230):
        for c in range(col0, col0 + 30):
            img.putpixel((c, r), (*COLOUR_SCALE[level_block - 1], 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def archive(tmp_path):
    """A tiny archive day: a storm moving 3 px east per frame, 12:00-12:10 SGT."""
    rows = [
        {"timestamp_sgt": f"2026-09-30T12:{m:02d}:00+08:00", "png": _png(25, 200 + 3 * i), **BBOX}
        for i, m in enumerate([0, 5, 10])
    ]
    day = tmp_path / "70km" / "date=2026-09-30"
    day.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(day / "frames.parquet")
    return tmp_path


def test_rendered_image_decodes_back_to_the_same_levels():
    rates = np.zeros((480, 480))
    rates[10:20, 10:20] = rate_table()[25]
    levels = decode_levels(render_png(rates))
    assert (levels[10:20, 10:20] == 25).all() and levels[0, 0] == 0


def test_recent_frames_need_three_consecutive(archive):
    when = datetime(2026, 9, 30, 12, 10, tzinfo=settings.tzinfo)
    frames = recent_frames(when, archive)
    assert [f["t"].strftime("%H:%M") for f in frames] == ["12:00", "12:05", "12:10"]
    early = datetime(2026, 9, 30, 12, 5, tzinfo=settings.tzinfo)
    assert recent_frames(early, archive) is None  # only two frames up to 12:05


def test_radar_view_moves_the_storm_and_gives_zone_rain(archive):
    view = radar_view(datetime(2026, 9, 30, 12, 10, tzinfo=settings.tzinfo), archive)
    assert set(view.images) == {"Now", "+30 min", "+60 min"}
    assert len(view.zones) == 55 and view.zones.nowcast_60_mm.max() > 0
    now = decode_levels(view.images["Now"]) > 0
    later = decode_levels(view.images["+30 min"]) > 0
    assert np.argwhere(later)[:, 1].mean() > np.argwhere(now)[:, 1].mean() + 10  # moved east


def test_no_radar_for_times_before_the_archive():
    assert radar_view(datetime(2021, 4, 17, 12, 45, tzinfo=settings.tzinfo)) is None


def test_forecast_features_swap_in_next_hour_rain():
    f = pd.DataFrame(
        {"ura_planning_area": ["A", "B"], "rain_60m": [5.0, 0.0], "rain_120m": [6.0, 0.0]}
    )
    z = pd.DataFrame({"ura_planning_area": ["A"], "nowcast_60_mm": [40.0]})
    out = forecast_features(f, z).set_index("ura_planning_area")
    assert out.loc["A", "rain_60m"] == 40 and out.loc["A", "rain_120m"] == 45
    # No radar for a zone means the forecast is unknown, not a dry hour.
    assert pd.isna(out.loc["B", "rain_60m"]) and pd.isna(out.loc["B", "rain_120m"])


def test_zone_without_a_nowcast_scores_no_data_not_low():
    f = pd.DataFrame(
        {
            "ura_planning_area": ["A", "B"],
            "rain_60m": [45.0, 45.0],
            "rain_120m": [60.0, 60.0],
            "rain_30m": [30.0, 30.0],
        }
    )
    z = pd.DataFrame({"ura_planning_area": ["A", "B"], "nowcast_60_mm": [40.0, np.nan]})
    out = score_zone_features(forecast_features(f, z), None).set_index("ura_planning_area")
    assert out.loc["B", "risk_tier"] == "No data" and np.isnan(out.loc["B", "flood_probability"])
    assert out.loc["A", "risk_tier"] != "No data"


# --- The Databricks radar volume (how the Databricks App gets frames) ---------------------------


class _FakeFiles:
    """The Files API: serves the archive fixture's day files from a fake volume path."""

    def __init__(self, archive):
        self.archive, self.calls = archive, []

    def download(self, path):
        from types import SimpleNamespace

        self.calls.append(path)
        rel = path.removeprefix("/Volumes/workspace/floodsense/radar/")
        f = self.archive / rel
        if not f.exists():
            raise FileNotFoundError(path)  # the SDK raises NotFound
        return SimpleNamespace(contents=io.BytesIO(f.read_bytes()))


def test_frames_come_from_the_volume_when_there_is_no_local_archive(archive, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from floodsense.radar import live

    files = _FakeFiles(archive)
    monkeypatch.setattr(settings, "radar_volume", "/Volumes/workspace/floodsense/radar")
    monkeypatch.setattr(live, "_volume_client", SimpleNamespace(files=files))
    monkeypatch.setattr(live, "_VOLUME_DAYS", {})
    monkeypatch.setattr(
        live, "_api_day", lambda *a, **k: pytest.fail("the API shouldn't be needed")
    )

    empty_local = tmp_path / "no_local_archive"
    when = datetime(2026, 9, 30, 12, 10, tzinfo=settings.tzinfo)
    frames = recent_frames(when, empty_local)
    assert [f["t"].strftime("%H:%M") for f in frames] == ["12:00", "12:05", "12:10"]
    assert "/Volumes/workspace/floodsense/radar/70km/date=2026-09-30/frames.parquet" in files.calls

    n = len(files.calls)
    recent_frames(when, empty_local)  # the day is kept: no second download
    assert (
        files.calls.count("/Volumes/workspace/floodsense/radar/70km/date=2026-09-30/frames.parquet")
        == 1
        and len(files.calls) >= n
    )


def test_a_day_missing_from_the_volume_is_retried_later(archive, monkeypatch):
    from types import SimpleNamespace

    from floodsense.radar import live

    files = _FakeFiles(archive)
    monkeypatch.setattr(settings, "radar_volume", "/Volumes/workspace/floodsense/radar")
    monkeypatch.setattr(live, "_volume_client", SimpleNamespace(files=files))
    monkeypatch.setattr(live, "_VOLUME_DAYS", {})
    assert live._volume_day("2026-10-01") is None
    assert live._volume_day("2026-10-01") is None
    assert len(files.calls) == 2  # misses aren't remembered


def test_no_volume_configured_means_no_volume_reads(monkeypatch):
    from floodsense.radar import live

    monkeypatch.setattr(settings, "radar_volume", None)
    assert live._volume_day("2026-09-30") is None
