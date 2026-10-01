"""
Tests for the I/O layer around the training core: feature store builder, training CLI, final report.

The rainfall store is a Phase 3 deliverable and is not available here, so the builder is tested
against the real 17 Apr 2021 replay served through a monkeypatched ``load_snapshots``. The training
tests use the SYNTHETIC ``data`` fixture from conftest; they check the plumbing (files, MLflow, the
one-shot final report), not model skill.
"""

import contextlib
import io
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.data import flood_events, rainfall_store
from floodsense.data.replay import load_replay
from floodsense.features import build_features
from floodsense.features.build_features import (
    DataUnavailableError,
    build_feature_store,
    load_feature_store,
)
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.models import train as train_cli
from floodsense.models import training
from floodsense.models.artifact import FloodModel

SGT = settings.tzinfo
REPLAY_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "replay" / "2021-04-17_western_storm.json"
)
RAIN_COLUMNS = ["rain_5m", "rain_15m", "rain_30m", "rain_60m", "rain_120m", "rain_decay_72h"]


# --------------------------------------------------------------------------------------------
# Feature store builder
# --------------------------------------------------------------------------------------------


def _serve(monkeypatch: pytest.MonkeyPatch, snapshots: list[Any]) -> None:
    def load_snapshots(start: datetime, end: datetime) -> list[Any]:
        return [s for s in snapshots if start <= s.timestamp <= end]

    monkeypatch.setattr(rainfall_store, "load_snapshots", load_snapshots)


def _quiet(frame: pd.DataFrame) -> pd.Series:
    return frame["rain_120m"] < settings.active_rain_min_mm_120m


@pytest.fixture(scope="module")
def replay():
    return load_replay(REPLAY_FILE)


@pytest.fixture(scope="module")
def full_table(replay) -> pd.DataFrame:
    return compute_zone_feature_table(replay.snapshots, replay.stations)


@pytest.fixture(scope="module")
def april_store(replay, tmp_path_factory) -> Path:
    """April 2021 built from the replay (which sits entirely inside April)."""
    with pytest.MonkeyPatch.context() as mp:
        _serve(mp, replay.snapshots)
        return build_feature_store(2021, 2021, tmp_path_factory.mktemp("features"))


def test_build_writes_only_months_with_snapshots(april_store):
    files = sorted(p.relative_to(april_store).as_posix() for p in april_store.rglob("*.parquet"))
    assert files == ["year=2021/month=04.parquet"]


def test_build_output_is_sgt_april_and_pruned(april_store):
    out = load_feature_store(root=april_store)
    ts = out["timestamp"]
    assert isinstance(ts.dtype, pd.DatetimeTZDtype)
    local = ts.dt.tz_convert(SGT)
    assert (local >= pd.Timestamp("2021-04-01", tz=SGT)).all()
    assert (local < pd.Timestamp("2021-05-01", tz=SGT)).all()
    assert not _quiet(out).any()  # the pruning rule holds
    assert "reporting_stations" in out.columns
    assert out["ura_planning_area"].nunique() == 55


def test_build_round_trips_and_filters_years(april_store, full_table):
    out = load_feature_store(root=april_store)
    expected = full_table[~_quiet(full_table)]
    assert len(out) == len(expected) < len(full_table)
    assert len(load_feature_store([2021], root=april_store)) == len(out)
    with pytest.raises(DataUnavailableError):
        load_feature_store([2019], root=april_store)


def test_build_features_match_the_full_replay(april_store, full_table):
    """Rain features at storm-day timestamps equal the single-pass computation over the replay."""
    cutoff = pd.Timestamp("2021-04-17 11:00", tz=SGT)
    out = load_feature_store(root=april_store)
    got = out[out["timestamp"] >= cutoff].set_index(["ura_planning_area", "timestamp"]).sort_index()
    want = full_table[full_table["timestamp"] >= cutoff]
    want = want[~_quiet(want)].set_index(["ura_planning_area", "timestamp"]).sort_index()
    assert got.index.equals(want.index) and len(got) > 0
    np.testing.assert_allclose(got[RAIN_COLUMNS], want[RAIN_COLUMNS], rtol=0, atol=1e-9)


