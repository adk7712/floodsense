"""
FloodSense - Ordinary kriging of station rainfall (numpy/scipy only).

Used by ``compare_interpolation`` to test whether kriging beats the IDW (p=2) interpolation that
the pipeline uses today. Not wired into the pipeline.

Method
------
* Isotropic variogram on haversine distances (km). Exponential by default: rainfall fields at the
  5-minute convective scale are rough close to the gauge and have no sharp finite range, which
  suits an exponential curve better than a spherical one (the comparison script reports the
  weighted fit error of both so the choice can be checked).
* Parametrised as ``gamma(h) = nugget + partial_sill * (1 - exp(-3 h / range_km))`` for h > 0 and
  ``gamma(0) = 0``, so kriging reproduces the observed value at a gauge exactly.
* The variogram is fitted once on pooled, per-step *normalised* semivariances (each step's
  semivariances are divided by that step's sample variance). Ordinary kriging weights depend only
  on the shape of the variogram, not on its overall scale, so a normalised variogram can be
  fitted on steps that are disjoint from the validation steps. That keeps the comparison free of
  leakage from the held-out gauge.
* The OK system is solved with a Lagrange multiplier (weights sum to 1). Negative predictions are
  clipped to 0 mm. Fields with fewer than two gauges, a singular system, or a constant field
  fall back to the mean of the gauges; the fallbacks are counted.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import curve_fit

FloatArray = np.ndarray


def _exponential(h: FloatArray, nugget: float, psill: float, range_km: float) -> FloatArray:
    return nugget + psill * (1.0 - np.exp(-3.0 * h / range_km))


def _spherical(h: FloatArray, nugget: float, psill: float, range_km: float) -> FloatArray:
    r = np.minimum(h / range_km, 1.0)
    return nugget + psill * (1.5 * r - 0.5 * r**3)


_MODELS: dict[str, Callable[[FloatArray, float, float, float], FloatArray]] = {
    "exponential": _exponential,
    "spherical": _spherical,
}


@dataclass(frozen=True)
class Variogram:
    """Isotropic variogram. ``range_km`` is the practical range (95% of the sill)."""

    nugget: float
    partial_sill: float
    range_km: float
    model: str = "exponential"

    def gamma(self, h: FloatArray) -> FloatArray:
        """Semivariance at distance ``h`` (km); exactly 0 at ``h == 0``."""
        h = np.asarray(h, dtype=np.float64)
        g = _MODELS[self.model](h, self.nugget, self.partial_sill, self.range_km)
        return np.where(h > 0.0, g, 0.0)


class SemivarianceBins:
    """Accumulates pooled, per-step normalised semivariances in distance bins."""

    def __init__(self, max_km: float = 40.0, bin_km: float = 2.0):
        self.edges = np.arange(0.0, max_km + bin_km, bin_km)
        n = len(self.edges) - 1
        self.sum = np.zeros(n)
        self.count = np.zeros(n)
        self.dist_sum = np.zeros(n)
        self.steps_used = 0
        self.steps_skipped_constant = 0

    def add_step(self, dist: FloatArray, values: FloatArray, min_variance: float = 1e-9) -> None:
        """Add one time step. ``dist`` is (n, n) in km, ``values`` has no NaN."""
        if len(values) < 3:
            return
        var = float(np.var(values, ddof=1))
        if var < min_variance:
            self.steps_skipped_constant += 1
            return
        iu = np.triu_indices(len(values), k=1)
        h = dist[iu]
        sv = 0.5 * (values[iu[0]] - values[iu[1]]) ** 2 / var
        idx = np.digitize(h, self.edges) - 1
        ok = (idx >= 0) & (idx < len(self.sum))
        np.add.at(self.sum, idx[ok], sv[ok])
        np.add.at(self.count, idx[ok], 1.0)
        np.add.at(self.dist_sum, idx[ok], h[ok])
        self.steps_used += 1

    def curve(self) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Bin mean distance, mean normalised semivariance and pair count (non-empty bins)."""
        keep = self.count > 0
        return (
            self.dist_sum[keep] / self.count[keep],
            self.sum[keep] / self.count[keep],
            self.count[keep],
        )


