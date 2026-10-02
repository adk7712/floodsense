"""
FloodSense - Spatial Rainfall Engine & Dynamic IDW Matrix.
Pre-computes Inverse Distance Weighting (IDW, p=2) matrix from NEA weather stations
to URA Planning Area centroids and handles real-time dynamic rebalancing when stations go offline.
"""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from floodsense.common.schemas import ZoneRainfall
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS, load_station_snapshot


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the Great Circle distance between two points on earth in kilometers."""
    R = 6371.0  # Earth's mean radius in km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


class IDWMatrixEngine:
    """
    Inverse Distance Weighting (IDW) Engine with dynamic rebalancing for missing stations.
    """

    def __init__(
        self,
        zones: dict[str, dict] | None = None,
        stations: dict[str, dict] | None = None,
        power: float = 2.0,
        eps: float = 1e-4,
    ):
        self.zones = zones or URA_PLANNING_AREAS
        self.stations = stations or load_station_snapshot()
        self.power = power
        self.eps = eps

        self.zone_names = sorted(list(self.zones.keys()))
        self.station_ids = sorted(list(self.stations.keys()))

        # Pre-compute distance and base weight matrices
        self.dist_matrix = self._compute_distance_matrix()
        self.base_weights = self._compute_base_idw_weights()

    def _compute_distance_matrix(self) -> np.ndarray:
        """Matrix of shape (num_stations, num_zones) storing distances in km."""
        num_stations = len(self.station_ids)
        num_zones = len(self.zone_names)
        dist_mat = np.zeros((num_stations, num_zones), dtype=np.float64)

        for i, s_id in enumerate(self.station_ids):
            s_lat = self.stations[s_id]["lat"]
            s_lon = self.stations[s_id]["lon"]
            for j, z_name in enumerate(self.zone_names):
                z_lat = self.zones[z_name]["lat"]
                z_lon = self.zones[z_name]["lon"]
                dist = haversine_distance_km(s_lat, s_lon, z_lat, z_lon)
                dist_mat[i, j] = max(dist, self.eps)

        return dist_mat

    def _compute_base_idw_weights(self) -> np.ndarray:
        """
        Base IDW weights matrix w_ij = (1 / d_ij^p) / sum_k(1 / d_kj^p).
        Shape: (num_stations, num_zones). Each column j sums to 1.0.
        """
        inv_dist_p = 1.0 / (self.dist_matrix**self.power)
        # Sum over stations (axis 0)
        col_sums = np.sum(inv_dist_p, axis=0, keepdims=True)
        weights = inv_dist_p / col_sums
        return weights

    def get_rebalanced_weights(self, active_station_ids: set[str]) -> np.ndarray:
        """
        Dynamically rebalances IDW weights when one or more stations are offline:
        w_tilde_ij = (w_ij * 1_{station i reporting}) / sum_k(w_kj * 1_{station k reporting})
        """
        active_mask = np.array(
            [1.0 if s_id in active_station_ids else 0.0 for s_id in self.station_ids],
            dtype=np.float64,
        ).reshape(-1, 1)

        rebalanced = self.base_weights * active_mask
        col_sums = np.sum(rebalanced, axis=0, keepdims=True)

        # Handle extreme edge case where no stations are reporting
        zero_mask = col_sums == 0.0
        col_sums[zero_mask] = 1.0
        rebalanced = rebalanced / col_sums
        return rebalanced

    def interpolate_matrix(self, station_values: np.ndarray) -> np.ndarray:
        """
        Interpolate many timesteps at once, rebalancing weights per timestep.

        ``station_values`` has shape ``(num_timesteps, num_stations)`` in ``self.station_ids``
        order, with NaN for stations that did not report. Returns ``(num_timesteps, num_zones)``.
        This is the same rebalanced IDW as ``interpolate_rainfall``:
        ``sum_i(r_i * w_ij * m_i) / sum_i(w_ij * m_i)``. Timesteps with no reporting station give 0.
        """
        reporting = ~np.isnan(station_values)
        numerator = np.where(reporting, station_values, 0.0) @ self.base_weights
        denominator = reporting.astype(np.float64) @ self.base_weights
        return np.divide(
            numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0
        )

    def interpolate_rainfall(
        self, station_readings: dict[str, float], timestamp=None
    ) -> list[ZoneRainfall]:
        """
        Interpolate 5-min station rainfall onto all 55 URA Planning Areas.
        """
        active_stations = {
            s for s, val in station_readings.items() if s in self.stations and val is not None
        }
        weights = self.get_rebalanced_weights(active_stations)

        # Vector of station rainfall values
        r_vec = np.array(
            [station_readings.get(s_id, 0.0) for s_id in self.station_ids], dtype=np.float64
        )

        # Matrix multiply: (num_stations,) @ (num_stations, num_zones) -> (num_zones,)
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            zone_rainfall_values = r_vec @ weights

        results = []
        for j, z_name in enumerate(self.zone_names):
            results.append(
                ZoneRainfall(
                    ura_planning_area=z_name,
                    timestamp=timestamp or pd.Timestamp.now(),
                    rainfall_mm=float(np.round(zone_rainfall_values[j], 2)),
                    reporting_stations_count=len(active_stations),
                    total_stations_count=len(self.station_ids),
                )
            )

        return results

    def to_dataframe(self) -> pd.DataFrame:
        """Export IDW base weights as a tidy DataFrame."""
        records = []
        for i, s_id in enumerate(self.station_ids):
            for j, z_name in enumerate(self.zone_names):
                records.append(
                    {
                        "station_id": s_id,
                        "station_name": self.stations[s_id]["name"],
                        "ura_planning_area": z_name,
                        "distance_km": round(float(self.dist_matrix[i, j]), 3),
                        "base_weight": round(float(self.base_weights[i, j]), 6),
                    }
                )
        return pd.DataFrame(records)

    def save_static_weights(
        self, output_parquet: str = "data/processed/station_zone_weights.parquet"
    ) -> None:
        """Save base weight matrix to Parquet file."""
        df = self.to_dataframe()
        out_path = Path(output_parquet)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_path, index=False)


if __name__ == "__main__":
    engine = IDWMatrixEngine()
    engine.save_static_weights()
    print(
        f"Generated static weights: {len(engine.station_ids)} stations -> {len(engine.zone_names)} zones"
    )