def test_warmup_is_used_for_decay_then_dropped(replay, tmp_path, monkeypatch):
    """Shift the replay 16 days earlier so its first three days fall in the March warm-up."""
    shift = pd.Timedelta(days=-16)
    shifted = [s.model_copy(update={"timestamp": s.timestamp + shift}) for s in replay.snapshots]
    assert shifted[0].timestamp < pd.Timestamp("2021-04-01", tz=SGT) < shifted[-1].timestamp
    _serve(monkeypatch, shifted)
    root = build_feature_store(2021, 2021, tmp_path)
    out = pd.read_parquet(root / "year=2021" / "month=04.parquet")
    march = pd.read_parquet(root / "year=2021" / "month=03.parquet")
    assert march["timestamp"].max() < pd.Timestamp("2021-04-01", tz=SGT)

    assert (out["timestamp"] >= pd.Timestamp("2021-04-01", tz=SGT)).all()  # warm-up dropped
    full = compute_zone_feature_table(shifted, replay.stations)
    want = full[full["timestamp"] >= pd.Timestamp("2021-04-01", tz=SGT)]
    want = want[~_quiet(want)]
    key = ["ura_planning_area", "timestamp"]
    got = out.set_index(key).sort_index()
    want = want.set_index(key).sort_index()
    assert got.index.equals(want.index)
    np.testing.assert_allclose(got[RAIN_COLUMNS], want[RAIN_COLUMNS], rtol=0, atol=1e-9)

    # Without the warm-up the decay feature would restart from zero, so it is not a no-op.
    april_only = [s for s in shifted if s.timestamp >= pd.Timestamp("2021-04-01", tz=SGT)]
    cold = compute_zone_feature_table(april_only, replay.stations)
    first = cold["timestamp"].min()
    warm_decay = full[full["timestamp"] == first].set_index("ura_planning_area")["rain_decay_72h"]
    cold_decay = cold[cold["timestamp"] == first].set_index("ura_planning_area")["rain_decay_72h"]
    assert (warm_decay - cold_decay).abs().max() > 1.0


def test_build_is_idempotent_and_warns_on_empty_months(replay, tmp_path, monkeypatch, caplog):
    _serve(monkeypatch, replay.snapshots)
    with caplog.at_level("WARNING", logger="FloodSense.BuildFeatures"):
        build_feature_store(2021, 2021, tmp_path)
    assert any("2021-03" in r.message and "skipped" in r.message for r in caplog.records)
    first = load_feature_store(root=tmp_path)
    build_feature_store(2021, 2021, tmp_path)  # overwrites April
    pd.testing.assert_frame_equal(first, load_feature_store(root=tmp_path))
    assert not list(tmp_path.rglob("*.tmp"))


def test_station_metadata_union_prefers_later_snapshots(replay):
    a, b = replay.snapshots[0], replay.snapshots[1]
    sid = next(iter(a.stations))
    moved = a.stations[sid].model_copy(update={"latitude": 1.0})
    b = b.model_copy(update={"stations": {**b.stations, sid: moved}})
    assert build_features._station_union([a, b])[sid].latitude == 1.0
    assert build_features._station_union([b, a])[sid].latitude == a.stations[sid].latitude


def test_build_without_a_rainfall_store_fails_with_a_pointer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "root_dir", tmp_path)  # no data/raw/rainfall here
    with pytest.raises(DataUnavailableError, match=r"docs/phase3-handoff\.md"):
        build_feature_store(2021, 2021, tmp_path)
    assert not list(tmp_path.rglob("*.parquet"))


def test_build_cli_reports_missing_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "root_dir", tmp_path)
    assert (
        build_features.main(
            ["--start-year", "2021", "--end-year", "2021", "--out-dir", str(tmp_path)]
        )
        == 1
    )


