"""
Phase 5 local acceptance: the pandas core the Databricks pipeline must call (see
docs/phase5-handoff.md). These run in CI; the workspace run is checked by test_phase5_parity.py.
"""

import json

import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.data.replay import load_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.models.artifact import load_model
from floodsense.models.scoring import score_zone_features
from floodsense.serving import pipeline_core as core


@pytest.fixture(scope="module")
def replay():
    return load_replay(settings.replay_file)


@pytest.fixture(scope="module")
def silver(replay):
    return core.payloads_to_readings(core.snapshot_to_payload(s) for s in replay.snapshots)


@pytest.fixture(scope="module")
def gold(replay):
    return core.replay_predictions()


def test_payload_round_trip_keeps_every_reading(replay, silver):
    readings, stations = silver
    assert len(readings) == sum(len(s.readings) for s in replay.snapshots)
    assert set(stations["station_id"]) == set(replay.stations)
    first = replay.snapshots[0]
    got = readings[readings["timestamp"] == pd.Timestamp(first.timestamp)]
    assert dict(zip(got["station_id"], got["rainfall_mm"], strict=True)) == first.readings
    assert str(readings["timestamp"].dt.tz) == "Asia/Singapore"


def test_gold_matches_the_app_path_exactly(replay, gold):
    """Same rows, features, probabilities and tiers as the app/backtest computes."""
    table = compute_zone_feature_table(replay.snapshots, replay.stations)
    stamps = pd.to_datetime(table["timestamp"])
    table = table[(stamps >= replay.display_start) & (stamps <= replay.display_end)]
    want = score_zone_features(table, load_model())[core.PREDICTION_COLUMNS]
    want = want.sort_values(["timestamp", "ura_planning_area"]).reset_index(drop=True)
    assert list(gold.columns) == core.PREDICTION_COLUMNS
    assert len(gold) == len(want) == 55 * len(replay.display_timestamps)
    assert (gold["risk_tier"] == want["risk_tier"]).all()
    for col in core.PREDICTION_COLUMNS[2:-1]:
        np.testing.assert_allclose(gold[col].to_numpy(float), want[col].to_numpy(float), rtol=1e-12)


def test_known_result_bukit_timah_goes_high_at_1245(gold):
    bt = gold[gold["ura_planning_area"] == "BUKIT TIMAH"]
    first_high = bt.loc[bt["risk_tier"] == "High", "timestamp"].min()
    assert first_high == pd.Timestamp("2021-04-17 12:45", tz="Asia/Singapore")


def test_missing_readings_stay_missing_and_bad_values_are_dropped():
    payload = {
        "code": 0,
        "data": {
            "stations": [
                {"id": "S1", "name": "One", "location": {"latitude": 1.3, "longitude": 103.8}},
                {"id": "S2", "name": "Two", "location": {"latitude": 1.35, "longitude": 103.85}},
                {"id": "S3", "name": "Three", "location": {"latitude": 1.4, "longitude": 103.9}},
            ],
            "readings": [
                {
                    "timestamp": "2026-01-01T10:00:00+08:00",
                    "data": [
                        {"stationId": "S1", "value": 0.0},
                        {"stationId": "S2", "value": None},  # not reporting: stays absent
                        {"stationId": "S3", "value": 450.0},  # impossible: dropped, not clipped
                    ],
                }
            ],
        },
    }
    readings, stations = core.payloads_to_readings([payload])
    assert readings[["station_id", "rainfall_mm"]].values.tolist() == [["S1", 0.0]]
    assert len(stations) == 3


def test_unrecognised_payload_raises_so_the_pipeline_can_quarantine_it():
    with pytest.raises(ValueError):
        core.payloads_to_readings([{"unexpected": True}])


def test_emit_window_and_warmup(replay, silver):
    readings, stations = silver
    t = replay.display_start
    out = core.score_window(readings, stations, emit_from=t, emit_to=t)
    assert len(out) == 55 and (out["timestamp"] == pd.Timestamp(t)).all()
    assert pd.Timedelta(hours=72) == core.WARMUP


def test_cli_exports_landing_files_and_reference(tmp_path, replay):
    assert core.main(["export-replay", "--out", str(tmp_path / "landing")]) == 0
    files = sorted((tmp_path / "landing").glob("rainfall_*.json"))
    assert len(files) == len(replay.snapshots)
    assert "readings" in json.loads(files[0].read_text())["data"]
    assert core.main(["expected", "--out", str(tmp_path / "expected.csv")]) == 0
    ref = pd.read_csv(tmp_path / "expected.csv")
    assert list(ref.columns) == core.PREDICTION_COLUMNS
