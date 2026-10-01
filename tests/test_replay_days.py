"""Replaying any day from the rainfall store (floodsense.app.replay_days)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from floodsense.app import replay_days as rd
from floodsense.common.config import settings
from floodsense.common.schemas import FloodEvent
from floodsense.data.replay import load_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.models.artifact import load_model
from floodsense.models.scoring import score_zone_features

needs_store = pytest.mark.skipif(not rd.store_available(), reason="rainfall store not present")


def ev(day: str, zone: str, severity: str = "Minor", precision: str = "exact") -> FloodEvent:
    return FloodEvent(
        event_id=f"{day}-{zone}",
        timestamp_start=pd.Timestamp(f"{day}T15:00+08:00").to_pydatetime(),
        time_precision=precision,
        location_raw="x",
        ura_planning_area=zone,
        severity=severity,
    )


def test_featured_storms_pins_17_april_then_ranks_by_reports():
    events = [
        ev("2021-04-17", "BUKIT TIMAH"),
        ev("2020-01-01", "BEDOK"),
        ev("2020-01-01", "TAMPINES"),
        ev("2022-05-05", "BEDOK", severity="Severe"),
        ev("2022-05-05", "GEYLANG"),
        ev("2023-03-03", "YISHUN"),
    ]
    days = [d for d, _ in rd.featured_storms(events, n=3)]
    assert days == [date(2021, 4, 17), date(2022, 5, 5), date(2020, 1, 1)]
    labels = dict(rd.featured_storms(events))
    assert labels[date(2020, 1, 1)] == "01 Jan 2020 · Bedok, Tampines"


def test_events_on_selects_the_day_in_time_order():
    events = [ev("2024-11-22", "YISHUN"), ev("2024-11-21", "BEDOK")]
    assert [e.ura_planning_area for e in rd.events_on(date(2024, 11, 22), events)] == ["YISHUN"]


@needs_store
def test_store_day_matches_the_recorded_replay():
    """17 Apr 2021 from the store agrees with the API recording (to the API's 2-decimal
    rounding), and gives the same Bukit Timah alert time."""
    replay = load_replay(settings.replay_file)
    view = rd.day_view(date(2021, 4, 17))
    assert view.step_share == 1.0 and view.total_stations == len(replay.stations)

    ref = compute_zone_feature_table(replay.snapshots, replay.stations)
    window = (ref["timestamp"] >= replay.display_start) & (ref["timestamp"] <= replay.display_end)
    ref = ref[window].sort_values(["timestamp", "ura_planning_area"]).reset_index(drop=True)
    got = view.features
    got = got[(got["timestamp"] >= replay.display_start) & (got["timestamp"] <= replay.display_end)]
    got = got.sort_values(["timestamp", "ura_planning_area"]).reset_index(drop=True)
    assert len(got) == len(ref)
    for col in ["rain_5m", "rain_30m", "rain_60m", "rain_120m"]:
        np.testing.assert_allclose(got[col], ref[col], atol=0.1, err_msg=col)

    scored = score_zone_features(got, load_model())
    bt = scored[scored["ura_planning_area"] == "BUKIT TIMAH"]
    assert bt.loc[bt["risk_tier"] == "High", "timestamp"].min() == pd.Timestamp(
        "2021-04-17 12:45", tz="Asia/Singapore"
    )


@needs_store
def test_gap_day_is_empty_and_partial_day_reports_its_share():
    assert rd.day_view(date(2018, 2, 8)).features.empty
    partial = rd.day_view(date(2021, 1, 1))
    assert 0.3 < partial.step_share < 0.4