# --------------------------------------------------------------------------------------------
# load_training_data
# --------------------------------------------------------------------------------------------


def test_load_training_data_without_a_feature_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "root_dir", tmp_path)
    with pytest.raises(DataUnavailableError, match=r"docs/phase3-handoff\.md"):
        train_cli.load_training_data()


def test_load_training_data_with_stub_events(april_store, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "root_dir", tmp_path)
    target = tmp_path / "data" / "processed" / "features"
    target.parent.mkdir(parents=True)
    target.symlink_to(april_store, target_is_directory=True)
    with pytest.raises(DataUnavailableError, match=r"docs/phase3-handoff\.md"):
        train_cli.load_training_data()  # events are still a stub


def test_load_training_data_labels_rows(april_store, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(settings, "root_dir", tmp_path)
    target = tmp_path / "data" / "processed" / "features"
    target.parent.mkdir(parents=True)
    target.symlink_to(april_store, target_is_directory=True)
    event = SimpleNamespace(
        event_id="bt-2021-04-17",
        ura_planning_area="BUKIT TIMAH",
        timestamp_start=datetime(2021, 4, 17, 14, 15, tzinfo=SGT),
        timestamp_end=None,
        time_precision="exact",
    )
    unused = SimpleNamespace(
        event_id="missing-zone",
        ura_planning_area="NOT A ZONE",
        timestamp_start=datetime(2021, 4, 17, 14, 15, tzinfo=SGT),
        timestamp_end=None,
        time_precision="exact",
    )
    monkeypatch.setattr(flood_events, "load_flood_events", lambda path=None: [event, unused])
    with caplog.at_level("INFO", logger="FloodSense.Trainer"):
        data = train_cli.load_training_data()
    assert len(data.windows) == 2
    assert (data.label_prob == 1.0).any()
    assert any("missing-zone" in r.message for r in caplog.records)  # reported, not silent


# --------------------------------------------------------------------------------------------
# run_training / run_final_report
# --------------------------------------------------------------------------------------------


class Trained:
    def __init__(self, root: Path, run: training.TrainingRun, stdout: str):
        self.root, self.run, self.stdout = root, run, stdout
        self.uri = f"sqlite:///{root / 'mlflow.db'}"


def _train_in(root: Path, data, candidates: list[str] | None, budgets: bool) -> Trained:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "root_dir", root)
        if budgets:
            mp.setattr(settings, "false_alarm_budget_high", 50.0)
            mp.setattr(settings, "false_alarm_budget_moderate", 100.0)
        if candidates:
            real = training.train
            mp.setattr(train_cli, "train", lambda d: real(d, candidates=candidates))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run = train_cli.run_training(data, tracking_uri=f"sqlite:///{root / 'mlflow.db'}")
    return Trained(root, run, buf.getvalue())


@pytest.fixture(scope="module")
def trained(data, tmp_path_factory) -> Trained:
    """All three candidates, budgets unset."""
    return _train_in(tmp_path_factory.mktemp("unset"), data, None, budgets=False)


@pytest.fixture(scope="module")
def trained_budgeted(data, tmp_path_factory) -> Trained:
    """Logistic only (fast), budgets set so thresholds exist."""
    return _train_in(tmp_path_factory.mktemp("budgeted"), data, ["logistic"], budgets=True)


def test_training_writes_model_and_card(trained):
    model_path = trained.root / "models" / "flood_model.joblib"
    assert isinstance(joblib.load(model_path), FloodModel)
    card = json.loads((trained.root / "models" / "model_card.json").read_text())
    assert card["selected_candidate"] == trained.run.selected
    assert set(card["candidates"]) == {"rule_rain30_25mm", "logistic", "lightgbm"}
    assert card["thresholds"] is None
    assert any("budgets unset" in n for n in card["notes"])
    assert card["calibration_method"] in {"platt", "isotonic"}
    selected = card["candidates"][trained.run.selected]
    assert "pr_auc" in selected["row_metrics_oof"] and "ece" in selected
    assert [f["validation_year"] for f in selected["folds"]] == [2020, 2021, 2022, 2023]
    assert all(isinstance(f["train_max_timestamp"], str) for f in selected["folds"])
    assert card["provenance"]["candidate"] == trained.run.selected


