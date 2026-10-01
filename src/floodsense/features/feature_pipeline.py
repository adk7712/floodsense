"""
FloodSense - Feature Engineering & Storm Rarity Engine.

Turns per-zone 5-minute rainfall into the model's feature table, fully vectorised
(no Python loop over rows; the only Python loop is over the planning areas):

1. Rolling rainfall accumulations over 15, 30, 60 and 120 minutes (3/6/12/24 rows). Windows are
   counted in rows within each zone and are partial at a zone's start.
2. 72-hour antecedent rainfall decay (half-life = 24 hours), an exponentially weighted running sum
   computed with a linear filter.
3. Storm rarity score and return period per zone, interpolated from empirical 30-minute rainfall
   quantiles (see ``StormRarityEstimator``).
4. PUB monitored flag.
5. Optional pruning of dry rows (no rain in the last 120 minutes and negligible antecedent decay).

Features are returned unrounded; rounding is a display concern. The only rounded comparison is the
pruning decision, which uses 2 dp so that the set of kept rows is unchanged.
"""

import math

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from floodsense.common.config import settings
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

# Half-life of 24 hours (288 five-minute intervals), derived from config
HALF_LIFE_STEPS = settings.decay_half_life_hours * 60.0 / settings.step_minutes
DECAY_LAMBDA_PER_STEP = math.log(2.0) / HALF_LIFE_STEPS  # ~0.002407 per 5-min step
DECAY_FACTOR_PER_STEP = math.exp(-DECAY_LAMBDA_PER_STEP)  # ~0.997596

# Rolling-window lengths in rows (15/30/60/120 minutes at 5-minute steps)
WINDOW_ROWS = {"rain_15m": 3, "rain_30m": 6, "rain_60m": 12, "rain_120m": 24}

# Quantile levels of the fitted rainfall distribution, and the return period (years) anchored at each
QUANTILE_LEVELS = np.array([0.50, 0.80, 0.95, 0.98, 0.995, 0.999])
RETURN_PERIOD_ANCHORS = np.array([0.2, 0.5, 1.0, 2.0, 5.0, 10.0])

FEATURE_COLUMNS = [
    "ura_planning_area",
    "timestamp",
    "rain_5m",
    "rain_15m",
    "rain_30m",
    "rain_60m",
    "rain_120m",
    "rain_decay_72h",
    "storm_rarity_score",
    "return_period_years",
    "pub_monitored",
]


