"""Offline unit tests for ordinary kriging and the IDW-vs-kriging comparison guard."""

import numpy as np
import pytest

from floodsense.spatial.compare_interpolation import assert_training_window
from floodsense.spatial.kriging import (
    OrdinaryKriger,
    SemivarianceBins,
    Variogram,
    fit_variogram,
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