def test_training_logs_nested_mlflow_runs(trained):
    import mlflow

    client = mlflow.MlflowClient(tracking_uri=trained.uri)
    experiment = client.get_experiment_by_name("floodsense")
    assert experiment is not None
    runs = client.search_runs([experiment.experiment_id])
    children = [r for r in runs if "mlflow.parentRunId" in r.data.tags]
    parents = [r for r in runs if "mlflow.parentRunId" not in r.data.tags]
    assert len(parents) == 1 and parents[0].data.params["selected"] == trained.run.selected
    assert {r.data.tags["mlflow.runName"] for r in children} == set(trained.run.results)
    for child in children:
        assert child.data.params["candidate"] == child.data.tags["mlflow.runName"]
        assert "rain_30m" in child.data.params["feature_columns"]
        assert child.data.params["validation_years"] == "2020,2021,2022,2023"
        assert "ece" in child.data.metrics
        assert not {"tp", "fp", "fn", "tn", "rows", "positives"} & set(child.data.metrics)
        names = {a.path for a in client.list_artifacts(child.info.run_id)}
        assert {"reliability.html", "tradeoff_curve.csv", "tradeoff_curve.html"} <= names


def test_training_prints_the_tradeoff_table(trained):
    assert f"Trade-off table for the selected candidate ({trained.run.selected})" in trained.stdout
    assert "false_episodes_per_zone_year" in trained.stdout
    assert "0.050" in trained.stdout  # first threshold row


def test_final_report_refuses_without_budgets(trained, data, monkeypatch):
    monkeypatch.setattr(settings, "root_dir", trained.root)
    with pytest.raises(ValueError, match="false-alarm budgets"):
        train_cli.run_final_report(data, tracking_uri=trained.uri)
    assert not (trained.root / "models" / "final_report.json").exists()  # test set not spent


def test_final_report_is_written_once(trained_budgeted, data, monkeypatch):
    t = trained_budgeted
    assert t.run.model.thresholds is not None
    card = json.loads((t.root / "models" / "model_card.json").read_text())
    assert card["thresholds"]["moderate"] <= card["thresholds"]["high"]
    monkeypatch.setattr(settings, "root_dir", t.root)
    path = t.root / "models" / "final_report.json"

    report = train_cli.run_final_report(data, tracking_uri=t.uri)
    on_disk = json.loads(path.read_text())  # strict JSON
    assert on_disk["test_years"] == [2024, 2025] == report["test_years"]
    assert on_disk["outcomes"] and {"event_id", "hit", "lead_minutes"} <= set(
        on_disk["outcomes"][0]
    )
    assert len(on_disk["hit_rate_ci90"]) == 2

    with pytest.raises(FileExistsError, match="--force"):
        train_cli.run_final_report(data, tracking_uri=t.uri)
    path.write_text("{}")
    train_cli.run_final_report(data, force=True, tracking_uri=t.uri)
    assert json.loads(path.read_text())["test_years"] == [2024, 2025]

    import mlflow

    client = mlflow.MlflowClient(tracking_uri=t.uri)
    experiment = client.get_experiment_by_name("floodsense")
    assert experiment is not None
    names = [r.data.tags["mlflow.runName"] for r in client.search_runs([experiment.experiment_id])]
    assert names.count("final-report") == 2


def test_final_report_needs_a_trained_model(data, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "root_dir", tmp_path)
    with pytest.raises(FileNotFoundError, match="run training first"):
        train_cli.run_final_report(data, tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}")


def test_force_requires_final_report():
    with pytest.raises(SystemExit):
        train_cli.main(["--force"])
