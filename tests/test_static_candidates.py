"""
Tests for the static-feature candidates (``floodsense.models.static_candidates``) and the
selection report (``floodsense.models.select_candidates``). Offline.

The ``data`` fixture (tests/conftest.py) is SYNTHETIC; it checks the protocol, not model skill.
"""

import joblib
import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import MODEL_FEATURE_COLUMNS, settings
from floodsense.models import select_candidates, static_candidates
from floodsense.models.artifact import FloodModel
from floodsense.models.scoring import score_zone_features
from floodsense.models.select_candidates import ablation, paired_difference_ci, run_selection
from floodsense.models.static_candidates import (
    STATIC_COLUMNS,
    RuleStaticAdjustment,
    StaticFloodModel,
    fit_static_candidate,
    load_zone_static,
)
from floodsense.models.training import RuleModel, cross_validate

SGT = "Asia/Singapore"
N_RAIN = len(MODEL_FEATURE_COLUMNS)
RAIN60 = MODEL_FEATURE_COLUMNS.index("rain_60m")


# --------------------------------------------------------------------------------------------
# Zone lookup and artifact
# --------------------------------------------------------------------------------------------


def test_zone_static_has_every_zone_and_three_values():
    table = load_zone_static()
    assert len(table) == 55
    assert all(len(v) == len(STATIC_COLUMNS) for v in table.values())
    assert all(np.isfinite(v).all() for v in table.values())


def test_zone_static_refuses_blanks_and_missing_zones(tmp_path):
    df = pd.read_csv(static_candidates.zone_static_path())
    blank = df.copy()
    blank.loc[0, "elevation_m_mean"] = np.nan
    blank.to_csv(tmp_path / "blank.csv", index=False)
    with pytest.raises(ValueError, match="blank"):
        load_zone_static(tmp_path / "blank.csv")
    df.iloc[1:].to_csv(tmp_path / "short.csv", index=False)
    with pytest.raises(ValueError, match="55"):
        load_zone_static(tmp_path / "short.csv")


def _rows(zones, rain60):
    n = len(zones)
    df = pd.DataFrame({c: np.zeros(n) for c in MODEL_FEATURE_COLUMNS})
    df["ura_planning_area"] = zones
    df["timestamp"] = pd.date_range("2021-04-17 12:00", periods=n, freq="5min", tz=SGT)
    df["rain_60m"] = rain60
    df["rain_120m"] = np.asarray(rain60) + 1.0
    df["rain_30m"] = np.asarray(rain60) / 2
    return df


def test_static_model_appends_the_zone_values_and_keeps_rain_feature_columns():
    table = load_zone_static()
    model = StaticFloodModel(
        estimator=RuleModel(RAIN60),
        feature_columns=MODEL_FEATURE_COLUMNS,
        rarity_quantiles={},
        zone_static=table,
    )
    rows = _rows(["BEDOK", "BUKIT TIMAH", "BEDOK"], [1.0, 2.0, 3.0])
    X = model.prepare(rows)
    assert X.shape == (3, N_RAIN + len(STATIC_COLUMNS))
    assert np.allclose(X[0, N_RAIN:], table["BEDOK"])
    assert np.allclose(X[1, N_RAIN:], table["BUKIT TIMAH"])
    assert model.feature_columns == MODEL_FEATURE_COLUMNS  # register_model.py builds input from it
    assert isinstance(model, FloodModel)


def test_unknown_zone_is_an_error_not_a_default():
    model = StaticFloodModel(
        estimator=RuleModel(RAIN60),
        feature_columns=MODEL_FEATURE_COLUMNS,
        rarity_quantiles={},
        zone_static=load_zone_static(),
    )
    with pytest.raises(ValueError, match="No static values"):
        model.prepare(_rows(["ATLANTIS"], [5.0]))


# --------------------------------------------------------------------------------------------
# Rule plus static adjustment
# --------------------------------------------------------------------------------------------


def _design(rng, n_zones=20, rows_per_zone=4000, effect=1.5):
    """Rows where flood odds rise with 60-minute rain and with the first static column."""
    zone_vals = rng.normal(size=(n_zones, len(STATIC_COLUMNS)))
    zone = np.repeat(np.arange(n_zones), rows_per_zone)
    rain = rng.gamma(1.5, 10.0, size=zone.size)
    X = np.zeros((zone.size, N_RAIN + len(STATIC_COLUMNS)))
    X[:, RAIN60] = rain
    X[:, N_RAIN:] = zone_vals[zone]
    eta = -5 + 2.0 * np.log(rain / 25 + 1e-6) + effect * zone_vals[zone, 0]
    y = (rng.uniform(size=zone.size) < 1 / (1 + np.exp(-eta))).astype(float)
    return X, y


def test_fit_needs_the_event_count():
    rng = np.random.default_rng(0)
    X, y = _design(rng, n_zones=5, rows_per_zone=500)
    with pytest.raises(ValueError, match="n_events"):
        RuleStaticAdjustment(RAIN60, N_RAIN).fit(X, y)


