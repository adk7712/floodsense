"""The data.gov.sg fallback survives rate limits (30 Sep 2026: the +1 h layer went blank)."""

from collections import OrderedDict
from datetime import datetime

import pytest

from floodsense.common.config import settings
from floodsense.radar import live

from .test_radar_live import BBOX, _png


class _Resp:
    def __init__(self, status, payload=None, content=b""):
        self.status_code, self._payload, self.content = status, payload, content

    def json(self):
        return self._payload


def _fake_api(rate_limited_first: int):
    """Three frames 14:50-15:00; the first ``rate_limited_first`` calls answer 429."""
    calls = {"n": 0}
    recs = [
        {"timestamp": f"2026-09-30T14:{m}:00+08:00", "image": {"url": f"http://img/{m}"}}
        for m in (50, 55)
    ] + [{"timestamp": "2026-09-30T15:00:00+08:00", "image": {"url": "http://img/00"}}]
    box = {
        "upperLeft": {"longitude": BBOX["bbox_ul_lon"], "latitude": BBOX["bbox_ul_lat"]},
        "lowerRight": {"longitude": BBOX["bbox_lr_lon"], "latitude": BBOX["bbox_lr_lat"]},
    }

    def get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] <= rate_limited_first:
            return _Resp(429)
        if url == live.API:
            return _Resp(200, {"data": {"records": recs[::-1], "boundaryBox": box}})
        return _Resp(200, content=_png(25, 200))

    return get, calls


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "_API_PNGS", OrderedDict())
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    monkeypatch.setattr(settings, "radar_volume", None)


WHEN = datetime(2026, 9, 30, 15, 0, tzinfo=settings.tzinfo)


def test_a_rate_limit_is_retried_not_treated_as_no_radar(monkeypatch, tmp_path):
    get, calls = _fake_api(rate_limited_first=1)
    monkeypatch.setattr(live.requests, "get", get)
    frames = live.recent_frames(WHEN, tmp_path)
    assert frames is not None and len(frames) == 3


def test_a_persistent_rate_limit_gives_no_view_never_a_guess(monkeypatch, tmp_path):
    get, _ = _fake_api(rate_limited_first=99)
    monkeypatch.setattr(live.requests, "get", get)
    assert live.recent_frames(WHEN, tmp_path) is None


def test_a_failed_image_download_is_not_decoded_as_a_frame(monkeypatch, tmp_path):
    get, _ = _fake_api(rate_limited_first=0)

    def flaky(url, **kw):
        return _Resp(500) if url.startswith("http://img") else get(url, **kw)

    monkeypatch.setattr(live.requests, "get", flaky)
    assert live.recent_frames(WHEN, tmp_path) is None


def test_images_are_kept_so_the_next_view_needs_fewer_requests(monkeypatch, tmp_path):
    get, calls = _fake_api(rate_limited_first=0)
    monkeypatch.setattr(live.requests, "get", get)
    live.recent_frames(WHEN, tmp_path)
    first = calls["n"]
    live.recent_frames(WHEN, tmp_path)
    assert calls["n"] - first == 1  # just the listing page; the three images are cached


def test_the_image_cache_is_capped(monkeypatch):
    monkeypatch.setattr(live, "API_PNG_CACHE_MAX", 3)
    monkeypatch.setattr(live.requests, "get", lambda url, **kw: _Resp(200, content=url.encode()))
    for i in range(5):
        live._api_png(f"http://img/{i}")
    live._api_png("http://img/2")  # a hit moves it to the newest end
    live._api_png("http://img/5")
    assert list(live._API_PNGS) == ["http://img/4", "http://img/2", "http://img/5"]