def fit_variogram(bins: SemivarianceBins, model: str = "exponential") -> tuple[Variogram, float]:
    """
    Weighted least-squares fit of the variogram to the binned curve.

    Returns the variogram and the pair-weighted RMS error of the fit (normalised units), so
    exponential and spherical can be compared.
    """
    if model not in _MODELS:
        raise ValueError(f"unknown variogram model: {model}")
    h, g, n = bins.curve()
    if len(h) < 4:
        raise ValueError("not enough distance bins with pairs to fit a variogram")
    fn = _MODELS[model]
    popt, _ = curve_fit(
        fn,
        h,
        g,
        p0=[0.2, max(float(g.max()) - 0.2, 0.1), 10.0],
        sigma=1.0 / np.sqrt(n),
        bounds=([0.0, 0.01, 0.5], [2.0, 5.0, 200.0]),
        maxfev=20000,
    )
    resid = g - fn(h, *popt)
    rmse = float(np.sqrt(np.sum(n * resid**2) / np.sum(n)))
    return Variogram(float(popt[0]), float(popt[1]), float(popt[2]), model), rmse


@dataclass
class OrdinaryKriger:
    """Ordinary kriging with a fixed variogram; counts how often it had to fall back."""

    variogram: Variogram
    n_calls: int = 0
    n_fallback_few: int = 0  # fewer than 2 gauges
    n_fallback_constant: int = 0  # constant field: kriging would return the constant anyway
    n_fallback_singular: int = 0  # singular or non-finite system
    last_weights: FloatArray | None = field(default=None, repr=False)

    @property
    def n_fallback(self) -> int:
        return self.n_fallback_few + self.n_fallback_constant + self.n_fallback_singular

    def predict_from_gamma(
        self, gamma_obs: FloatArray, gamma_target: FloatArray, values: FloatArray
    ) -> float:
        """
        Predict one point from precomputed semivariances.

        ``gamma_obs`` is (n, n) between gauges, ``gamma_target`` (n,) from gauges to the target,
        ``values`` (n,) with no NaN. Result is clipped to >= 0.
        """
        self.n_calls += 1
        self.last_weights = None
        n = len(values)
        if n < 2:
            self.n_fallback_few += 1
            return float(max(np.mean(values), 0.0)) if n else 0.0
        if float(np.ptp(values)) < 1e-12:
            self.n_fallback_constant += 1
            return float(max(values[0], 0.0))
        a = np.ones((n + 1, n + 1))
        a[:n, :n] = gamma_obs
        a[n, n] = 0.0
        b = np.append(gamma_target, 1.0)
        try:
            sol = np.linalg.solve(a, b)
        except np.linalg.LinAlgError:
            self.n_fallback_singular += 1
            return float(max(np.mean(values), 0.0))
        if not np.all(np.isfinite(sol)):
            self.n_fallback_singular += 1
            return float(max(np.mean(values), 0.0))
        self.last_weights = sol[:n]
        return float(max(float(sol[:n] @ values), 0.0))

    def predict(self, dist_obs: FloatArray, dist_target: FloatArray, values: FloatArray) -> float:
        """Predict one point from distances (km)."""
        return self.predict_from_gamma(
            self.variogram.gamma(dist_obs), self.variogram.gamma(dist_target), values
        )

    def leave_one_out(self, dist: FloatArray, values: FloatArray) -> FloatArray:
        """Predict every gauge from all the others. ``dist`` (n, n), ``values`` (n,), no NaN."""
        n = len(values)
        gamma = self.variogram.gamma(dist)
        out = np.empty(n)
        for i in range(n):
            keep = np.arange(n) != i
            out[i] = self.predict_from_gamma(
                gamma[np.ix_(keep, keep)], gamma[keep, i], values[keep]
            )
        return out
