"""Pure helpers of docs/results/risk_multiple_2017_2023.py (offline; no feature store needed)."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_PATH = Path(__file__).resolve().parents[1] / "docs" / "results" / "risk_multiple_2017_2023.py"
_spec = importlib.util.spec_from_file_location("risk_multiple_2017_2023", _PATH)
assert _spec is not None and _spec.loader is not None
rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rm)


def test_years_never_reach_the_test_set():
    assert rm.LAST_YEAR < 2024
    assert rm.TEST_START.year == 2024


def test_tiers_match_risk_tier_boundaries():
    t = {"moderate": 0.005, "high": 0.007}
    prob = np.array([0.004, 0.005, 0.0069, 0.007, 0.02])
    m = rm.assign_tiers(prob, t)
    assert m["moderate"].tolist() == [False, True, True, False, False]
    assert m["high"].tolist() == [False, False, False, True, True]
    assert m["moderate_or_higher"].tolist() == [False, True, True, True, True]


def test_ratio_and_per_day_sums():
    day = np.array(["d1", "d1", "d2", "d2"])
    label = np.array([0.0, 1.0, 0.0, 0.0])
    masks = {"tier": np.array([False, True, False, False])}
    steps, floods, groups = rm.per_day_sums(day, label, masks)
    assert groups == ["all", "tier"]
    assert steps.sum(0).tolist() == [4, 1]
    n = rm.ratio(steps.sum(0), floods.sum(0))
    assert n[0] == 1.0 and n[1] == pytest.approx(4.0)  # 1/1 vs 1/4


def test_bootstrap_is_seeded_and_brackets_the_point():
    rng = np.random.default_rng(0)
    steps = np.column_stack([np.full(200, 100.0), rng.integers(0, 3, 200).astype(float)])
    floods = np.column_stack([rng.random(200) * 0.05, rng.random(200) * 0.02 * steps[:, 1]])
    a = rm.block_bootstrap(steps, floods, np.arange(200), 500, 1)
    b = rm.block_bootstrap(steps, floods, np.arange(200), 500, 1)
    assert np.array_equal(a, b)
    point = rm.ratio(steps.sum(0), floods.sum(0))[1]
    lo, hi = np.nanpercentile(a[:, 1], [5, 95])
    assert lo < point < hi


def test_display_rule():
    assert rm.display_phrase(8.0, 12.0)["phrase"] == "about 10 times"
    assert rm.display_phrase(10.6, 49.4)["phrase"] == "at least 10 times"
    assert rm.display_phrase(1.5, 9.0)["show"] is False
    assert rm.display_phrase(0.0, 300.0)["show"] is False
