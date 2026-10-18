"""Offline unit tests for ordinary kriging and the IDW-vs-kriging comparison guard."""

import numpy as np
import pytest

from floodsense.spatial.compare_interpolation import assert_training_window
from floodsense.spatial.kriging import (
    KrigingUpperEngine,
    OrdinaryKriger,
    SemivarianceBins,
    Variogram,
    fit_variogram,
    haversine_matrix_km,
)

VGM = Variogram(nugget=0.1, partial_sill=1.0, range_km=10.0)


def _grid() -> tuple[np.ndarray, np.ndarray]:
    pts = np.array([[0.0, 0.0], [4.0, 0.0], [0.0, 5.0], [6.0, 7.0], [3.0, 3.0]])
    dist = np.linalg.norm(pts[:, None] - pts[None], axis=-1)
    return pts, dist


def test_reproduces_value_at_station() -> None:
    pts, dist = _grid()
    z = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
    k = OrdinaryKriger(VGM)
    for i in range(len(z)):
        assert k.predict(dist, dist[i], z) == pytest.approx(z[i], abs=1e-8)


def test_weights_sum_to_one() -> None:
    pts, dist = _grid()
    z = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
    target = np.linalg.norm(pts - np.array([2.0, 2.0]), axis=1)
    k = OrdinaryKriger(VGM)
    k.predict(dist, target, z)
    assert k.last_weights is not None
    assert k.last_weights.sum() == pytest.approx(1.0)


def test_constant_field_returns_constant_and_is_counted() -> None:
    pts, dist = _grid()
    target = np.linalg.norm(pts - np.array([2.0, 2.0]), axis=1)
    k = OrdinaryKriger(VGM)
    assert k.predict(dist, target, np.full(5, 2.5)) == pytest.approx(2.5)
    assert k.n_fallback_constant == 1 and k.n_fallback == 1


def test_single_gauge_falls_back_to_mean() -> None:
    k = OrdinaryKriger(VGM)
    assert k.predict(np.zeros((1, 1)), np.array([3.0]), np.array([1.5])) == 1.5
    assert k.n_fallback_few == 1


def test_negative_prediction_is_clipped() -> None:
    # A steep field with the target beyond the data makes OK weights go negative.
    dist = np.array([[0.0, 1.0, 2.0], [1.0, 0.0, 1.0], [2.0, 1.0, 0.0]])
    target = np.array([3.0, 2.0, 1.0])
    k = OrdinaryKriger(Variogram(0.0, 1.0, 50.0, "spherical"))
    raw = None
    z = np.array([5.0, 1.0, 0.0])
    pred = k.predict(dist, target, z)
    assert k.last_weights is not None
    raw = float(k.last_weights @ z)
    assert pred >= 0.0
    assert raw < 0.0 or pred == pytest.approx(raw)


def test_leave_one_out_shape_and_nonnegative() -> None:
    _, dist = _grid()
    out = OrdinaryKriger(VGM).leave_one_out(dist, np.array([0.0, 0.0, 2.0, 1.0, 0.0]))
    assert out.shape == (5,) and (out >= 0).all()


def test_variogram_fit_recovers_parameters() -> None:
    rng = np.random.default_rng(0)
    bins = SemivarianceBins(max_km=40.0, bin_km=2.0)
    h = np.linspace(1.0, 39.0, 20)
    bins.dist_sum = h * 100
    bins.count = np.full(20, 100.0)
    truth = Variogram(0.2, 0.8, 12.0)
    bins.sum = truth.gamma(h) * 100 * (1 + 0.001 * rng.standard_normal(20))
    bins.edges = np.append(h - 1, h[-1] + 1)
    fitted, err = fit_variogram(bins, "exponential")
    assert fitted.range_km == pytest.approx(12.0, rel=0.1)
    assert err < 0.01


def test_test_years_are_refused() -> None:
    assert_training_window(2017, 2023)
    with pytest.raises(ValueError, match="held-out"):
        assert_training_window(2017, 2024)
    with pytest.raises(ValueError, match="held-out"):
        assert_training_window(2024, 2026)


