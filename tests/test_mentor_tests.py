"""Offline tests for the pre-registered mentor tests (B7) on SYNTHETIC data; never quote numbers."""

import json

import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.models import mentor_tests as mt
from tests.conftest import ZONES


@pytest.fixture
def budgets(monkeypatch):
    monkeypatch.setattr(settings, "false_alarm_budget_high", 2.5)
    monkeypatch.setattr(settings, "false_alarm_budget_moderate", 11.0)


@pytest.fixture
def zone_static(tmp_path):
    path = tmp_path / "zone_static.csv"
    pd.DataFrame({"ura_planning_area": ZONES, "built_up_land_pct": [0.0, 50.0, 100.0]}).to_csv(
        path, index=False
    )
    return path


def summary(event_score: float, high: dict[str, bool], moderate: dict[str, bool]) -> dict:
    tier = lambda o: {"outcomes": o, "false_episodes_per_zone_year": 1.0}  # noqa: E731
    return {"event_score": event_score, "tiers": {"high": tier(high), "moderate": tier(moderate)}}


def test_runoff_coefficients_use_fixed_rational_values(zone_static) -> None:
    c = mt.runoff_coefficients(zone_static)
    assert c == pytest.approx({ZONES[0]: 0.3, ZONES[1]: 0.6, ZONES[2]: 0.9})


def test_runoff_coefficients_refuse_blanks(tmp_path) -> None:
    path = tmp_path / "z.csv"
    pd.DataFrame({"ura_planning_area": ["A"], "built_up_land_pct": [None]}).to_csv(path)
    with pytest.raises(ValueError, match="complete"):
        mt.runoff_coefficients(path)


def test_runoff_input_is_rain_times_c(data, zone_static) -> None:
    dev = data.development()
    coeffs = mt.runoff_coefficients(zone_static)
    got = mt.runoff_input(dev, coeffs)
    expected = dev.features["rain_60m"] * dev.features["ura_planning_area"].map(coeffs)
    pd.testing.assert_series_equal(got, expected)


def test_with_rain_60m_changes_only_that_column(data) -> None:
    dev = data.development()
    swapped = mt.with_rain_60m(dev, dev.features["rain_60m"] * 2)
    assert (swapped.features["rain_60m"] == dev.features["rain_60m"] * 2).all()
    others = [c for c in dev.features.columns if c != "rain_60m"]
    pd.testing.assert_frame_equal(swapped.features[others], dev.features[others])
    assert dev.features["rain_60m"].equals(data.development().features["rain_60m"])  # no mutation


def test_with_rain_60m_refuses_missing_or_misaligned(data) -> None:
    dev = data.development()
    with pytest.raises(ValueError, match="missing"):
        mt.with_rain_60m(
            dev, dev.features["rain_60m"].where(dev.features.index != dev.features.index[0])
        )
    with pytest.raises(ValueError, match="aligned"):
        mt.with_rain_60m(dev, dev.features["rain_60m"].iloc[1:])


def test_krig_upper_input_refuses_partial_coverage(data, tmp_path) -> None:
    dev = data.development()
    part = dev.features[["ura_planning_area", "timestamp"]].iloc[:10].copy()
    part[mt.KRIG_COLUMN] = 1.0
    (tmp_path / "year=2018").mkdir()
    part.to_parquet(tmp_path / "year=2018" / "month=01.parquet")
    with pytest.raises(ValueError, match="no kriged-upper value"):
        mt.krig_upper_input(dev, tmp_path)


def test_krig_upper_input_aligns_by_zone_and_time(data, tmp_path) -> None:
    dev = data.development()
    full = dev.features[["ura_planning_area", "timestamp"]].sample(frac=1, random_state=0).copy()
    full[mt.KRIG_COLUMN] = dev.features.loc[full.index, "rain_60m"] + 1
    (tmp_path / "year=2018").mkdir()
    full.reset_index(drop=True).to_parquet(tmp_path / "year=2018" / "month=01.parquet")
    got = mt.krig_upper_input(dev, tmp_path)
    pd.testing.assert_series_equal(got, dev.features["rain_60m"] + 1, check_names=False)