class StormRarityEstimator:
    """
    Fits and estimates localized storm intensity rarity curves and return periods per planning area.
    Uses empirical quantile distributions calibrated on historical bursts.
    """

    def __init__(self):
        # Default calibration thresholds (mm per 30-min) for return periods [1yr, 2yr, 5yr, 10yr, 50yr]
        # Calibrated against Singapore Meteorological Service / PUB IDF curves
        self.zone_quantiles: dict[str, np.ndarray] = {}
        self.default_quantiles = np.array([15.0, 25.0, 38.0, 52.0, 70.0, 95.0])
        self._init_default_distributions()

    def _init_default_distributions(self):
        """Initializes default IDF curves for all 55 planning areas with micro-climate variations."""
        for zone in URA_PLANNING_AREAS:
            # Western and central zones (e.g. Bukit Timah, Jurong) historically receive slightly higher peaks
            factor = (
                1.1
                if zone in ["BUKIT TIMAH", "JURONG WEST", "CHOA CHU KANG", "SUNGEI KADUT"]
                else 1.0
            )
            self.zone_quantiles[zone] = self.default_quantiles * factor

    def fit_zone_distributions(self, historical_zone_rain_df: pd.DataFrame):
        """
        Fit empirical quantile thresholds from multi-year historical 30-min rain bursts.
        """
        if "rain_30m" not in historical_zone_rain_df.columns:
            return

        for zone, group in historical_zone_rain_df.groupby("ura_planning_area"):
            # Fit only on active rain bursts (rain_30m > 1.0mm)
            active_bursts = group["rain_30m"][group["rain_30m"] > 1.0].values
            if len(active_bursts) >= 50:
                q_vals = np.quantile(active_bursts, [0.50, 0.80, 0.95, 0.98, 0.995, 0.999])
                self.zone_quantiles[zone] = q_vals

    def score_array(self, zone: str, rain_30m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Vectorised storm rarity score [0.0, 1.0] and return period in years (unrounded).

        - rain <= 0.1 mm: (0, 0)
        - below the first quantile: score scales linearly to 0.5, return period 0.1
        - between the first and last quantile: piecewise-linear interpolation of the score on
          the quantile levels and of the return period on the anchors
        - at or above the last quantile: score 1.0, return period grows linearly beyond 10 years
        """
        r = np.asarray(rain_30m, dtype=np.float64)
        q = np.asarray(self.zone_quantiles.get(zone, self.default_quantiles), dtype=np.float64)

        # np.interp is continuous, so r == q[i] gives exactly the anchor value at i.
        score = np.interp(r, q, QUANTILE_LEVELS)
        rp = np.interp(r, q, RETURN_PERIOD_ANCHORS)

        below = r < q[0]
        score = np.where(below, 0.5 * r / q[0], score)
        rp = np.where(below, 0.1, rp)

        above = r >= q[-1]
        score = np.where(above, 1.0, score)
        rp = np.where(above, 10.0 + 20.0 * (r - q[-1]) / max(1.0, float(q[-1])), rp)

        quiet = r <= 0.1
        return np.where(quiet, 0.0, score), np.where(quiet, 0.0, rp)

    def compute_rarity_and_return_period(self, zone: str, rain_30m: float) -> tuple[float, float]:
        """
        Calculates storm rarity score [0.0, 1.0] and estimated return period in years,
        rounded for display (4 dp and 2 dp). See ``score_array`` for the unrounded batch version.
        """
        score, rp = self.score_array(zone, np.array([rain_30m], dtype=np.float64))
        return round(float(score[0]), 4), round(float(rp[0]), 2)


class FeaturePipeline:
    """
    Batch feature generation for FloodSense.
    """

    def __init__(self):
        self.rarity_estimator = StormRarityEstimator()

    def process_batch_dataframe(
        self, df: pd.DataFrame, prune_zero_rain: bool = True
    ) -> pd.DataFrame:
        """
        Transforms a batch DataFrame of zone rainfall into full engineered feature set.
        df columns required: ['ura_planning_area', 'timestamp', 'rainfall_mm']

        Rolling windows are counted in rows, so each zone's rows must be contiguous 5-minute
        steps (see ``floodsense.features.zone_features`` for building such a grid).

        Features are not rounded. With ``prune_zero_rain`` a row is dropped when its 120-minute
        rain rounds to 0.00 mm and its decay rounds to below 1.00 (the comparison is rounded; the
        returned values are not).
        """
        df = df.sort_values(by=["ura_planning_area", "timestamp"]).reset_index(drop=True)

        zone_codes, zone_names = pd.factorize(df["ura_planning_area"], sort=True)
        rain = df["rainfall_mm"].to_numpy(dtype=np.float64)
        n_rows = len(rain)

        out = {col: np.empty(n_rows) for col in WINDOW_ROWS}
        decay = np.empty(n_rows)
        score = np.empty(n_rows)
        rp = np.empty(n_rows)

        # Rows are sorted by zone, so each zone is one contiguous slice.
        bounds = np.flatnonzero(np.diff(zone_codes)) + 1
        starts = np.concatenate(([0], bounds))
        stops = np.concatenate((bounds, [n_rows]))
        for zone, lo, hi in zip(zone_names, starts, stops, strict=True):
            zone_rain = rain[lo:hi]
            n = hi - lo
            for col, window in WINDOW_ROWS.items():
                # Full convolution truncated to n gives partial windows at the zone's start.
                out[col][lo:hi] = np.convolve(zone_rain, np.ones(window), mode="full")[:n]
            decay[lo:hi] = lfilter([1.0], [1.0, -DECAY_FACTOR_PER_STEP], zone_rain)
            score[lo:hi], rp[lo:hi] = self.rarity_estimator.score_array(
                zone, out["rain_30m"][lo:hi]
            )

        pub_by_zone = np.array(
            [URA_PLANNING_AREAS.get(z, {}).get("pub_monitored", 0) for z in zone_names],
            dtype=np.int64,
        )

        features = pd.DataFrame(
            {
                "ura_planning_area": df["ura_planning_area"],
                "timestamp": df["timestamp"],
                "rain_5m": rain,
                "rain_15m": out["rain_15m"],
                "rain_30m": out["rain_30m"],
                "rain_60m": out["rain_60m"],
                "rain_120m": out["rain_120m"],
                "rain_decay_72h": decay,
                "storm_rarity_score": score,
                "return_period_years": rp,
                "pub_monitored": pub_by_zone[zone_codes],
            }
        )

        if prune_zero_rain:
            # Zero-rain pruning: drop quiet dry periods unless antecedent decay exists.
            quiet = (np.round(out["rain_120m"], 2) == 0.0) & (np.round(decay, 2) < 1.0)
            features = features[~quiet].reset_index(drop=True)

        return features[FEATURE_COLUMNS]
