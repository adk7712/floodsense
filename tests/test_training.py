"""
Tests for the training core (Phase 4, workstream C).

The `data` fixture (tests/conftest.py) is SYNTHETIC: storms in three zones over 2018-2025 where
a flood starts 20 minutes after 30-minute rain first exceeds 30 mm. It exists to exercise the protocol
(folds, leakage, calibration, thresholds), not to produce numbers anyone should quote.
"""

import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import MODEL_FEATURE_COLUMNS, settings
from floodsense.models import training
from floodsense.models.calibration import (
    expected_calibration_error,
    fit_calibrator,
    reliability_table,
)
from floodsense.models.training import choose_thresholds, final_report, train

SGT = "Asia/Singapore"
ZONES = ["BUKIT TIMAH", "BEDOK", "JURONG WEST"]


@pytest.fixture(scope="module")
def run(data):
    return train(data)


def test_training_runs_and_selects_a_candidate(run):
    assert run.selected in run.results
    assert set(run.results) == {"rule_rain30", "rule_rain60", "logistic", "lightgbm"}
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


def test_zone_years_counts_a_partial_year_by_its_share():
    from floodsense.models.training import LabelledData

    stamps = pd.to_datetime(
        ["2024-01-01 00:00", "2025-06-15 12:00", "2026-07-01 23:55"]
    ).tz_localize(SGT)
    features = pd.DataFrame({"ura_planning_area": ["A", "B", "A"], "timestamp": stamps})
    d = LabelledData(features, pd.Series([0.0, 0.0, 0.0], index=features.index), [])
    # 2024 and 2025 in full, 2026 to 2 Jul 00:00 = 182/365 of the year; 2 zones
    assert d.zone_years() == pytest.approx(2 * (2 + 182 / 365), rel=1e-6)
