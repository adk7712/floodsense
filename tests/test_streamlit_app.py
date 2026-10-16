"""
FloodSense - Streamlit app tests (in-process, via streamlit.testing.v1.AppTest).

Live mode is exercised with the network patched out, so these tests run offline.
"""

from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from floodsense.app import replay_days
from floodsense.app.replay_days import store_available
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

pytestmark = pytest.mark.integration

APP_PATH = str(Path(__file__).parent.parent / "src" / "floodsense" / "app" / "streamlit_app.py")


@pytest.fixture
def offline(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr("time.sleep", lambda *_: None)


def _app(mode: str = "Replay Storm", stage: str = "simulate") -> AppTest:
    """The app in ``mode`` (it opens in Live Feed by default; most tests replay real storms).
    ``stage`` is the replay sidebar step: "choose" (date and storms) or "simulate" (area, time)."""
    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = mode
    at.session_state["replay_stage"] = stage
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


def test_streamlit_app_theme_toggle():
    at = _app()
    assert at.toggle[0].value is False
    assert at.session_state["theme"] == "light"

    at.toggle[0].set_value(True)
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["theme"] == "dark"

    at.toggle[0].set_value(False)
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["theme"] == "light"


def test_streamlit_app_theme_toggle_preserves_session_state():
    at = _app()
    # Select non-default zone and scrub time slider
    at.selectbox[0].set_value("BEDOK")
    at.select_slider[0].set_value("11:30")
    at.run(timeout=60)
    assert not at.exception
    assert at.selectbox[0].value == "BEDOK"
    assert at.select_slider[0].value == "11:30"

    # Switch theme to Dark
    at.toggle[0].set_value(True)
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["theme"] == "dark"
    assert at.selectbox[0].value == "BEDOK"
    assert at.select_slider[0].value == "11:30"

    # Switch back to Light
    at.toggle[0].set_value(False)
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["theme"] == "light"
    assert at.selectbox[0].value == "BEDOK"
    assert at.select_slider[0].value == "11:30"


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

    at = _app(stage="choose")
    assert at.sidebar.date_input[0].value == date(2021, 4, 17)
    text = " ".join(md.value for md in at.markdown)
    assert "Reported floods this day" in text and "Dunearn" in text

    storm = next(b for b in at.sidebar.button if b.label.startswith("22 Nov 2024"))
    storm.click()
    at.run(timeout=60)
    assert not at.exception
    assert at.sidebar.date_input[0].value == date(2024, 11, 22)
    assert "Yishun" in " ".join(md.value for md in at.markdown)
    next(b for b in at.sidebar.button if b.label == "Simulate this date").click()
    at.run(timeout=60)
    assert "22 Nov 2024" in at.sidebar.select_slider[0].label

    next(b for b in at.sidebar.button if b.label == "Choose another date").click()
    at.run(timeout=60)
    at.sidebar.date_input[0].set_value(date(2019, 7, 15))
    at.run(timeout=60)
    assert not at.exception
    assert "none in our 66 sourced events" in " ".join(md.value for md in at.markdown)


@needs_store
def test_gap_and_patchy_days_are_flagged_not_shown_as_dry():
    from datetime import date

    at = _app(stage="choose")
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
def test_replay_sidebar_has_a_choose_step_and_a_simulate_step():
    from datetime import date

    at = _app(stage="choose")
    sidebar_buttons = [b.label for b in at.sidebar.button]
    assert len(at.sidebar.date_input) == 1 and "Simulate this date" in sidebar_buttons
    assert len(at.sidebar.select_slider) == 0 and len(at.sidebar.selectbox) == 0

    next(b for b in at.sidebar.button if b.label == "Simulate this date").click()
    at.run(timeout=60)
    assert not at.exception
    assert len(at.sidebar.date_input) == 0
    assert at.sidebar.selectbox[0].value == "BUKIT TIMAH"
    assert at.sidebar.select_slider[0].value == "12:15"
    order = [type(el).__name__ for el in at.sidebar]
    assert order.index("Selectbox") < order.index("SelectSlider")  # area, then time
    assert [b.label for b in at.sidebar.button][-1] == "Choose another date"  # at the bottom

    # The chosen area and time survive a trip back to the date step.
    at.sidebar.selectbox[0].set_value("BEDOK")
    at.sidebar.select_slider[0].set_value("13:00")
    at.run(timeout=60)
    next(b for b in at.sidebar.button if b.label == "Choose another date").click()
    at.run(timeout=60)
    assert at.sidebar.date_input[0].value == date(2021, 4, 17)
    next(b for b in at.sidebar.button if b.label == "Simulate this date").click()
    at.run(timeout=60)
    assert at.sidebar.selectbox[0].value == "BEDOK"
    assert at.sidebar.select_slider[0].value == "13:00"


@pytest.mark.parametrize("stage", ["choose", "simulate"])
def test_every_button_works(stage):
    labels = [b.label for b in _app(stage=stage).button]
    assert labels, "expected buttons"
    for label in labels:  # each from a fresh app: some buttons switch the sidebar step
        at = _app(stage=stage)
        next(b for b in at.button if b.label == label).click()
        at.run(timeout=60)
        assert not at.exception, f"{label!r} raised {at.exception}"


def test_replay_button_jumps_from_live_to_17_april(offline):
    from datetime import date

    at = _app(mode="Live Feed")
    # Live is offline here, so the page stops early; switch to replay another day first.
    at.session_state["mode"] = "Replay Storm"
    at.session_state["replay_date"] = date(2024, 11, 22)
    at.run(timeout=60)
    at.session_state["mode"] = "Live Feed"  # offline: the error page offers the jump
    at.run(timeout=60)
    next(b for b in at.button if "Switch to Replay Storm" in b.label).click()
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["mode"] == "Replay Storm"
    assert at.session_state["replay_date"] == date(2021, 4, 17)
    assert "17 Apr 2021" in at.sidebar.select_slider[0].label  # straight to the simulate step


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


# --- Live mode on the Databricks gold table (A2) ------------------------------------------------


@pytest.fixture
def gold_table(monkeypatch):
    """A configured warehouse whose gold answer each test sets; data.gov.sg stays offline.

    Not the ``offline`` fixture: it also stubs ``time.sleep``, which starves AppTest's own wait
    loop once a full Live page renders."""
    import streamlit as st

    from floodsense.common.config import settings
    from floodsense.serving import gold

    def boom(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(settings, "api_backoff_sec", 0.0)
    monkeypatch.setattr(settings, "databricks_warehouse_id", "test-warehouse")
    st.cache_data.clear()  # gold_latest() is cached across AppTest runs in this process

    def answer(minutes_old: float | None):
        import time as _time

        from tests.test_gold import gold_rows

        def latest_zone_risk():
            if minutes_old is None:
                raise gold.GoldUnavailable("warehouse unreachable")
            epoch = int(_time.time() - minutes_old * 60)
            return gold.to_frame(gold.PREDICTION_COLUMNS, gold_rows(epoch))

        monkeypatch.setattr(gold, "latest_zone_risk", latest_zone_risk)

    yield answer
    st.cache_data.clear()


def _page_text(at: AppTest) -> str:
    return " ".join(str(e.value) for e in [*at.markdown, *at.caption, *at.warning, *at.metric])


def test_live_reads_fresh_gold_as_scored_on_databricks(gold_table):
    gold_table(minutes_old=12)
    at = _app(mode="Live Feed")
    text = _page_text(at)
    assert "scored on Databricks" in text
    assert "12 min old" in text
    assert not at.warning
    assert at.metric[0].value == "1 / 55"  # gold's own tiers: Bukit Timah is High
    assert at.session_state["use_data_gov"] is False


def test_stale_gold_shows_its_age_and_data_gov_is_one_switch_away(gold_table):
    gold_table(minutes_old=9 * 24 * 60)
    at = _app(mode="Live Feed")
    assert "9 days old" in _page_text(at)
    assert not at.warning  # the age next to the latest reading is the only stale signal
    assert not any(b.label == "Use data.gov.sg readings" for b in at.button)

    at.button(key="advanced_btn").click()  # Advanced opens its toggle to the right
    at.run(timeout=60)
    at.toggle(key="use_data_gov").set_value(True)
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["use_data_gov"] is True
    assert any("Live feed unavailable" in str(e.value) for e in at.error)  # offline here

    at.button(key="advanced_btn_open").click()  # closing Advanced keeps the choice
    at.run(timeout=60)
    assert at.session_state["use_data_gov"] is True


def test_unreachable_gold_falls_back_to_data_gov(gold_table):
    gold_table(minutes_old=None)
    at = _app(mode="Live Feed")
    assert any("warehouse unreachable" in str(w.value) for w in at.warning)
    assert any("Live feed unavailable" in str(e.value) for e in at.error)


def test_without_a_warehouse_live_starts_on_data_gov_quietly(offline):
    at = _app(mode="Live Feed")
    assert at.session_state["use_data_gov"] is True
    assert not any("Databricks" in str(w.value) for w in at.warning)


def test_live_data_gov_scores_the_newest_filled_in_step(monkeypatch):
    """NEA publishes a step with a few gauges first; the badge and the risk use the step before."""
    from datetime import timedelta

    import streamlit as st

    from floodsense.common.config import settings
    from floodsense.common.schemas import RainfallSnapshot
    from floodsense.data.replay import load_replay
    from floodsense.ingestion.poller import NEAPoller

    snaps = load_replay(settings.replay_file).snapshots
    last = snaps[-1]
    stub = RainfallSnapshot.model_construct(
        timestamp=last.timestamp + timedelta(minutes=5),
        readings={sid: 0.0 for sid in list(last.readings)[:4]},
        stations=last.stations,
    )

    def boom(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", boom)  # radar and alerts stay offline
    monkeypatch.setattr(NEAPoller, "fetch_history", lambda self, hours: [*snaps, stub])
    st.cache_data.clear()
    try:
        at = _app(mode="Live Feed")
        text = _page_text(at)
        assert f"● Live: {len(last.readings)} of " in text
        assert "● Live: 4 of " not in text
    finally:
        st.cache_data.clear()


# --- Resident view -------------------------------------------------------------------------
def _resident(zone: str | None = None, time_label: str = "12:15") -> AppTest:
    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = "Replay Storm"
    at.session_state["replay_stage"] = "simulate"
    at.session_state["view"] = "Resident view"
    if zone:
        at.session_state["zone"] = zone
    at.session_state[f"replay-time-{replay_days.PINNED_STORM}"] = time_label
    at.run(timeout=60)
    assert not at.exception
    return at


def _resident_text(at: AppTest) -> str:
    return " ".join(md.value for md in at.markdown) + " ".join(c.value for c in at.caption)


def _tiers_at(time_label: str) -> dict[str, str]:
    """Each zone's tier at a time on 17 Apr 2021, scored independently of the app."""
    from floodsense.models.artifact import load_model
    from floodsense.models.scoring import score_zone_features

    table = replay_days.day_view(replay_days.PINNED_STORM).features
    step = table[table["timestamp"].dt.strftime("%H:%M") == time_label]
    scored = score_zone_features(step.reset_index(drop=True), load_model())
    return dict(zip(scored["ura_planning_area"], scored["risk_tier"], strict=True))


def test_resident_view_renders_plain_language_and_hides_the_analyst_page():
    at = _resident()
    text = _resident_text(at)
    assert "Your area" in text and "What to do" in text
    assert "flash flood" in text and "(Singapore time)" in text
    assert "complements, and does not replace, PUB's flood alerts" in text
    assert "Replay of past data: showing 12:15, 17 Apr 2021" in text
    for jargon in ("probability", "calibrated", "IDW", "(experimental)"):
        assert jargon.lower() not in text.lower()
    assert len(at.metric) == 0  # no KPI cards, charts or map: the analyst page is not drawn
    assert at.selectbox[0].value == "BUKIT TIMAH"


def test_resident_view_switch_returns_to_the_analyst_view():
    at = _resident()
    at.button_group[0].set_value("Analyst view")
    at.run(timeout=60)
    assert not at.exception
    assert "/ 55" in at.metric[0].value
    assert "Zone Diagnostic" in " ".join(md.value for md in at.markdown)


def test_resident_area_selection_changes_the_shown_area():
    at = _resident(zone="BEDOK")
    assert "Bedok, right now" in _resident_text(at)
    at.selectbox[0].set_value("BUKIT TIMAH")
    at.run(timeout=60)
    assert not at.exception
    text = _resident_text(at)
    assert "Bukit Timah, right now" in text and "Bedok, right now" not in text


@needs_store
def test_resident_advice_matches_the_tier():
    from floodsense.app.resident_view import ADVICE_FOR_TIER, RISK_NOW

    # 12:50 on 17 Apr 2021 has High zones. Check each tier that occurs.
    at = _resident(time_label="12:50")
    tiers = _tiers_at("12:50")
    seen = set()
    for zone in sorted(URA_PLANNING_AREAS):
        tier = tiers[zone]
        if tier in seen and tier != "High":
            continue
        at.selectbox[0].set_value(zone)
        at.run(timeout=60)
        assert not at.exception
        text = _resident_text(at)
        assert RISK_NOW[tier] in text, (zone, tier)
        assert ADVICE_FOR_TIER[tier] in text
        for other in set(ADVICE_FOR_TIER) - {tier}:
            assert ADVICE_FOR_TIER[other] not in text
        seen.add(tier)
    assert "High" in seen, "expected some zone to be High at 12:50 on 17 Apr 2021"


def test_resident_view_in_live_reads_the_same_gold_and_shows_no_stale_caption(gold_table):
    gold_table(minutes_old=9 * 24 * 60)
    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = "Live Feed"
    at.session_state["view"] = "Resident view"
    at.session_state["zone"] = "BUKIT TIMAH"
    at.run(timeout=60)
    assert not at.exception
    text = _resident_text(at)
    assert "Bukit Timah, right now" in text
    assert "High risk of a flash flood" in text  # gold's own tier, as in the analyst view
    assert not at.warning
    assert "poller" not in text and "old" not in text.lower().replace("older", "")
    assert not any(m.label == "Use data.gov.sg readings" for m in at.button)
    at.button(key="advanced_btn").click()  # the sidebar control still works from this view
    at.run(timeout=60)
    assert not at.exception