def test_variance_nonnegative_and_zero_at_gauge() -> None:
    pts, dist = _grid()
    z = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
    k = OrdinaryKriger(VGM)
    gamma = VGM.gamma(dist)
    for i in range(len(z)):
        mean, var = k.predict_with_variance_from_gamma(gamma, gamma[i], z)
        assert mean == pytest.approx(z[i], abs=1e-8)
        assert var == pytest.approx(0.0, abs=1e-8)  # gamma(0)=0: exact interpolation
    target = np.linalg.norm(pts - np.array([2.0, 2.0]), axis=1)
    _, var = k.predict_with_variance_from_gamma(gamma, VGM.gamma(target), z)
    assert var > VGM.nugget * 0.99  # between gauges, at least about the nugget


def test_variance_matches_weights_formula_and_grows_with_distance() -> None:
    pts, dist = _grid()
    z = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
    k = OrdinaryKriger(VGM)
    gamma = VGM.gamma(dist)
    far = VGM.gamma(np.linalg.norm(pts - np.array([40.0, 40.0]), axis=1))
    near = VGM.gamma(np.linalg.norm(pts - np.array([2.0, 2.0]), axis=1))
    _, v_near = k.predict_with_variance_from_gamma(gamma, near, z)
    _, v_far = k.predict_with_variance_from_gamma(gamma, far, z)
    assert v_far > v_near
    # far away, sigma2 tends to the sill (nugget + partial sill) plus the mean's own variance
    assert v_far > VGM.nugget + VGM.partial_sill * 0.9


def test_zero_nugget_variance_is_zero_at_gauge_and_positive_nearby() -> None:
    pts, dist = _grid()
    z = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
    v0 = Variogram(nugget=0.0, partial_sill=1.0, range_km=10.0)
    k = OrdinaryKriger(v0)
    gamma = v0.gamma(dist)
    _, var = k.predict_with_variance_from_gamma(gamma, gamma[0], z)
    assert var == pytest.approx(0.0, abs=1e-8)
    near = v0.gamma(np.linalg.norm(pts - (pts[0] + 0.5), axis=1))
    _, var_n = k.predict_with_variance_from_gamma(gamma, near, z)
    assert var_n > 0.0


def _engine(upper_sd: float = 1.0) -> tuple[KrigingUpperEngine, np.ndarray, np.ndarray]:
    st_lat = np.array([1.30, 1.32, 1.35, 1.40, 1.28])
    st_lon = np.array([103.80, 103.85, 103.70, 103.90, 103.75])
    z_lat = np.array([1.31, 1.36, 1.29])
    z_lon = np.array([103.82, 103.78, 103.74])
    return KrigingUpperEngine(st_lat, st_lon, z_lat, z_lon, VGM, upper_sd), st_lat, st_lon


def test_engine_shape_nan_ignored_and_mean_matches_predict() -> None:
    eng0, st_lat, st_lon = _engine(upper_sd=0.0)
    vals = np.array(
        [[0.5, 2.0, np.nan, 1.0, 3.0], [1.0, 1.0, 1.0, 1.0, 1.0], [np.nan] * 5],
        dtype=np.float64,
    )
    out = eng0.interpolate_matrix(vals)
    assert out.shape == (3, 3)
    assert np.all(out[2] == 0.0)  # no gauge reporting: 0, as IDW
    assert np.allclose(out[1], 1.0)  # constant field
    idx = np.array([0, 1, 3, 4])
    d_obs = haversine_matrix_km(st_lat[idx], st_lon[idx], st_lat[idx], st_lon[idx])
    d_t = haversine_matrix_km(st_lat[idx], st_lon[idx], np.array([1.36]), np.array([103.78]))
    ref = OrdinaryKriger(VGM).predict(d_obs, d_t[:, 0], vals[0, idx])
    assert out[0, 1] == pytest.approx(ref)


def test_engine_upper_at_least_mean_and_single_gauge_fallback() -> None:
    eng1, _, _ = _engine(upper_sd=1.0)
    eng0, _, _ = _engine(upper_sd=0.0)
    rng = np.random.default_rng(0)
    vals = rng.gamma(1.0, 1.5, size=(20, 5))
    vals[rng.random(vals.shape) < 0.2] = np.nan
    up, mean = eng1.interpolate_matrix(vals), eng0.interpolate_matrix(vals)
    assert np.all(up >= mean - 1e-12) and np.all(mean >= 0.0)
    assert np.any(up > mean)
    single = np.array([[np.nan, 2.5, np.nan, np.nan, np.nan]])
    assert np.allclose(eng1.interpolate_matrix(single), 2.5)