def test_verdict_needs_all_three_criteria() -> None:
    ids = [f"e{i}" for i in range(10)]
    base = summary(0.5, dict.fromkeys(ids, False), dict.fromkeys(ids, False))
    win = summary(0.7, dict.fromkeys(ids, True), dict.fromkeys(ids, False))
    assert mt.verdict(win, base)["adopted"]
    small = summary(0.52, dict.fromkeys(ids, True), dict.fromkeys(ids, False))
    assert not mt.verdict(small, base)["criteria"]["a_event_score"]
    # High wins, but Moderate loses 2 floods: (c) fails.
    base2 = summary(0.5, dict.fromkeys(ids, False), {i: i in ("e0", "e1") for i in ids})
    lose = summary(0.7, dict.fromkeys(ids, True), dict.fromkeys(ids, False))
    v = mt.verdict(lose, base2)
    assert v["criteria"]["b_tier_ci_above_0"] and not v["criteria"]["c_other_tier_loss_le_1"]
    assert v["result"] == "not distinguishable, kept the simpler rule"


def test_verdict_c_counts_floods_lost_not_the_net_change() -> None:
    # Moderate wins clearly; High gains 2 and loses 2 (net 0, as T2's High): (c) fails.
    ids = [f"e{i}" for i in range(10)]
    base = summary(0.5, {i: i in ("e0", "e1") for i in ids}, dict.fromkeys(ids, False))
    cand = summary(0.7, {i: i in ("e2", "e3") for i in ids}, dict.fromkeys(ids, True))
    v = mt.verdict(cand, base)
    assert v["tiers"]["high"]["lost"] == ["e0", "e1"]
    assert v["tiers"]["high"]["candidate_hits"] == v["tiers"]["high"]["baseline_hits"]
    assert v["criteria"]["b_tier_ci_above_0"] and not v["criteria"]["c_other_tier_loss_le_1"]


def test_verdict_both_tiers_winning_is_enough() -> None:
    ids = [f"e{i}" for i in range(10)]
    base = summary(0.5, dict.fromkeys(ids, False), dict.fromkeys(ids, False))
    cand = summary(0.7, dict.fromkeys(ids, True), dict.fromkeys(ids, True))
    v = mt.verdict(cand, base)
    assert v["adopted"] and all(v["criteria"].values())


def test_verdict_refuses_different_floods() -> None:
    with pytest.raises(ValueError, match="same floods"):
        mt.verdict(summary(0.5, {"a": True}, {"a": True}), summary(0.5, {"b": True}, {"b": True}))


def test_t3_gate_counts_rain30_only_catches(tmp_path) -> None:
    def cand(mod):
        return {"tiers": {"moderate": {"outcomes": mod}}}

    payload = {
        "candidates": {
            "rule_rain30": cand({"a": True, "b": True, "c": True, "d": False}),
            "rule_rain60": cand({"a": True, "b": False, "c": False, "d": True}),
        }
    }
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(payload))
    gate = mt.t3_gate(path)
    assert gate["caught_by_rain30_only"] == ["b", "c"]
    assert gate["caught_by_rain60_only"] == ["d"]
    assert not gate["run_t3"]  # 2 or fewer: skipped

    payload["candidates"]["rule_rain30"]["tiers"]["moderate"]["outcomes"]["e"] = True
    payload["candidates"]["rule_rain60"]["tiers"]["moderate"]["outcomes"]["e"] = False
    path.write_text(json.dumps(payload))
    assert mt.t3_gate(path)["run_t3"]  # 3: runs


def test_run_tests_scores_only_2020_2023(data, zone_static, budgets) -> None:
    payload = mt.run_tests(data, ["t1"], zone_static=zone_static, run_date="2026-10-18")
    assert payload["data"]["test_years_scored"] is False
    assert payload["baseline"]["validation_years"] == [2020, 2021, 2022, 2023]
    t1 = payload["tests"]["t1_runoff"]
    assert t1["summary"]["validation_years"] == [2020, 2021, 2022, 2023]
    assert set(t1["verdict"]["tiers"]) == {"high", "moderate"}
    assert t1["verdict"]["result"] in ("adopt", "not distinguishable, kept the simpler rule")
