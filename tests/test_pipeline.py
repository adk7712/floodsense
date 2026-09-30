"""
FloodSense - Comprehensive Unit & Integration Test Suite.
Tests:
1. Spatial Engine: IDW normalization, distance calculation, dynamic gauge outage rebalancing.
2. Feature Engine: Multi-scale accumulations, 72h exponential soil moisture decay, storm rarity curves.
3. Stream Optimization: Zero-rain pruning efficiency.
4. Schema Contracts: Pydantic defensive data validation.
5. Ground Truth Extractor: Unstructured alert parsing and target label attachment.
6. Ingestion Poller: NEA API response validation and defensive synthetic fallback.
7. Model Evaluation: Metric calculation (PR-AUC, FAR, Brier score).
8. Replay Loader: 17 April 2021 storm slice verification.
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

from src.common.schemas import RainfallReading, ZoneRainfall, FloodEvent, RiskTier
from src.spatial.idw_matrix import IDWMatrixEngine, haversine_distance_km
from src.spatial.singapore_geo import URA_PLANNING_AREAS, NEA_WEATHER_STATIONS, create_singapore_geojson
from src.features.feature_pipeline import FeaturePipeline, StormRarityEstimator, DECAY_FACTOR_PER_STEP
from src.features.ground_truth_extractor import GroundTruthExtractor
from src.ingestion.poller import NEAPoller
from src.models.train import evaluate_model_predictions, FEATURE_COLUMNS
from src.data.synthetic_or_historical_loader import generate_april_2021_replay_slice


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
    active_subset = set(all_stations[:len(all_stations) // 2])
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
    decayed = initial_moisture * (DECAY_FACTOR_PER_STEP ** 288)
    assert pytest.approx(decayed, rel=1e-2) == 50.0


def test_zero_rain_stream_pruning():
    """Verify that zero-rain dry intervals are pruned to save >80% compute footprint."""
    feat_pipe = FeaturePipeline()
    dates = pd.date_range("2026-01-01", periods=200, freq="5min")
    dummy_df = pd.DataFrame({
        "ura_planning_area": ["BISHAN"] * 200,
        "timestamp": dates,
        "rainfall_mm": [0.0] * 200
    })
    processed = feat_pipe.process_batch_dataframe(dummy_df, prune_zero_rain=True)
    assert len(processed) == 0


def test_storm_rarity_score():
    """Verify that high rainfall bursts produce high rarity scores and return periods."""
    estimator = StormRarityEstimator()
    score_low, rp_low = estimator.compute_rarity_and_return_period("BUKIT TIMAH", 5.0)
    score_extreme, rp_extreme = estimator.compute_rarity_and_return_period("BUKIT TIMAH", 85.0)

    assert score_low < score_extreme
    assert rp_low < rp_extreme
    assert score_extreme >= 0.95
    assert rp_extreme >= 5.0


def test_pydantic_schema_validation():
    """Verify that schemas enforce physical limits and handle valid/invalid inputs."""
    valid = RainfallReading(
        station_id="S104",
        timestamp=datetime.now(),
        rainfall_mm=25.5
    )
    assert valid.rainfall_mm == 25.5

    with pytest.raises(ValueError):
        RainfallReading(
            station_id="S104",
            timestamp=datetime.now(),
            rainfall_mm=150.0
        )


def test_ground_truth_extractor():
    """Verify text alert parsing and label attachment."""
    extractor = GroundTruthExtractor()
    alert_text = "Heavy rain causing flash flood along Dunearn Road near Sime Darby Centre. Impassable to vehicles."
    event = extractor.parse_unstructured_alert(alert_text, "2026-10-01T15:00:00")

    assert event is not None
    assert event.ura_planning_area == "BUKIT TIMAH"
    assert event.severity == "Severe"

    # Test label attachment
    test_df = pd.DataFrame([{
        "ura_planning_area": "BUKIT TIMAH",
        "timestamp": "2021-04-17T14:00:00"  # 15 min before known 14:15 flood
    }])
    labeled = extractor.attach_labels_to_feature_df(test_df, lead_time_minutes=60)
    assert labeled.iloc[0]["flood_within_60min"] == 1


def test_poller_and_validation(tmp_path):
    """Verify NEA poller parsing and mock data generation."""
    poller = NEAPoller(landing_dir=str(tmp_path))
    payload = poller._generate_synthetic_payload()
    valid_readings = poller.parse_and_validate(payload)

    assert len(valid_readings) > 0
    staged = poller.stage_payload_to_volume(payload)
    assert staged.exists()


def test_model_evaluation_metrics():
    """Verify that evaluate_model_predictions computes PR-AUC and False Alarm Rate."""
    y_true = np.array([0, 0, 0, 0, 1, 1])
    y_prob = np.array([0.05, 0.1, 0.2, 0.8, 0.9, 0.95])
    metrics = evaluate_model_predictions(y_true, y_prob, threshold=0.5)

    assert "pr_auc" in metrics
    assert "false_alarm_rate" in metrics
    assert metrics["pr_auc"] > 0.8
    assert metrics["recall"] == 1.0


def test_geojson_generation():
    """Verify GeoJSON generation for 55 planning areas."""
    gj = create_singapore_geojson()
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == len(URA_PLANNING_AREAS)


def test_replay_slice_generation(tmp_path):
    """Verify replay slice generation."""
    out_file = tmp_path / "replay_test.json"
    p = generate_april_2021_replay_slice(str(out_file))
    assert p.exists()
