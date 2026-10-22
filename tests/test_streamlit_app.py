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
    """The Analyst view in ``mode`` (the app opens in Live Feed on the Resident view; most tests
    replay real storms on the Analyst page). ``stage`` is the replay sidebar step: "choose" (date
    and storms) or "simulate" (area, time)."""
    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = mode
    at.session_state["view"] = "Analyst view"
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
    # The 5 number cards; public transport is on the Resident view.
    assert len(at.metric) == 5
    assert not any("Public transport at risk" in h.value for h in at.subheader)


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
    assert "High from" in text
    # H2: the live model's figures are its 2020–2023 validation; the held-out test is version 1's.
    assert "the live model's method and thresholds caught 18 of 28" in text
    assert "scored once, with version 1" in text
    assert "(version 3) has not been scored on it" in text
    assert "radar nowcasting is next" not in text and "60.8M" not in text
    from floodsense.data.flood_events import load_flood_events

    assert f"Flood labels: {len(load_flood_events())} sourced events" in text
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
    from floodsense.data.flood_events import load_flood_events

    n = len(load_flood_events())
    text = " ".join(md.value for md in at.markdown)
    assert f"none in our {n} sourced events" in text and "66 sourced" not in text


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


def test_opens_on_the_resident_view():
    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = "Replay Storm"
    at.session_state["replay_stage"] = "simulate"
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["view"] == "Resident view"
    assert len(at.selectbox) == 1  # the page's own area picker; the sidebar one steps aside
    assert len(at.metric) == 0


def test_the_headline_says_how_many_areas_are_at_risk():
    at = _app()
    high, moderate = (int(m.value.split(" / ")[0]) for m in at.metric[:2])
    text = " ".join(md.value for md in at.markdown)
    if high:
        assert f"{high} of 55 areas at High risk" in text
    elif moderate:
        assert f"{moderate} of 55 areas at Moderate risk, none at High" in text
    else:
        assert "No area at Moderate or High risk" in text
    assert "Floods reported this day:" in text and "Bukit Timah (13:44 approx.)" in text
    assert "What to do" not in text  # the residents' advice stays on the Resident view


@needs_store
def test_a_test_set_day_keeps_its_second_look_label_in_view():
    from datetime import date

    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = "Replay Storm"
    at.session_state["replay_stage"] = "simulate"
    at.session_state["view"] = "Analyst view"
    at.session_state["replay_date"] = date(2024, 11, 22)
    at.run(timeout=60)
    assert not at.exception
    # A top-level caption, not one folded inside an expander.
    main = [c.value for c in at.main.caption if c.value]
    folded = [c.value for e in at.expander for c in e.caption]
    assert any("second look" in c for c in main if c not in folded)


