"""
FloodSense - Streamlit app tests (in-process, via streamlit.testing.v1.AppTest).

Live mode is exercised with the network patched out, so these tests run offline.
"""

from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from floodsense.app.replay_days import store_available

pytestmark = pytest.mark.integration

APP_PATH = str(Path(__file__).parent.parent / "src" / "floodsense" / "app" / "streamlit_app.py")


@pytest.fixture
def offline(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr("time.sleep", lambda *_: None)


def _app(mode: str = "Replay Storm") -> AppTest:
    """The app in ``mode`` (it opens in Live Feed by default; most tests replay real storms)."""
    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = mode
    at.run(timeout=60)
    assert not at.exception
    return at


def test_streamlit_app_default_render():
    at = _app()
    assert "FloodSense" in at.title[0].value
    assert at.select_slider[0].value == "12:15"  # opens at the storm peak
    assert "/ 55" in at.metric[0].value


def test_streamlit_app_mode_toggle(offline):
    at = _app()
    at.sidebar.button_group[0].set_value("Live Feed")
    at.run(timeout=60)
    assert not at.exception

    at.sidebar.button_group[0].set_value("Replay Storm")
    at.run(timeout=60)
    assert not at.exception
    # 5 KPI cards, plus 3 transport counters: 12:15 on 17 Apr 2021 has Moderate areas with MRT.
    assert len(at.metric) == 8


def test_streamlit_app_replay_scrubbing_changes_the_view():
    # Widget handles go stale after at.run(), so re-fetch them before every interaction.
    at = _app()
    at.select_slider[0].set_value("11:00")
    at.run(timeout=30)
    before = at.metric[2].value  # max 30-min rain

    at.select_slider[0].set_value("12:15")
    at.run(timeout=30)
    peak = at.metric[2].value

    assert not at.exception
    assert float(peak.split()[0]) > float(before.split()[0])


def test_streamlit_app_all_zones_selection():
    at = _app()
    for zone in ["BUKIT TIMAH", "BEDOK", "JURONG WEST", "PUNGGOL", "DOWNTOWN CORE"]:
        at.selectbox[0].set_value(zone)
        at.run(timeout=30)
        assert not at.exception
        assert at.selectbox[0].value == zone


def test_diagnostics_use_real_model_and_current_status():
    at = _app()
    labels = [m.label for m in at.metric]
    assert "Wet-Ground Index" in labels and "Wet Ground (72h)" not in labels
    text = " ".join(md.value for md in at.markdown) + " ".join(c.value for c in at.caption)
    assert "placeholder" not in text and "boundary polygons to come" not in text
    assert "60.8M readings" in text and "High from" in text
    assert "DAISI Challenge" not in " ".join(c.value for c in at.sidebar.caption)


needs_store = pytest.mark.skipif(not store_available(), reason="rainfall store not present")


@needs_store
def test_replay_any_date_and_featured_storms():
    from datetime import date

    at = _app()
    assert at.sidebar.date_input[0].value == date(2021, 4, 17)
    text = " ".join(md.value for md in at.markdown)
    assert "Reported floods this day" in text and "Dunearn" in text

    storm = next(b for b in at.sidebar.button if b.label.startswith("22 Nov 2024"))
    storm.click()
    at.run(timeout=60)
    assert not at.exception
    assert at.sidebar.date_input[0].value == date(2024, 11, 22)
    assert "22 Nov 2024" in at.sidebar.select_slider[0].label
    assert "Yishun" in " ".join(md.value for md in at.markdown)

    at.sidebar.date_input[0].set_value(date(2019, 7, 15))
    at.run(timeout=60)
    assert not at.exception
    assert "none in our 66 sourced events" in " ".join(md.value for md in at.markdown)


@needs_store
def test_gap_and_patchy_days_are_flagged_not_shown_as_dry():
    from datetime import date

    at = _app()
    at.sidebar.date_input[0].set_value(date(2018, 2, 8))  # no NEA readings at all
    at.run(timeout=60)
    assert not at.exception
    assert any("no rain-gauge readings" in w.value for w in at.warning)
    assert len(at.metric) == 0  # nothing rendered as if it were dry

    at.sidebar.date_input[0].set_value(date(2021, 1, 1))  # readings stop at 07:55
    at.run(timeout=60)
    assert any("patchy" in w.value for w in at.warning)
    assert len(at.metric) == 5


def test_without_the_store_the_recorded_storm_still_replays(monkeypatch):
    from floodsense.app import replay_days

    monkeypatch.setattr(replay_days, "store_available", lambda: False)
    at = _app()
    assert len(at.sidebar.date_input) == 0
    assert at.select_slider[0].value == "12:15"
    assert "Dunearn" in " ".join(md.value for md in at.markdown)


def test_opens_in_live_mode_by_default(offline):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=60)
    assert not at.exception
    assert at.sidebar.button_group[0].value == "Live Feed"
    assert any(
        "Live feed unavailable" in e.value for e in at.error
    )  # offline: says so, no fake rain


@needs_store
def test_replay_time_slider_sits_above_the_storm_buttons():
    at = _app()
    order = [type(el).__name__ for el in at.sidebar]
    assert order.index("SelectSlider") < order.index("Button")


def test_every_button_works():
    at = _app()
    labels = [b.label for b in at.button]
    assert labels, "expected buttons"
    for label in labels:
        btn = next(b for b in at.button if b.label == label)
        btn.click()
        at.run(timeout=60)
        assert not at.exception, f"{label!r} raised {at.exception}"


def test_replay_button_jumps_from_live_to_17_april(offline):
    from datetime import date

    at = _app(mode="Live Feed")
    # Live is offline here, so the page stops early; switch to replay another day first.
    at.session_state["mode"] = "Replay Storm"
    at.session_state["replay_date"] = date(2024, 11, 22)
    at.run(timeout=60)
    next(b for b in at.button if b.label.startswith("⟳ Replay the 17 Apr 2021")).click()
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["mode"] == "Replay Storm"
    assert at.sidebar.date_input[0].value == date(2021, 4, 17)


def test_no_hard_coded_figures_on_the_page():
    at = _app()
    page = " ".join(
        str(e.value) for e in [*at.markdown, *at.caption, *at.metric, *at.warning, *at.info]
    )
    for banned in [
        "0.05 max",
        " psi",
        "100% Ingest",
        "1-Hour",
        "DAISI",
        "SYNCHRONIZED",
        "P(Flash)",
    ]:
        assert banned not in page, banned
