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
* Kriging variance: the system is ``[[Gamma, 1], [1', 0]] [lambda; mu] = [gamma0; 1]``, so the
  ordinary-kriging variance is ``sigma2 = lambda' gamma0 + mu`` (this sign of ``mu`` goes with
  this layout). It is in the variogram's units; ``sigma2 = 0`` at a gauge when the nugget is
  zero. With a nugget the variogram still has ``gamma(0) = 0`` at a gauge (exact interpolation),
  so the variance there is 0 too, while at any other point it is at least about the nugget.
* The OK system is solved with a Lagrange multiplier (weights sum to 1). Negative predictions are
  clipped to 0 mm. Fields with fewer than two gauges, a singular system, or a constant field
  fall back to the mean of the gauges; the fallbacks are counted.
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from scipy.linalg import LinAlgWarning, lu_factor, lu_solve
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

    def predict_with_variance_from_gamma(
        self, gamma_obs: FloatArray, gamma_target: FloatArray, values: FloatArray
    ) -> tuple[float, float]:
        """
        Like ``predict_from_gamma`` but also returns the kriging variance (normalised units).

        ``sigma2 = lambda' gamma_target + mu``, clipped at 0. Fallback cases (fewer than 2
        gauges, constant field, singular system) return variance 0. The mean is clipped >= 0.
        """
        mean = self.predict_from_gamma(gamma_obs, gamma_target, values)
        if self.last_weights is None:
            return mean, 0.0
        n = len(values)
        a = np.ones((n + 1, n + 1))
        a[:n, :n] = gamma_obs
        a[n, n] = 0.0
        sol = np.linalg.solve(a, np.append(gamma_target, 1.0))
        return mean, float(max(sol[:n] @ gamma_target + sol[n], 0.0))

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


def haversine_matrix_km(
    lat1: FloatArray, lon1: FloatArray, lat2: FloatArray, lon2: FloatArray
) -> FloatArray:
    """Pairwise great-circle distance (km), shape (len(lat1), len(lat2)); same R as the IDW code."""
    p1, p2 = np.radians(lat1)[:, None], np.radians(lat2)[None, :]
    dphi = p2 - p1
    dlam = np.radians(lon2)[None, :] - np.radians(lon1)[:, None]
    a = np.sin(dphi / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2.0) ** 2
    return 6371.0 * 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))


class KrigingUpperEngine:
    """
    Vectorised ordinary kriging of many time steps onto fixed targets, mean + k kriging SD.

    Same interface as ``IDWMatrixEngine.interpolate_matrix``: ``station_values`` is (T, S) with
    NaN for gauges that did not report; the result is (T, Z). For each step only reporting gauges
    enter the system (missing is never read as 0). The kriging weights and normalised variance
    depend only on *which* gauges report, so they are computed once per distinct reporting set
    (one LU factorisation, all Z targets as right-hand sides) and reused for every step sharing it.

    Per step: ``upper = max(mean, 0) + upper_sd * sqrt(max(sigma2_norm, 0) * var_step)`` where
    ``var_step`` is the population variance (ddof=0) of that step's reporting gauge values, which
    scales the normalised variogram to mm^2. Steps with all reporting gauges equal return that
    constant (SD 0); with fewer than 2 gauges, the mean of the reporting gauges (SD 0); with none,
    0 (as ``IDWMatrixEngine.interpolate_matrix``); a singular system falls back to the gauge mean.
    """

    def __init__(
        self,
        station_lat: FloatArray,
        station_lon: FloatArray,
        target_lat: FloatArray,
        target_lon: FloatArray,
        variogram: Variogram,
        upper_sd: float = 1.0,
    ):
        self.variogram = variogram
        self.upper_sd = upper_sd
        self.gamma_ss = variogram.gamma(
            haversine_matrix_km(station_lat, station_lon, station_lat, station_lon)
        )
        self.gamma_sz = variogram.gamma(
            haversine_matrix_km(station_lat, station_lon, target_lat, target_lon)
        )
        self.n_targets = self.gamma_sz.shape[1]
        self.n_singular_sets = 0

    def _set_solution(self, idx: np.ndarray) -> tuple[FloatArray, FloatArray] | None:
        """Weights (n, Z) and normalised variance (Z,) for one reporting set; None if singular."""
        n = len(idx)
        a = np.ones((n + 1, n + 1))
        a[:n, :n] = self.gamma_ss[np.ix_(idx, idx)]
        a[n, n] = 0.0
        b = np.ones((n + 1, self.n_targets))
        b[:n] = self.gamma_sz[idx]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", LinAlgWarning)
            try:
                sol = lu_solve(lu_factor(a, check_finite=False), b, check_finite=False)
            except (np.linalg.LinAlgError, ValueError):
                return None
        if not np.all(np.isfinite(sol)):
            return None
        lam = sol[:n]
        return lam, np.maximum((lam * b[:n]).sum(axis=0) + sol[n], 0.0)

    def interpolate_matrix(self, station_values: FloatArray) -> FloatArray:
        """Upper rain (mean + upper_sd * SD, clipped at 0) for every step: (T, S) -> (T, Z)."""
        vals = np.asarray(station_values, dtype=np.float64)
        n_steps = vals.shape[0]
        out = np.zeros((n_steps, self.n_targets))
        reporting = ~np.isnan(vals)
        packed = np.packbits(reporting, axis=1)
        _, first, inverse = np.unique(packed, axis=0, return_index=True, return_inverse=True)
        inverse = np.asarray(inverse).ravel()
        for g, f in enumerate(first):
            rows = np.flatnonzero(inverse == g)
            idx = np.flatnonzero(reporting[f])
            n = len(idx)
            if n == 0:
                continue  # no gauge reporting: 0, as IDWMatrixEngine
            v = vals[np.ix_(rows, idx)]  # (Tg, n)
            if n < 2:
                out[rows] = np.maximum(v[:, :1], 0.0)
                continue
            sol = self._set_solution(idx)
            if sol is None:
                self.n_singular_sets += 1
                out[rows] = np.maximum(v.mean(axis=1, keepdims=True), 0.0)
                continue
            lam, sigma2 = sol
            mean = np.maximum(v @ lam, 0.0)  # (Tg, Z)
            sd = np.sqrt(sigma2[None, :] * v.var(axis=1)[:, None])
            res = mean + self.upper_sd * sd
            constant = np.ptp(v, axis=1) < 1e-12  # exact: constant field, SD 0
            res[constant] = np.maximum(v[constant, :1], 0.0)
            out[rows] = res
        return np.maximum(out, 0.0)