def test_opens_in_live_mode_by_default(offline):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=60)
    assert not at.exception
    assert at.sidebar.button_group[0].value == "Live Feed"
    assert any(
        "can't reach NEA's rain gauges" in e.value for e in at.error
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
    st.cache_resource.clear()  # and so is a remembered gold failure

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
    assert any(w.value.startswith("Data is 9 days old") for w in at.warning)  # and a warning
    assert not any(b.label == "Use data.gov.sg readings" for b in at.button)

    at.button(key="advanced_btn").click()  # Advanced opens its toggle to the right
    at.run(timeout=60)
    at.toggle(key="use_data_gov").set_value(True)
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["use_data_gov"] is True
    assert any("can't reach NEA's rain gauges" in str(e.value) for e in at.error)  # offline here

    at.button(key="advanced_btn_open").click()  # closing Advanced keeps the choice
    at.run(timeout=60)
    assert at.session_state["use_data_gov"] is True


def test_unreachable_gold_falls_back_to_data_gov(gold_table):
    gold_table(minutes_old=None)
    at = _app(mode="Live Feed")
    assert any("Couldn't read the Databricks gold table" in str(w.value) for w in at.warning)
    assert "warehouse unreachable" in at.expander[0].code[0].value  # the detail, collapsed
    assert any("can't reach NEA's rain gauges" in str(e.value) for e in at.error)


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
    assert "not an official warning; alerts can be late or missed; follow PUB." in text
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


def test_resident_view_in_live_reads_the_same_gold_and_flags_old_data(gold_table):
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
    assert [w.value for w in at.warning] == [
        "Out of date: the latest data is 9 days old, so this may not be current."
    ]
    assert "poller" not in text and "old" not in text.lower().replace("older", "")
    assert not any(m.label == "Use data.gov.sg readings" for m in at.button)
    at.button(key="advanced_btn").click()  # the sidebar control still works from this view
    at.run(timeout=60)
    assert not at.exception


# --- PUB flood alert for the selected area (Live only) -----------------------------------------
def _fake_alert(zone: str = "BUKIT TIMAH"):
    from datetime import datetime

    from floodsense.common.config import settings
    from floodsense.ingestion.flood_alerts import FloodAlert

    return FloodAlert(
        identifier="test-1",
        msg_type="Alert",
        references="",
        issued_at=datetime(2026, 10, 17, 14, 5, tzinfo=settings.tzinfo),
        headline="Flash flood",
        description="Flash flood at Bukit Timah Road near Coronation Road. Avoid the area.",
        area_desc="Bukit Timah",
        latitude=1.33,
        longitude=103.8,
        radius_km=0.5,
        severity="Severe",
        zone=zone,
    )


def _alert_lines(at: AppTest) -> list[str]:
    return [str(e.value) for e in at.error if "PUB flood alert active here" in str(e.value)]


@pytest.fixture
def pub_alert(gold_table, monkeypatch):
    from floodsense.ingestion import flood_alerts

    gold_table(minutes_old=5)
    monkeypatch.setattr(flood_alerts, "fetch_flood_alerts", lambda *a, **k: [_fake_alert()])


@pytest.mark.parametrize("view", ["Analyst view", "Resident view"])
def test_pub_alert_shows_only_for_its_own_area_in_live(pub_alert, view):
    def run(zone):
        at = AppTest.from_file(APP_PATH)
        at.session_state["mode"] = "Live Feed"
        at.session_state["view"] = view
        at.session_state["zone"] = zone
        at.run(timeout=60)
        assert not at.exception
        return _alert_lines(at)

    here = run("BUKIT TIMAH")
    assert len(here) == 1
    assert "Coronation Road" in here[0] and "14:05 SGT" in here[0]
    assert run("BEDOK") == []


def test_no_pub_alert_line_in_replay(pub_alert):
    at = _app(mode="Replay Storm")
    at.session_state["zone"] = "BUKIT TIMAH"
    at.run(timeout=60)
    assert not at.exception
    assert _alert_lines(at) == []


# --- Shareable ?area= link -----------------------------------------------------------------
def _linked(slug: str | None, view: str | None = None) -> AppTest:
    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = "Replay Storm"
    at.session_state["replay_stage"] = "simulate"
    at.session_state["view"] = view or "Analyst view"
    if slug is not None:
        at.query_params["area"] = slug
    at.run(timeout=60)
    assert not at.exception
    return at


@pytest.mark.parametrize("view", [None, "Resident view"])
@pytest.mark.parametrize("slug", ["bukit-merah", "Bukit-Merah"])
def test_area_link_selects_the_area(slug, view):
    assert _linked(slug, view).session_state["zone"] == "BUKIT MERAH"


@pytest.mark.parametrize("slug", ["no-such-place", "", "   "])
def test_bad_area_link_falls_back_to_the_default(slug):
    assert _linked(slug).session_state["zone"] == "BUKIT TIMAH"


def test_no_area_link_uses_the_default():
    assert _linked(None).session_state["zone"] == "BUKIT TIMAH"


def test_area_link_does_not_override_a_later_choice():
    at = _linked("bukit-merah")
    at.session_state["zone"] = "BEDOK"
    at.run(timeout=60)
    assert not at.exception
    assert at.session_state["zone"] == "BEDOK"


def test_transport_table_pins_the_selected_area_first_even_when_low():
    at = _linked("bedok", "Resident view")
    table = next(m.value for m in at.markdown if "<thead>" in m.value)
    first_row = table.split("<tbody>")[1].split("</tr>")[0]
    assert "Bedok" in first_row and "Low" in first_row


def test_public_transport_is_on_the_resident_view_with_tiers_only():
    at = _resident()  # 12:15 on 17 Apr 2021 has Moderate areas with MRT exits
    assert any("Public transport at risk" in h.value for h in at.subheader)
    text = _resident_text(at)
    assert "**Stations with an exit at risk:**" in text and "Moderate areas" in text
    table = next(m.value for m in at.markdown if "<thead>" in m.value)
    # No percentages for residents (the table's own CSS has "width:100%", so check the cells).
    assert "Chance" not in table and "%</td>" not in table


# --- live outage, gold failure, stale data, odd inputs (stress-test fixes) -------------------

from datetime import datetime, timedelta  # noqa: E402

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from floodsense.common.config import settings  # noqa: E402
from floodsense.common.schemas import RainfallSnapshot, StationMetadata  # noqa: E402
from floodsense.ingestion import flood_alerts, poller  # noqa: E402
from floodsense.serving import gold  # noqa: E402
from floodsense.serving.gold import GoldUnavailable  # noqa: E402
from floodsense.serving.pipeline_core import PREDICTION_COLUMNS  # noqa: E402
from floodsense.spatial.singapore_geo import load_station_snapshot  # noqa: E402


def _now() -> datetime:
    return datetime.now(settings.tzinfo).replace(second=0, microsecond=0)


def _live_feed(monkeypatch, end: datetime, rain: float = 0.0, empty_last: int = 0) -> None:
    """Live data.gov.sg readings ending at ``end``; the last ``empty_last`` steps have no gauge."""
    stations = {
        sid: StationMetadata(station_id=sid, name=s["name"], latitude=s["lat"], longitude=s["lon"])
        for sid, s in load_station_snapshot().items()
    }
    snaps = []
    for k in range(72):
        ts = end - timedelta(minutes=5 * (71 - k))
        readings = {} if k >= 72 - empty_last else {sid: rain for sid in stations}
        snaps.append(RainfallSnapshot(timestamp=ts, readings=readings, stations=stations))
    monkeypatch.setattr(poller.NEAPoller, "fetch_history", lambda self, hours, now=None: snaps)
    monkeypatch.setattr(flood_alerts, "fetch_flood_alerts", lambda day=None, **kw: [])
    monkeypatch.setattr(settings, "databricks_warehouse_id", None)
    st.cache_data.clear()
    st.cache_resource.clear()  # also forgets a recent gold failure


def _gold_frame(ts: pd.Timestamp, drop: tuple[str, ...] = ()) -> pd.DataFrame:
    rows = [
        dict(
            ura_planning_area=z,
            timestamp=ts,
            rain_5m=0.0,
            rain_15m=0.0,
            rain_30m=0.0,
            rain_60m=0.0,
            rain_120m=0.0,
            rain_decay_72h=1.0,
            reporting_stations=90,
            flood_probability=0.001,
            risk_tier="Low",
        )
        for z in sorted(URA_PLANNING_AREAS)
        if z not in drop
    ]
    return pd.DataFrame(rows)[PREDICTION_COLUMNS]


def _use_gold(monkeypatch, fn) -> None:
    monkeypatch.setattr(gold, "latest_zone_risk", fn)
    monkeypatch.setattr(gold, "warehouse_id", lambda: "wh-test")


def _all_text(at: AppTest) -> str:
    parts = [
        str(x.value) for c in (at.markdown, at.caption, at.warning, at.error, at.info) for x in c
    ]
    return "\n".join(parts)


def _live_app(**state) -> AppTest:
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["mode"] = "Live Feed"
    at.session_state["view"] = "Analyst view"
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    return at


def test_an_outage_step_shows_no_data_not_low(monkeypatch):
    _live_feed(monkeypatch, _now() - timedelta(minutes=5), rain=4.0, empty_last=3)
    at = _live_app(use_data_gov=True)
    assert not at.exception
    text = _all_text(at)
    assert any("Too few rain gauges are reporting" in w.value for w in at.warning)
    assert "No gauge data — risk unknown" in text
    assert "LOW RISK" not in text and "All dry" not in text and "No rain" not in text
    assert at.metric[0].value == "0 / 55" and at.metric[1].value == "0 / 55"

    resident = _live_app(use_data_gov=True, view="Resident view")
    assert not resident.exception
    rtext = _all_text(resident)
    assert "No gauge data — risk unknown" in rtext and "Low risk of a flash flood" not in rtext


def test_a_gap_in_the_last_hour_adds_a_caution(monkeypatch):
    _live_feed(monkeypatch, _now() - timedelta(minutes=5), rain=0.0)
    snaps = poller.NEAPoller.fetch_history(None, 3)  # the patched feed
    gap = snaps[-4].timestamp
    monkeypatch.setattr(
        poller.NEAPoller,
        "fetch_history",
        lambda self, hours, now=None: [s for s in snaps if s.timestamp != gap],
    )
    st.cache_data.clear()
    at = _live_app(use_data_gov=True)
    assert not at.exception
    text = _all_text(at)
    assert (
        "Some readings in the last hour are missing, so rain totals and risk may read too low."
        in text
    )
    assert (
        "No rain here in the last 30 minutes" not in text and "No rain in the last 30" not in text
    )
    assert "Readings missing" in text and "All dry" not in text
    resident = _live_app(use_data_gov=True, view="Resident view")
    assert "Some rain readings from the last hour are missing" in _all_text(resident)


def test_a_failed_gold_read_is_not_cached_and_not_hammered(monkeypatch):
    calls = {"n": 0}
    now = pd.Timestamp(_now())

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise GoldUnavailable("warehouse starting")
        return _gold_frame(now)

    _live_feed(monkeypatch, _now())
    _use_gold(monkeypatch, flaky)
    at = _live_app(use_data_gov=False)
    assert any("Databricks gold table" in w.value for w in at.warning)
    assert "warehouse starting" not in _all_text(at)  # the detail is in the expander
    at.run()  # a rerun soon after leaves the failed warehouse alone
    assert calls["n"] == 1
    st.cache_resource.clear()  # what Retry does: forget the failure
    at.run()
    assert calls["n"] == 2
    assert not any("Databricks gold table" in w.value for w in at.warning)


def test_a_zone_missing_from_gold_shows_no_data_instead_of_crashing(monkeypatch):
    _live_feed(monkeypatch, _now())
    now = pd.Timestamp(_now())
    _use_gold(monkeypatch, lambda: _gold_frame(now, drop=("BUKIT TIMAH",)))
    at = _live_app(use_data_gov=False)
    assert not at.exception
    assert "No gauge data — risk unknown" in _all_text(at)
    assert "1 area has no data" in _all_text(at)
    resident = _live_app(use_data_gov=False, view="Resident view")
    assert not resident.exception and "No gauge data — risk unknown" in _all_text(resident)


@pytest.mark.parametrize("source", ["data.gov.sg", "gold"])
def test_old_data_is_flagged_in_both_views(monkeypatch, source):
    old = _now() - timedelta(hours=5)
    _live_feed(monkeypatch, old)
    _use_gold(monkeypatch, lambda: _gold_frame(pd.Timestamp(old)))
    at = _live_app(use_data_gov=source == "data.gov.sg")
    assert not at.exception
    assert any(w.value.startswith("Data is 5 h") and "old" in w.value for w in at.warning)
    resident = _live_app(use_data_gov=source == "data.gov.sg", view="Resident view")
    assert any("Out of date" in w.value for w in resident.warning)


def test_gold_rows_of_different_ages_use_the_oldest_for_the_age(monkeypatch):
    _live_feed(monkeypatch, _now())
    now = pd.Timestamp(_now())
    frame = _gold_frame(now)
    frame.loc[frame["ura_planning_area"] == "BEDOK", "timestamp"] = now - pd.Timedelta(hours=5)
    _use_gold(monkeypatch, lambda: frame)
    at = _live_app(use_data_gov=False)
    assert any(w.value.startswith("Data is 5 h") for w in at.warning)
    assert "1 of 55 areas have older readings than the rest" in _all_text(at)


def test_deselecting_the_mode_keeps_live_feed(monkeypatch):
    _live_feed(monkeypatch, _now())
    at = _live_app(use_data_gov=True)
    at.session_state["mode"] = (
        None  # the browser sends None when the chosen segment is clicked again
    )
    at.run()
    assert not at.exception
    assert at.session_state["mode"] == "Live Feed"
    assert any("Live:" in str(m.value) for m in at.markdown)


def test_an_unknown_area_link_says_so_and_the_url_follows_the_area(monkeypatch):
    _live_feed(monkeypatch, _now())
    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.session_state["mode"] = "Live Feed"
    at.session_state["view"] = "Analyst view"
    at.session_state["use_data_gov"] = True
    at.query_params["area"] = "atlantis [x](http://e.invalid)"
    at.run()
    assert not at.exception
    notice = [i.value for i in at.info if "not found" in i.value]
    assert notice and "showing Bukit Timah" in notice[0] and "\\[x\\]" in notice[0]
    at.sidebar.selectbox[0].set_value("BEDOK")
    at.run()
    assert at.query_params["area"] == ["bedok"] or at.query_params["area"] == "bedok"
    assert not any("not found" in i.value for i in at.info)


def test_a_live_outage_reads_plainly_with_the_detail_tucked_away(monkeypatch):
    def boom(self, hours, now=None):
        raise poller.LiveFeedUnavailable("HTTP 403 from https://api.example/rain")

    monkeypatch.setattr(poller.NEAPoller, "fetch_history", boom)
    monkeypatch.setattr(settings, "databricks_warehouse_id", None)
    st.cache_data.clear()
    at = _live_app(use_data_gov=True)
    assert [e.value for e in at.error] == ["We can't reach NEA's rain gauges right now."]
    assert len(at.expander) == 1  # the technical detail, collapsed
    assert "api.example" in at.expander[0].code[0].value
    assert "FLOODSENSE_DATA_GOV_API_KEY" in at.expander[0].caption[0].value  # 403: the key hint
    assert "api.example" not in " ".join(m.value for m in at.markdown)
    resident = _live_app(use_data_gov=True, view="Resident view")
    assert len(resident.expander) == 0 and "403" not in _all_text(resident)


def test_a_pub_alert_fetch_failure_is_said_in_both_views(monkeypatch):
    _live_feed(monkeypatch, _now())

    def boom(day=None, **kw):
        raise poller.LiveFeedUnavailable("HTTP 429 https://api.example/alerts")

    monkeypatch.setattr(flood_alerts, "fetch_flood_alerts", boom)
    at = _live_app(use_data_gov=True)
    assert "PUB flood alerts can't be checked right now." in _all_text(at)
    assert "api.example" not in _all_text(at)
    resident = _live_app(use_data_gov=True, view="Resident view")
    assert "PUB flood alerts can't be checked right now." in _all_text(resident)


def test_a_failed_model_load_is_a_warning_and_gold_hides_the_fallback_levels(monkeypatch):
    from floodsense.models import artifact

    st.cache_resource.clear()  # get_model() is cached across runs

    def no_model():
        raise OSError("libomp missing")

    monkeypatch.setattr(artifact, "load_model", no_model)
    now = pd.Timestamp(_now())
    _live_feed(monkeypatch, _now())
    _use_gold(monkeypatch, lambda: _gold_frame(now))
    at = _live_app(use_data_gov=False)
    text = _all_text(at)
    assert not at.exception
    assert "25.0%" not in text and "65.0%" not in text and "heavier than" not in text
    assert "The local model isn't loaded" in text

    replay = AppTest.from_file(APP_PATH, default_timeout=120)
    replay.session_state["mode"] = "Replay Storm"
    replay.session_state["view"] = "Analyst view"
    replay.run(timeout=60)
    assert any("could not be loaded" in w.value for w in replay.warning)
    st.cache_resource.clear()  # don't leave the failed load cached for the other tests
