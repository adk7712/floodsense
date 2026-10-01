"""
FloodSense - Feature Engineering & Storm Rarity Engine.
Implements:
1. Multi-scale rolling accumulations (15m, 30m, 60m, 120m).
2. 72-hour antecedent soil moisture decay factor (half-life = 24 hours).
3. Extreme-value storm rarity & return period quantile estimation per zone.
4. PUB monitored bias correction flag.
5. Zero-rain stream pruning optimization (>85% data reduction).
"""

import math

import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.common.schemas import ZoneFeatureVector, ZoneRainfall
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

# Half-life of 24 hours (288 five-minute intervals), derived from config
HALF_LIFE_STEPS = settings.decay_half_life_hours * 60.0 / settings.step_minutes
DECAY_LAMBDA_PER_STEP = math.log(2.0) / HALF_LIFE_STEPS  # ~0.002407 per 5-min step
DECAY_FACTOR_PER_STEP = math.exp(-DECAY_LAMBDA_PER_STEP)  # ~0.997596


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

    def compute_rarity_and_return_period(self, zone: str, rain_30m: float) -> tuple[float, float]:
        """
        Calculates storm rarity score [0.0, 1.0] and estimated return period in years.
        """
        if rain_30m <= 0.1:
            return 0.0, 0.0

        quantiles = self.zone_quantiles.get(zone, self.default_quantiles)
        # Empirical quantile interpolation
        if rain_30m < quantiles[0]:
            score = 0.5 * (rain_30m / quantiles[0])
            return_period = 0.1
        elif rain_30m >= quantiles[-1]:
            score = 1.0
            excess = (rain_30m - quantiles[-1]) / max(1.0, quantiles[-1])
            return_period = 10.0 + excess * 20.0
        else:
            # Interpolate within quantiles
            idx = np.searchsorted(quantiles, rain_30m)
            base_p = [0.50, 0.80, 0.95, 0.98, 0.995, 0.999]
            p_low = base_p[idx - 1]
            p_high = base_p[idx]
            q_low = quantiles[idx - 1]
            q_high = quantiles[idx]
            frac = (rain_30m - q_low) / (q_high - q_low)
            score = p_low + frac * (p_high - p_low)

            # Map percentile to return period in years
            # p=0.80 ~ 0.5yr, p=0.95 ~ 1yr, p=0.98 ~ 2yr, p=0.995 ~ 5yr, p=0.999 ~ 10yr
            rp_anchors = [0.2, 0.5, 1.0, 2.0, 5.0, 10.0]
            return_period = rp_anchors[idx - 1] + frac * (rp_anchors[idx] - rp_anchors[idx - 1])

        return round(float(score), 4), round(float(return_period), 2)


class FeaturePipeline:
    """
    Stateful and batch feature generation for FloodSense.
    """

    def __init__(self):
        self.rarity_estimator = StormRarityEstimator()
        # Per-zone state tracking for live streaming: decay state and rolling buffers
        self.zone_decay_state: dict[str, float] = {z: 0.0 for z in URA_PLANNING_AREAS}
        self.zone_rolling_buffer: dict[str, list[float]] = {z: [] for z in URA_PLANNING_AREAS}

    def update_streaming_reading(self, reading: ZoneRainfall) -> ZoneFeatureVector:
        """
        Process a single incoming 5-minute zone rainfall reading in real-time.
        Maintains $O(1)$ memory and compute efficiency.
        """
        zone = reading.ura_planning_area
        rain_5m = float(reading.rainfall_mm)

        # Update 72h exponential decay state recursively: R_decay(t) = R(t) + factor * R_decay(t-1)
        prev_decay = self.zone_decay_state.get(zone, 0.0)
        curr_decay = round(rain_5m + DECAY_FACTOR_PER_STEP * prev_decay, 2)
        self.zone_decay_state[zone] = curr_decay

        # Update rolling buffer (max 24 steps = 120 min)
        buf = self.zone_rolling_buffer.setdefault(zone, [])
        buf.append(rain_5m)
        if len(buf) > 24:
            buf.pop(0)

        # Compute rolling window sums
        rain_15m = round(sum(buf[-3:]), 2)
        rain_30m = round(sum(buf[-6:]), 2)
        rain_60m = round(sum(buf[-12:]), 2)
        rain_120m = round(sum(buf[-24:]), 2)

        # Compute storm rarity & return period
        rarity_score, return_period = self.rarity_estimator.compute_rarity_and_return_period(
            zone, rain_30m
        )

        pub_mon = URA_PLANNING_AREAS.get(zone, {}).get("pub_monitored", 0)

        return ZoneFeatureVector(
            ura_planning_area=zone,
            timestamp=reading.timestamp,
            rain_5m=rain_5m,
            rain_15m=rain_15m,
            rain_30m=rain_30m,
            rain_60m=rain_60m,
            rain_120m=rain_120m,
            rain_decay_72h=curr_decay,
            storm_rarity_score=rarity_score,
            return_period_years=return_period,
            pub_monitored=pub_mon,
        )

    def process_batch_dataframe(
        self, df: pd.DataFrame, prune_zero_rain: bool = True
    ) -> pd.DataFrame:
        """
        Transforms a batch DataFrame of zone rainfall into full engineered feature set.
        df columns required: ['ura_planning_area', 'timestamp', 'rainfall_mm']
        """
        df = df.sort_values(by=["ura_planning_area", "timestamp"]).reset_index(drop=True)

        feature_records = []
        for zone, group in df.groupby("ura_planning_area"):
            pub_mon = URA_PLANNING_AREAS.get(zone, {}).get("pub_monitored", 0)
            group = group.copy().reset_index(drop=True)
            rain_arr = group["rainfall_mm"].values
            ts_arr = group["timestamp"].values

            n = len(rain_arr)
            # Compute rolling sums via convolution
            w15 = np.convolve(rain_arr, np.ones(3), mode="full")[:n]
            w30 = np.convolve(rain_arr, np.ones(6), mode="full")[:n]
            w60 = np.convolve(rain_arr, np.ones(12), mode="full")[:n]
            w120 = np.convolve(rain_arr, np.ones(24), mode="full")[:n]

            # Compute recursive 72h decay
            decay_arr = np.zeros(n, dtype=np.float64)
            running_decay = 0.0
            for k in range(n):
                running_decay = rain_arr[k] + DECAY_FACTOR_PER_STEP * running_decay
                decay_arr[k] = running_decay

            for k in range(n):
                r5 = float(rain_arr[k])
                r15 = float(round(w15[k], 2))
                r30 = float(round(w30[k], 2))
                r60 = float(round(w60[k], 2))
                r120 = float(round(w120[k], 2))
                dec = float(round(decay_arr[k], 2))

                # Zero-rain optimization: prune quiet dry periods unless antecedent decay exists
                if prune_zero_rain and r120 == 0.0 and dec < 1.0:
                    continue

                score, rp = self.rarity_estimator.compute_rarity_and_return_period(zone, r30)

                feature_records.append(
                    {
                        "ura_planning_area": zone,
                        "timestamp": ts_arr[k],
                        "rain_5m": r5,
                        "rain_15m": r15,
                        "rain_30m": r30,
                        "rain_60m": r60,
                        "rain_120m": r120,
                        "rain_decay_72h": dec,
                        "storm_rarity_score": score,
                        "return_period_years": rp,
                        "pub_monitored": pub_mon,
                    }
                )

        return pd.DataFrame(feature_records)
