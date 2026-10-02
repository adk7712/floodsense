"""Tests for the storm-replay backtest tool (floodsense.models.backtest)."""

import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.data.replay import load_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.models import backtest
from floodsense.models.backtest import BacktestEvent, main, parse_event, run_backtest
from floodsense.models.scoring import default_thresholds, predict_probabilities

REPLAY = settings.replay_file
BT_EVENT = "BUKIT TIMAH|2021-04-17T13:30|approx_hour"


@pytest.fixture(autouse=True)
def stub_events(monkeypatch):
    """Pin "no event file" so these tests don't depend on data/reference/flood_events.csv."""

    def not_implemented(*args, **kwargs):
        raise NotImplementedError("flood events disabled in this test")

    monkeypatch.setattr(backtest, "load_flood_events", not_implemented)


@pytest.fixture(scope="module")
def heuristic_run(tmp_path_factory):
    # Module-scoped, so it cannot use the function-scoped autouse monkeypatch; pass events=[].
    out = tmp_path_factory.mktemp("heuristic")
    return run_backtest(REPLAY, model="heuristic", events=[], out_dir=out), out


def test_heuristic_summary_covers_all_zones(heuristic_run):
    summary, _ = heuristic_run
    replay = load_replay(REPLAY)
    assert summary["n_zones"] == len(summary["zones"]) == 55
    assert summary["display_window"]["start"] == replay.display_start.isoformat()
    assert summary["display_window"]["end"] == replay.display_end.isoformat()
    assert summary["model"]["kind"] == "heuristic"
    assert summary["thresholds"]["source"] == "default"
    assert summary["thresholds"]["values"] == default_thresholds()


def test_heuristic_writes_outputs(heuristic_run):
    summary, out = heuristic_run
    assert (out / "summary.json").is_file()
    html = (out / "timeline.html").read_text()
    assert "plotly" in html.lower() and "heuristic" in html
    on_disk = json.loads((out / "summary.json").read_text())
    assert on_disk["n_zones"] == 55
    assert on_disk["zones"]["BUKIT TIMAH"] == summary["zones"]["BUKIT TIMAH"]


def test_times_fall_inside_display_window(heuristic_run):
    summary, _ = heuristic_run
    lo = datetime.fromisoformat(summary["display_window"]["start"])
    hi = datetime.fromisoformat(summary["display_window"]["end"])
    zone = summary["zones"]["BUKIT TIMAH"]
    assert lo <= datetime.fromisoformat(zone["peak_time"]) <= hi
    if zone["first_high"] is not None:
        assert lo <= datetime.fromisoformat(zone["first_high"]) <= hi
    for z in summary["zones"].values():
        assert lo <= datetime.fromisoformat(z["peak_time"]) <= hi


def test_peak_matches_direct_scoring(heuristic_run):
    summary, _ = heuristic_run
    replay = load_replay(REPLAY)
    features = compute_zone_feature_table(replay.snapshots, replay.stations)
    stamps = pd.to_datetime(features["timestamp"])
    features = features[(stamps >= replay.display_start) & (stamps <= replay.display_end)]
    probs = predict_probabilities(features, None)
    mask = (features["ura_planning_area"] == "BUKIT TIMAH").to_numpy()
    assert summary["zones"]["BUKIT TIMAH"]["peak_probability"] == pytest.approx(
        probs[mask].max(), rel=1e-9
    )
    # Alert times are consistent with the recorded peak and thresholds.
    t = summary["thresholds"]["values"]
    for z in summary["zones"].values():
        assert (z["first_high"] is not None) == (z["peak_probability"] >= t["high"])
        assert (z["first_moderate"] is not None) == (z["peak_probability"] >= t["moderate"])


def test_event_outcome_present(tmp_path):
    summary = run_backtest(
        REPLAY, model="heuristic", events=[parse_event(BT_EVENT)], out_dir=tmp_path
    )
    assert summary["events"]["count"] == 1
    for level in ("high", "moderate"):
        (outcome,) = summary["evaluation"][level]["outcomes"]
        assert outcome["zone"] == "BUKIT TIMAH"
        assert isinstance(outcome["hit"], bool)
        if outcome["hit"]:
            assert outcome["lead_minutes"] is not None
        else:
            assert outcome["lead_minutes"] is None


def test_event_outcome_written_to_tmp(tmp_path):
    ev = BacktestEvent("BUKIT TIMAH", datetime.fromisoformat("2021-04-17T13:30"), "approx_hour")
    summary = run_backtest(REPLAY, model="heuristic", events=[ev], out_dir=tmp_path)
    assert "BUKIT TIMAH" in (tmp_path / "timeline.html").read_text()
    assert json.loads((tmp_path / "summary.json").read_text())["evaluation"] is not None
    assert summary["evaluation"]["high"]["summary"]["events"] == 1


def test_no_events_records_reason(tmp_path):
    summary = run_backtest(REPLAY, model="heuristic", out_dir=tmp_path)
    assert summary["evaluation"] is None
    assert summary["events"]["source"] == "none"
    assert summary["events"]["count"] == 0
    assert "not implemented" in summary["events"]["reason"]
    assert (tmp_path / "summary.json").is_file()


def test_missing_events_file_records_reason(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("flood_events.csv")

    monkeypatch.setattr(backtest, "load_flood_events", missing)
    summary = run_backtest(REPLAY, model="heuristic", out_dir=tmp_path)
    assert summary["evaluation"] is None
    assert "missing" in summary["events"]["reason"]


def test_model_path_must_be_flood_model(tmp_path):
    import joblib

    bad = tmp_path / "not_a_model.joblib"
    joblib.dump({"x": 1}, bad)
    with pytest.raises(TypeError):
        run_backtest(REPLAY, model=bad, events=[], out_dir=tmp_path / "out")


def test_parse_event():
    ev = parse_event("bukit timah|2021-04-17T13:30|exact")
    assert ev.ura_planning_area == "BUKIT TIMAH"
    assert ev.time_precision == "exact"
    assert ev.timestamp_start.utcoffset().total_seconds() == 8 * 3600  # naive = SGT
    assert parse_event("A|2021-04-17T13:30").time_precision == "approx_hour"
    with pytest.raises(ValueError):
        parse_event("A|2021-04-17T13:30|sometime")
    with pytest.raises(ValueError):
        parse_event("just-a-zone")


def test_cli_heuristic(tmp_path, capsys):
    code = main(
        [
            "--replay",
            str(REPLAY),
            "--model",
            "heuristic",
            "--event",
            BT_EVENT,
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "BUKIT TIMAH" in printed and "1ST HIGH" in printed
    assert (tmp_path / "summary.json").is_file()
    assert np.isfinite(
        json.loads((tmp_path / "summary.json").read_text())["evaluation"]["zone_years"]
    )


def test_cli_rejects_bad_event(tmp_path):
    with pytest.raises(SystemExit):
        main(["--model", "heuristic", "--event", "nonsense", "--out", str(tmp_path)])


def test_rows_without_active_rain_score_zero():
    """The active-rain gate applies to models too, matching what the feature store keeps."""

    class AlwaysHigh:
        def predict_proba(self, features):
            return np.ones(len(features))

    features = pd.DataFrame({"rain_30m": [0.0, 0.0, 5.0], "rain_120m": [0.0, 0.19, 5.0]})
    assert predict_probabilities(features, AlwaysHigh()).tolist() == [0.0, 0.0, 1.0]
