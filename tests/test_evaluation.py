"""Tests for evaluation metrics (Phase 4, workstream C)."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from floodsense.labels.policy import EventWindow
from floodsense.models.evaluation import (
    alert_episodes,
    evaluate_alerts,
    hit_rate_ci,
    row_metrics,
    select_threshold,
    tradeoff_curve,
)

SGT = "Asia/Singapore"
H = timedelta(minutes=60)


def ts(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"2021-04-17 {hhmm}", tz=SGT)


def timeline(zone: str, start: str, probs: list[float]) -> pd.DataFrame:
    stamps = pd.date_range(ts(start), periods=len(probs), freq="5min")
    return pd.DataFrame({"ura_planning_area": zone, "timestamp": stamps, "prob": probs})


def exact_event(zone="A", start="12:00", end="13:00", eid="ev") -> EventWindow:
    return EventWindow(eid, zone, ts(start), ts(start), ts(end), "exact")


# --- row level ----------------------------------------------------------------------------


def test_row_metrics_separates_fpr_from_false_alarm_ratio():
    y = np.array([0, 0, 0, 0, 1, 1])
    p = np.array([0.05, 0.1, 0.2, 0.8, 0.9, 0.95])
    m = row_metrics(y, p, threshold=0.5)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 0, 3)
    assert m["precision"] == pytest.approx(2 / 3)
    assert m["recall"] == 1.0
    assert m["false_positive_rate"] == pytest.approx(1 / 4)  # FP / (FP + TN)
    assert m["false_alarm_ratio"] == pytest.approx(1 / 3)  # FP / (FP + TP)


def test_legacy_metadata_false_alarm_ratio_was_93_percent():
    # models/model_metadata.json reported "false_alarm_rate": 0.0126 for TP=142, FP=1999, TN=157045
    y = np.concatenate([np.ones(142), np.zeros(1999), np.zeros(157045)])
    p = np.concatenate([np.ones(142), np.ones(1999), np.zeros(157045)])
    m = row_metrics(y, p, threshold=0.5)
    assert m["false_positive_rate"] == pytest.approx(0.0126, abs=1e-4)
    assert m["false_alarm_ratio"] == pytest.approx(1999 / 2141)
    assert m["false_alarm_ratio"] > 0.93


def test_row_metrics_rejects_uncertain_labels():
    with pytest.raises(ValueError, match="certain labels"):
        row_metrics(np.array([0, np.nan]), np.array([0.1, 0.2]), 0.5)


# --- episodes -----------------------------------------------------------------------------


def test_alert_episodes_are_contiguous_runs_per_zone():
    scored = pd.concat(
        [
            timeline("A", "10:00", [0, 0.8, 0.8, 0, 0.9, 0, 0, 0.7, 0.7, 0.7]),
            timeline("B", "10:00", [0.1] * 10),
        ]
    )
    eps = alert_episodes(scored, threshold=0.5)
    assert eps["steps"].tolist() == [2, 1, 3]
    assert (eps["ura_planning_area"] == "A").all()
    assert eps["start"].iloc[0] == ts("10:05")
    assert str(eps["start"].dt.tz) == SGT


def test_missing_rows_split_episodes():
    scored = timeline("A", "10:00", [0.9, 0.9, 0.9])
    scored = scored.drop(index=1)  # a pruned row is "no alert"
    assert len(alert_episodes(scored, 0.5)) == 2


# --- events -------------------------------------------------------------------------------


def _probs(start: str, end: str, on: list[tuple[str, str]], hi=0.9, lo=0.1) -> pd.DataFrame:
    stamps = pd.date_range(ts(start), ts(end), freq="5min")
    p = np.full(len(stamps), lo)
    for a, b in on:
        p[(stamps >= ts(a)) & (stamps <= ts(b))] = hi
    return pd.DataFrame({"ura_planning_area": "A", "timestamp": stamps, "prob": p})


def test_timely_alert_is_a_hit_with_lead_time():
    scored = _probs("09:00", "14:00", on=[("11:20", "11:40")])
    summary, outcomes, eps = evaluate_alerts(
        scored, [exact_event()], 0.5, zone_years=1.0, horizon=H
    )
    assert outcomes[0].hit
    assert outcomes[0].first_alert == ts("11:20")
    assert outcomes[0].lead_minutes == 40
    assert summary["false_episodes"] == 0
    assert not eps["is_false"].any()


def test_long_running_alert_counts_lead_from_episode_start():
    scored = _probs("09:00", "14:00", on=[("10:30", "11:55")])
    _, outcomes, _ = evaluate_alerts(scored, [exact_event()], 0.5, zone_years=1.0, horizon=H)
    assert outcomes[0].hit
    assert outcomes[0].first_alert == ts("11:00")  # first alert inside the horizon window
    assert outcomes[0].lead_minutes == 90


def test_late_alert_is_a_miss_but_not_a_false_alarm():
    scored = _probs("09:00", "14:00", on=[("12:05", "12:30")])  # after the flood started
    summary, outcomes, _ = evaluate_alerts(scored, [exact_event()], 0.5, zone_years=1.0, horizon=H)
    assert not outcomes[0].hit
    assert summary["false_episodes"] == 0


def test_unrelated_alert_is_a_false_alarm():
    scored = _probs("09:00", "14:00", on=[("09:10", "09:20"), ("11:30", "11:50")])
    summary, outcomes, eps = evaluate_alerts(
        scored, [exact_event()], 0.5, zone_years=2.0, horizon=H
    )
    assert outcomes[0].hit
    assert summary["episodes"] == 2
    assert summary["false_episodes"] == 1
    assert summary["episode_false_alarm_ratio"] == 0.5
    assert summary["false_episodes_per_zone_year"] == 0.5
    assert summary["false_alert_hours"] == pytest.approx(15 / 60)  # 3 steps x 5 min


def test_alert_in_another_zone_does_not_count():
    scored = _probs("09:00", "14:00", on=[("11:30", "11:50")])
    _, outcomes, _ = evaluate_alerts(
        scored, [exact_event(zone="B")], 0.5, zone_years=1.0, horizon=H
    )
    assert not outcomes[0].hit


def test_imprecise_event_accepts_alerts_inside_the_uncertainty():
    w = EventWindow("approx", "A", ts("11:45"), ts("12:15"), ts("13:15"), "approx_15min")
    scored = _probs("09:00", "14:00", on=[("12:00", "12:05")])
    _, outcomes, _ = evaluate_alerts(scored, [w], 0.5, zone_years=1.0, horizon=H)
    assert outcomes[0].hit  # the flood may not have started yet at 12:00
    assert outcomes[0].lead_minutes == -15  # conservative: measured against start_lo


# --- trade-off and selection ----------------------------------------------------------------


def test_tradeoff_curve_is_monotone_where_it_must_be():
    rng = np.random.default_rng(0)
    stamps = pd.date_range(ts("00:00"), ts("23:55"), freq="5min")
    scored = pd.concat(
        pd.DataFrame(
            {"ura_planning_area": z, "timestamp": stamps, "prob": rng.random(len(stamps)) ** 3}
        )
        for z in ["A", "B", "C"]
    )
    events = [exact_event("A", "06:00", "07:00", "a"), exact_event("B", "15:00", "16:00", "b")]
    curve = tradeoff_curve(scored, events, zone_years=3 / 365)
    assert (np.diff(curve["hit_rate"]) <= 0).all()  # higher threshold never catches more floods
    assert (np.diff(curve["false_alert_hours"]) <= 0).all()  # ...nor raises more alert time


def test_select_threshold_respects_budget():
    curve = pd.DataFrame(
        {
            "threshold": [0.2, 0.4, 0.6, 0.8],
            "hit_rate": [1.0, 0.8, 0.8, 0.4],
            "false_episodes_per_zone_year": [9.0, 3.0, 1.5, 0.5],
        }
    )
    assert select_threshold(curve, 2.0)["threshold"] == 0.6  # 0.4 hits as well but over budget
    assert select_threshold(curve, 5.0)["threshold"] == 0.6  # tie on hit rate -> quieter one
    assert select_threshold(curve, 10.0)["threshold"] == 0.2
    assert select_threshold(curve, 0.1) is None


def test_hit_rate_ci():
    assert hit_rate_ci([True] * 5) == (1.0, 1.0)
    lo, hi = hit_rate_ci([True, False, True, True, False, True, False, True])
    assert 0.0 <= lo < 0.625 < hi <= 1.0
