"""
Tests for the training core (Phase 4, workstream C).

The fixture is SYNTHETIC and lives only here: storms in three zones over 2018-2025 where a flood
starts 20 minutes after 30-minute rain first exceeds 30 mm. It exists to exercise the protocol
(folds, leakage, calibration, thresholds), not to produce numbers anyone should quote.
"""

import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import MODEL_FEATURE_COLUMNS, settings
from floodsense.features.feature_pipeline import FeaturePipeline
from floodsense.labels.policy import label_rows
from floodsense.models import training
from floodsense.models.calibration import (
    expected_calibration_error,
    fit_calibrator,
    reliability_table,
)
from floodsense.models.training import LabelledData, choose_thresholds, final_report, train

SGT = "Asia/Singapore"
ZONES = ["BUKIT TIMAH", "BEDOK", "JURONG WEST"]


@pytest.fixture(scope="module")
def data() -> LabelledData:
    rng = np.random.default_rng(42)
    frames, events = [], []
    for year in range(2018, 2026):
        for zone in ZONES:
            for s in range(15):
                start = pd.Timestamp(f"{year}-01-01", tz=SGT) + pd.Timedelta(
                    days=int(20 * s + rng.integers(0, 15)), hours=13
                )
                stamps = pd.date_range(start, periods=48, freq="5min")
                peak = rng.gamma(2.0, 3.5)  # mm per 5 min at the storm peak
                shape = np.exp(-(((np.arange(48) - 20) / 6.0) ** 2))
                rain = np.round(peak * shape + rng.uniform(0, 0.3, 48), 1)
                df = pd.DataFrame(
                    {"ura_planning_area": zone, "timestamp": stamps, "rainfall_mm": rain}
                )
                feats = FeaturePipeline().process_batch_dataframe(df, prune_zero_rain=False)
                frames.append(feats)
                crossing = feats.index[feats["rain_30m"] > 30.0]
                if len(crossing):
                    t0 = feats.loc[crossing[0], "timestamp"] + pd.Timedelta(minutes=20)
                    events.append(
                        type(
                            "Ev",
                            (),
                            dict(
                                ura_planning_area=zone,
                                timestamp_start=t0.to_pydatetime(),
                                timestamp_end=(t0 + pd.Timedelta(minutes=45)).to_pydatetime(),
                                time_precision="exact",
                                event_id=f"{zone}-{year}-{s}",
                            ),
                        )()
                    )
    features = pd.concat(frames, ignore_index=True)
    labels, report = label_rows(features, events)
    from floodsense.labels.policy import event_window

    windows = [event_window(e) for e in events]
    assert not report.events_without_rows
    return LabelledData(features, labels["label_prob"], windows)


@pytest.fixture(scope="module")
def run(data):
    return train(data)


def test_training_runs_and_selects_a_candidate(run):
    assert run.selected in run.results
    assert set(run.results) == {"rule_rain30_25mm", "logistic", "lightgbm"}
    assert run.model.feature_columns == MODEL_FEATURE_COLUMNS
    assert run.model.calibrator is not None


def test_forward_chaining_folds_never_train_on_the_future(run):
    for result in run.results.values():
        years = [f["validation_year"] for f in result.folds]
        assert years == [2020, 2021, 2022, 2023]
        for fold in result.folds:
            assert fold["train_max_timestamp"] < pd.Timestamp(
                f"{fold['validation_year']}-01-01", tz=SGT
            )


def test_test_years_are_never_used_for_development(data, monkeypatch):
    seen = []
    real_fit = training.fit_candidate

    def spy(name, d, cols):
        seen.append(d.features["timestamp"].max())
        return real_fit(name, d, cols)

    monkeypatch.setattr(training, "fit_candidate", spy)
    train(data, candidates=["logistic"])
    assert seen and max(seen) < pd.Timestamp(f"{settings.test_start_year}-01-01", tz=SGT)


def test_model_learns_the_signal_and_is_calibrated(run):
    best = run.results[run.selected]
    base_rate = best.row["positives"] / best.row["rows"]
    assert best.row["pr_auc"] > 3 * base_rate
    assert best.ece < 0.1


def test_thresholds_are_not_chosen_without_budgets(run):
    assert run.model.thresholds is None
    assert any("budgets unset" in n for n in run.notes)


def test_final_report_refuses_without_thresholds(run, data):
    with pytest.raises(ValueError, match="false-alarm budgets"):
        final_report(run.model, data)


def test_final_report_with_budgets(data, monkeypatch):
    monkeypatch.setattr(settings, "false_alarm_budget_high", 50.0)
    monkeypatch.setattr(settings, "false_alarm_budget_moderate", 100.0)
    run = train(data, candidates=["logistic"])
    assert run.model.thresholds and run.model.thresholds["moderate"] <= run.model.thresholds["high"]
    report = final_report(run.model, data)
    assert report["test_years"] == [2024, 2025]
    lo, hi = report["hit_rate_ci90"]
    assert 0.0 <= lo <= report["events_high"]["hit_rate"] <= hi <= 1.0


def test_choose_thresholds_requires_both_budgets(monkeypatch):
    curve = pd.DataFrame(
        {
            "threshold": [0.2, 0.5],
            "hit_rate": [1.0, 0.5],
            "false_episodes_per_zone_year": [4.0, 1.0],
        }
    )
    monkeypatch.setattr(settings, "false_alarm_budget_high", 1.0)
    monkeypatch.setattr(settings, "false_alarm_budget_moderate", None)
    assert choose_thresholds(curve) is None
    monkeypatch.setattr(settings, "false_alarm_budget_moderate", 5.0)
    assert choose_thresholds(curve) == {"moderate": 0.2, "high": 0.5}


def test_calibration_fixes_overconfident_scores():
    rng = np.random.default_rng(0)
    raw = rng.uniform(0, 1, 20000)
    y = (rng.uniform(0, 1, raw.size) < raw**3).astype(float)  # true risk far below the raw score
    before = expected_calibration_error(reliability_table(raw, y))
    cal = fit_calibrator(raw, y, method="isotonic")
    after = expected_calibration_error(reliability_table(cal(raw), y))
    assert before > 0.15 and after < 0.02