def test_without_static_the_ranking_is_the_rain_rule():
    rng = np.random.default_rng(1)
    X, y = _design(rng, n_zones=8, rows_per_zone=1000)
    est = RuleStaticAdjustment(RAIN60, N_RAIN, use_static=False, n_events=30).fit(X, y)
    assert est.rain_coef_ > 0
    ours = est.predict_proba(X)[:, 1]
    rule = RuleModel(RAIN60).predict_proba(X)[:, 1]
    # Without static columns the shipped score is the rule's own score, so calibration and the
    # threshold sweep see exactly what rule_rain60 gives them (the ablation baseline).
    assert np.allclose(ours, rule, atol=1e-6)


def test_heavy_prior_keeps_static_coefficients_near_zero_and_data_moves_them():
    rng = np.random.default_rng(2)
    X, y = _design(rng)
    shrunk = RuleStaticAdjustment(RAIN60, N_RAIN, prior_sd=1e-3, n_events=int(y.sum())).fit(X, y)
    assert np.abs(shrunk.static_coef_).max() < 1e-2
    free = RuleStaticAdjustment(RAIN60, N_RAIN, prior_sd=10.0, n_events=int(y.sum())).fit(X, y)
    assert free.static_coef_[0] > 0.5  # the true effect is positive and strong
    summary = free.summary()
    assert set(summary["static_coef_per_sd"]) == set(STATIC_COLUMNS)


def test_fewer_events_shrink_more():
    rng = np.random.default_rng(3)
    X, y = _design(rng)
    few = RuleStaticAdjustment(RAIN60, N_RAIN, n_events=3).fit(X, y)
    many = RuleStaticAdjustment(RAIN60, N_RAIN, n_events=300).fit(X, y)
    assert abs(few.static_coef_[0]) < abs(many.static_coef_[0])


# --------------------------------------------------------------------------------------------
# Fitting inside the CV protocol
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["rule_rain60_static", "logistic_static"])
def test_static_candidates_cross_validate_without_seeing_test_years(data, name):
    seen = []

    def spy(n, d, cols):
        seen.append(d.features["timestamp"].max())
        return fit_static_candidate(n, d, cols)

    result = cross_validate(name, data.development(), MODEL_FEATURE_COLUMNS, fit=spy)
    assert [f["validation_year"] for f in result.folds] == [2020, 2021, 2022, 2023]
    assert seen and max(seen) < pd.Timestamp(f"{settings.test_start_year}-01-01", tz=SGT)
    assert result.oof["prob"].between(0, 1).all()


def test_saved_static_model_scores_plain_feature_rows(data, tmp_path):
    model = fit_static_candidate("rule_rain60_static", data.development(), MODEL_FEATURE_COLUMNS)
    path = model.save(tmp_path / "m.joblib")
    loaded = joblib.load(path)
    assert isinstance(loaded, StaticFloodModel)
    rows = data.features.head(50).drop(columns=["storm_rarity_score"], errors="ignore")
    scored = score_zone_features(rows, loaded)
    assert scored["flood_probability"].between(0, 1).all()
    assert set(scored["risk_tier"]) <= {"Low", "Moderate", "High"}


# --------------------------------------------------------------------------------------------
# Selection report
# --------------------------------------------------------------------------------------------


def test_paired_difference_ci():
    a = [True] * 6 + [False] * 4
    assert paired_difference_ci(a, a) == (0.0, 0.0)
    lo, hi = paired_difference_ci([True] * 10, [False] * 10)
    assert lo == hi == 1.0
    with pytest.raises(ValueError):
        paired_difference_ci([True], [True, False])


def test_ablation_pairs_the_same_events():
    def summary(outcomes):
        tier = {"outcomes": outcomes, "false_episodes_per_zone_year": 1.0}
        return {"tiers": {"high": tier, "moderate": tier}}

    rows = ablation(
        {
            "rule_rain60_static": summary({"a": True, "b": False}),
            "rule_rain60_nostatic": summary({"a": False, "b": False}),
        }
    )
    assert len(rows) == 2
    assert rows[0]["gained"] == ["a"] and rows[0]["lost"] == []
    assert rows[0]["hits_with"] == 1 and rows[0]["hits_without"] == 0


def test_run_selection_writes_only_to_its_own_folder(data, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "false_alarm_budget_high", 50.0)
    monkeypatch.setattr(settings, "false_alarm_budget_moderate", 100.0)
    names = ["rule_rain60", "rule_rain60_static", "rule_rain60_nostatic"]
    payload = run_selection(data, out_dir=tmp_path, run_date="2026-01-01", candidates=names)
    assert set(payload["candidates"]) == set(names)
    assert payload["data"]["test_years_scored"] is False
    assert payload["best_static_candidate"] == "rule_rain60_static"
    assert (tmp_path / "selection_2026-01-01.json").exists()
    saved = joblib.load(tmp_path / "rule_rain60_static.joblib")
    assert isinstance(saved, StaticFloodModel) and saved.thresholds is not None
    assert len(payload["candidates"]["rule_rain60_static"]["fold_coefficients"]) == 4
    assert {r["tier"] for r in payload["ablation"]} == {"high", "moderate"}
    assert select_candidates.candidates_dir() == settings.models_dir / "candidates"
