"""
FloodSense - Comprehensive Unit & Integration Test Suite.
Tests:
1. Spatial Engine: IDW normalization, distance calculation, dynamic gauge outage rebalancing.
2. Feature Engine: Multi-scale accumulations, 72h exponential soil moisture decay, storm rarity curves.
3. Stream Optimization: Zero-rain pruning efficiency.
4. Schema Contracts: Pydantic defensive data validation.
5. Ingestion Poller: landing-volume staging (parsing is covered in test_phase2_correctness.py).
6. Model Evaluation: Metric calculation (PR-AUC, FAR, Brier score).
"""

import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from floodsense.common.schemas import RainfallReading
from floodsense.features.feature_pipeline import (
    DECAY_FACTOR_PER_STEP,
    FeaturePipeline,
    StormRarityEstimator,
)
from floodsense.ingestion.poller import NEAPoller
from floodsense.spatial.idw_matrix import IDWMatrixEngine, haversine_distance_km
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS


def test_haversine_distance():
    """Verify haversine distance calculation between known Singapore coordinates."""
    lat_orchard, lon_orchard = 1.3048, 103.8318
    lat_changi, lon_changi = 1.3595, 103.9892
    dist = haversine_distance_km(lat_orchard, lon_orchard, lat_changi, lon_changi)
    assert 15.0 < dist < 25.0


def test_idw_weights_normalization():
    """Verify that IDW base weights for every planning area sum exactly to 1.0."""
    engine = IDWMatrixEngine()
    col_sums = np.sum(engine.base_weights, axis=0)
    assert np.allclose(col_sums, 1.0, atol=1e-6)
    assert engine.base_weights.shape == (len(engine.station_ids), len(engine.zone_names))


def test_dynamic_rebalancing_with_gauge_outages():
    """Verify dynamic weight rebalancing when 50% of weather stations drop offline."""
    engine = IDWMatrixEngine()
    all_stations = engine.station_ids

    # Simulate dropping half the stations
    active_subset = set(all_stations[: len(all_stations) // 2])
    rebalanced = engine.get_rebalanced_weights(active_subset)

    # Inactive stations must have exactly 0.0 weight
    inactive_indices = [i for i, s_id in enumerate(all_stations) if s_id not in active_subset]
    for idx in inactive_indices:
        assert np.all(rebalanced[idx, :] == 0.0)

    # Active weights must re-normalize to 1.0 per zone
    col_sums = np.sum(rebalanced, axis=0)
    assert np.allclose(col_sums, 1.0, atol=1e-6)


def test_idw_interpolation_output():
    """Verify IDW rainfall interpolation onto 55 planning zones."""
    engine = IDWMatrixEngine()
    station_readings = {s_id: 10.0 for s_id in engine.station_ids}
    zone_readings = engine.interpolate_rainfall(station_readings)

    assert len(zone_readings) == len(URA_PLANNING_AREAS)
    for zr in zone_readings:
        assert pytest.approx(zr.rainfall_mm, rel=1e-2) == 10.0
        assert zr.reporting_stations_count == len(engine.station_ids)


def test_antecedent_decay_half_life():
    """Verify that 72h decay factor exhibits ~24h (288 steps) half-life."""
    initial_moisture = 100.0
    decayed = initial_moisture * (DECAY_FACTOR_PER_STEP**288)
    assert pytest.approx(decayed, rel=1e-2) == 50.0


def test_zero_rain_stream_pruning():
    """Verify that zero-rain dry intervals are pruned to save >80% compute footprint."""
    feat_pipe = FeaturePipeline()
    dates = pd.date_range("2026-01-01", periods=200, freq="5min")
    dummy_df = pd.DataFrame(
        {"ura_planning_area": ["BISHAN"] * 200, "timestamp": dates, "rainfall_mm": [0.0] * 200}
    )
    processed = feat_pipe.process_batch_dataframe(dummy_df, prune_zero_rain=True)
    assert len(processed) == 0


def test_storm_rarity_score():
    """Verify that high rainfall bursts produce high rarity scores and return periods."""
    estimator = StormRarityEstimator()
    (score_low, score_extreme), (rp_low, rp_extreme) = estimator.score_array(
        "BUKIT TIMAH", np.array([5.0, 85.0])
    )

    assert score_low < score_extreme
    assert rp_low < rp_extreme
    assert score_extreme >= 0.95
    assert rp_extreme >= 5.0


def test_pydantic_schema_validation():
    """Verify that schemas enforce physical limits and handle valid/invalid inputs."""
    valid = RainfallReading(station_id="S104", timestamp=datetime.now(), rainfall_mm=25.5)
    assert valid.rainfall_mm == 25.5

    with pytest.raises(ValueError):
        RainfallReading(station_id="S104", timestamp=datetime.now(), rainfall_mm=150.0)


def test_poller_stages_raw_payload(tmp_path):
    """The poller writes raw payloads to the landing volume unchanged."""
    payload = {"code": 0, "data": {"stations": [], "readings": []}}
    staged = NEAPoller(landing_dir=tmp_path).stage_payload_to_volume(payload, "x.json")
    assert json.loads(staged.read_text()) == payload


# --- Scoring unknown inputs, and the IDW engine's defaults ---------------------------------------


def _scoring_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ura_planning_area": ["BUKIT TIMAH"] * 3,
            "rain_5m": [4.0, 4.0, 4.0],
            "rain_15m": [12.0, 12.0, 12.0],
            "rain_30m": [24.0, 24.0, 24.0],
            "rain_60m": [48.0, np.nan, 48.0],
            "rain_120m": [60.0, 60.0, np.nan],
            "rain_decay_72h": [50.0] * 3,
            "storm_rarity_score": [0.0] * 3,
        }
    )


def test_nan_feature_scores_no_data_not_low_or_an_error():
    from floodsense.models.artifact import load_model
    from floodsense.models.scoring import score_zone_features

    out = score_zone_features(_scoring_frame(), load_model())
    assert list(out["risk_tier"][1:]) == ["No data", "No data"]
    assert out["flood_probability"][1:].isna().all()
    assert out["flood_probability"].iloc[0] >= 0 and out["risk_tier"].iloc[0] != "No data"
    # The heuristic only reads rain_30m and rain_120m, so only a NaN rain_120m makes it unknown.
    heur = score_zone_features(_scoring_frame(), None)
    assert heur["risk_tier"].iloc[2] == "No data" and heur["risk_tier"].iloc[1] != "No data"


def test_empty_stations_or_zones_are_not_swapped_for_the_defaults():
    assert IDWMatrixEngine(stations={}).stations == {}
    assert IDWMatrixEngine(zones={}).zones == {}
