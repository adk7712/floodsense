"""
Equivalence tests for the vectorised feature pipeline (Phase 4, workstream B).

``_reference_process_batch`` is a frozen copy of the original row-loop implementation. The
vectorised ``FeaturePipeline.process_batch_dataframe`` must reproduce it, except that features are
no longer rounded to 2 dp (rounding is a display concern), so rounded columns may differ by up to
0.005 and the rarity score by a correspondingly tiny amount.
"""

import time

import numpy as np
import pandas as pd
import pytest

from floodsense.features.feature_pipeline import (
    DECAY_FACTOR_PER_STEP,
    FeaturePipeline,
    StormRarityEstimator,
)
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

RAIN_COLUMNS = ["rain_5m", "rain_15m", "rain_30m", "rain_60m", "rain_120m", "rain_decay_72h"]
ROUNDING_TOLERANCE = 0.0051


def _reference_process_batch(
    df: pd.DataFrame, rarity: StormRarityEstimator, prune_zero_rain: bool
) -> pd.DataFrame:
    """The original (pre-Phase 4) row-loop implementation, frozen for comparison."""
    df = df.sort_values(by=["ura_planning_area", "timestamp"]).reset_index(drop=True)
    records = []
    for zone, group in df.groupby("ura_planning_area"):
        pub_mon = URA_PLANNING_AREAS.get(zone, {}).get("pub_monitored", 0)
        rain = group["rainfall_mm"].to_numpy()
        ts = group["timestamp"].tolist()
        n = len(rain)
        w = {k: np.convolve(rain, np.ones(k), mode="full")[:n] for k in (3, 6, 12, 24)}
        decay = np.zeros(n)
        running = 0.0
        for k in range(n):
            running = rain[k] + DECAY_FACTOR_PER_STEP * running
            decay[k] = running
        for k in range(n):
            r120, dec = round(w[24][k], 2), round(decay[k], 2)
            if prune_zero_rain and r120 == 0.0 and dec < 1.0:
                continue
            r30 = round(w[6][k], 2)
            score, rp = rarity.compute_rarity_and_return_period(zone, r30)
            records.append(
                {
                    "ura_planning_area": zone,
                    "timestamp": ts[k],
                    "rain_5m": float(rain[k]),
                    "rain_15m": round(w[3][k], 2),
                    "rain_30m": r30,
                    "rain_60m": round(w[12][k], 2),
                    "rain_120m": r120,
                    "rain_decay_72h": dec,
                    "storm_rarity_score": score,
                    "return_period_years": rp,
                    "pub_monitored": pub_mon,
                }
            )
    return pd.DataFrame(records)


def _random_zone_rain(n_steps: int, zones: list[str], seed: int) -> pd.DataFrame:
    """Gauge-like rain: mostly dry, bursty storms, values on a 0.2 mm tipping-bucket grid."""
    rng = np.random.default_rng(seed)
    stamps = pd.date_range("2021-04-14 00:00", periods=n_steps, freq="5min", tz="Asia/Singapore")
    frames = []
    for zone in zones:
        storm = rng.random(n_steps) < 0.04
        storm = np.convolve(storm, np.ones(12), mode="same") > 0  # ~1 h storms
        rain = np.where(storm, rng.gamma(1.2, 2.5, n_steps), 0.0)
        rain = np.round(rain / 0.2) * 0.2
        frames.append(
            pd.DataFrame({"ura_planning_area": zone, "timestamp": stamps, "rainfall_mm": rain})
        )
    return pd.concat(frames, ignore_index=True).sample(
        frac=1.0, random_state=seed
    )  # unsorted input


def _align(a: pd.DataFrame, b: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    key = ["ura_planning_area", "timestamp"]
    return (
        a.sort_values(key).reset_index(drop=True),
        b.sort_values(key).reset_index(drop=True),
    )


@pytest.mark.parametrize("prune", [False, True])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_vectorised_matches_reference(prune, seed):
    zones = ["BUKIT TIMAH", "BEDOK", "JURONG WEST", "TUAS"]
    df = _random_zone_rain(n_steps=3 * 288, zones=zones, seed=seed)
    pipe = FeaturePipeline()

    expected = _reference_process_batch(df, pipe.rarity_estimator, prune_zero_rain=prune)
    actual = pipe.process_batch_dataframe(df, prune_zero_rain=prune)
    expected, actual = _align(expected, actual)

    assert list(actual.columns) == list(expected.columns)
    assert len(actual) == len(expected)
    pd.testing.assert_series_equal(actual["timestamp"], expected["timestamp"])
    assert (actual["ura_planning_area"] == expected["ura_planning_area"]).all()
    for col in RAIN_COLUMNS:
        np.testing.assert_allclose(actual[col], expected[col], atol=ROUNDING_TOLERANCE, err_msg=col)
    np.testing.assert_allclose(
        actual["storm_rarity_score"], expected["storm_rarity_score"], atol=1e-3
    )
    np.testing.assert_allclose(
        actual["return_period_years"], expected["return_period_years"], atol=0.02
    )
    assert (actual["pub_monitored"] == expected["pub_monitored"]).all()


def test_rainfall_features_are_never_negative():
    # Long series: cumulative-sum differencing would leave float residue like -1e-12 here.
    df = _random_zone_rain(n_steps=365 * 288, zones=["BUKIT TIMAH"], seed=3)
    out = FeaturePipeline().process_batch_dataframe(df, prune_zero_rain=False)
    assert (out[RAIN_COLUMNS] >= 0).all().all()
    dry = out["rain_5m"] == 0
    assert (out.loc[dry & (out["rain_120m"] == 0), "rain_30m"] == 0).all()


def test_timezone_is_preserved():
    df = _random_zone_rain(n_steps=50, zones=["BISHAN"], seed=4)
    out = FeaturePipeline().process_batch_dataframe(df, prune_zero_rain=False)
    assert str(out["timestamp"].dt.tz) == "Asia/Singapore"


def test_full_year_all_zones_is_fast():
    """One year x 55 zones (~5.8M rows) must be practical; the row loop took many minutes."""
    df = _random_zone_rain(n_steps=365 * 288, zones=sorted(URA_PLANNING_AREAS), seed=5)
    start = time.perf_counter()
    out = FeaturePipeline().process_batch_dataframe(df, prune_zero_rain=False)
    elapsed = time.perf_counter() - start
    assert len(out) == len(df)
    assert elapsed < 60, f"took {elapsed:.1f}s"
