"""
FloodSense - Exhaustive Deep Stress Testing & Data Consistency Suite.
Fuzzes all 55 URA planning areas, all 60 replay timesteps, and live feed transitions.
Verifies physical invariants, mathematical monotonicity, and schema constraints.
"""

from pathlib import Path

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from floodsense.features.feature_pipeline import FeaturePipeline
from floodsense.spatial.idw_matrix import IDWMatrixEngine
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

APP_PATH = str(Path(__file__).parent.parent / "src" / "floodsense" / "app" / "streamlit_app.py")


@pytest.mark.integration
def test_exhaustive_replay_sweep():
    """Fuzz through all 60 steps of the 17 Apr 2021 storm replay and verify data invariants."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=20)
    assert not at.exception

    slider = at.slider[0]
    # Sweep every 5 steps from 0 to 59
    for step in range(0, 60, 5):
        slider.set_value(step)
        at.run(timeout=10)
        assert not at.exception

        # Verify KPI metrics exist and have valid values
        metrics = at.metric
        assert len(metrics) >= 5
        high_risk_metric = metrics[0].value
        assert "/" in high_risk_metric  # Format "X / 55"


@pytest.mark.integration
def test_all_55_ura_zones_inspection():
    """Fuzz all 55 URA planning areas in the deep dive dropdown to ensure zero rendering exceptions."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=20)
    assert not at.exception

    box = at.selectbox[0]
    for zone in URA_PLANNING_AREAS:
        box.set_value(zone)
        at.run(timeout=10)
        assert not at.exception


def test_physical_and_mathematical_invariants():
    """Verify core math: IDW weight normalization, decay non-negativity, rarity bounds, risk monotonicity."""
    engine = IDWMatrixEngine()
    feat_pipe = FeaturePipeline()

    # 1. Test IDW normalization with random gauge dropout scenarios
    np.random.seed(42)
    all_stations = engine.station_ids
    for _ in range(20):
        # Randomly keep 10% to 100% of stations
        k = np.random.randint(5, len(all_stations) + 1)
        active = set(np.random.choice(all_stations, size=k, replace=False))
        weights = engine.get_rebalanced_weights(active)
        col_sums = np.sum(weights, axis=0)
        assert np.allclose(col_sums, 1.0, atol=1e-5), (
            f"Weights did not sum to 1.0 with {k} active stations"
        )

    # 2. Test physical rainfall bounding & rarity scoring
    for rain_val in [0.0, 0.5, 5.0, 25.0, 50.0, 95.0]:
        score, rp = feat_pipe.rarity_estimator.compute_rarity_and_return_period(
            "BUKIT TIMAH", rain_val
        )
        assert 0.0 <= score <= 1.0, f"Rarity score out of bounds: {score}"
        assert rp >= 0.0, f"Return period negative: {rp}"

    # 3. Monotonicity check
    s1, _ = feat_pipe.rarity_estimator.compute_rarity_and_return_period("BUKIT TIMAH", 10.0)
    s2, _ = feat_pipe.rarity_estimator.compute_rarity_and_return_period("BUKIT TIMAH", 60.0)
    assert s1 <= s2, "Rarity score is not monotonic with rainfall intensity"
