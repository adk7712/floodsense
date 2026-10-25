"""
FloodSense - Was there enough gauge data behind a zone's risk?

Training fills a missing rain reading with 0 (``features/zone_features.py``). That is fine for the
model's labels but wrong for a live alert: an outage looks like a dry hour and the zone shows Low.
Serving therefore flags every zone-step by how many gauges reported:

- ``"none"``: fewer than ``MIN_REPORTING_STATIONS`` gauges reported in this step. The rain is
  unknown, not dry, so ``withhold_tier`` shows "No data" and no probability.
- ``"partial"``: this step is fine, but a step in the last 60 minutes (the ``rain_60m`` window,
  12 five-minute steps) was ``"none"`` or is missing from the frame, so the rolling rain is
  understated. The tier is kept; the app can add a caution.
- ``"ok"``: this step and its whole 60-minute window had enough gauges.

Steps before the frame's first timestamp are not counted as missing: the frame just starts there.

Gold rows carry only the current step's ``reporting_stations``, so on gold only ``"none"`` versus
``"ok"`` can be told; ``"partial"`` needs the step history.
"""

import pandas as pd

from floodsense.common.config import MIN_REPORTING_STATIONS, settings
from floodsense.models.scoring import NO_DATA_TIER

__all__ = ["coverage", "withhold_tier"]

# The rain_60m window: this step plus the 11 before it.
WINDOW_STEPS = 12


def coverage(features: pd.DataFrame) -> pd.Series:
    """
    ``"ok"`` / ``"partial"`` / ``"none"`` for every row of a zone-step feature table.

    Needs ``ura_planning_area``, ``timestamp`` and ``reporting_stations``. The result has the same
    index as ``features``. Raises ``ValueError`` for a timestamp off the 5-minute grid. A NaN
    ``reporting_stations`` counts as no gauges ("none"); a missing column raises ``KeyError``.
    """
    if features.empty:
        return pd.Series([], index=features.index, dtype=object)
    step = f"{int(settings.step_minutes)}min"
    stations = features.pivot_table(
        index="timestamp",
        columns="ura_planning_area",
        values="reporting_stations",
        aggfunc="min",
        dropna=False,
    )
    grid = pd.date_range(stations.index.min(), stations.index.max(), freq=step)
    stations = stations.reindex(grid)
    # A step is bad when it was too thin, or the frame has no row for it at all.
    bad = ~(stations >= MIN_REPORTING_STATIONS)
    earlier_bad = (
        bad.astype(float).shift(1).rolling(WINDOW_STEPS - 1, min_periods=1).max().fillna(0.0) > 0
    )
    wide = pd.DataFrame("ok", index=grid, columns=stations.columns, dtype=object)
    wide = wide.mask(earlier_bad, "partial").mask(bad, "none")
    pos_t = grid.get_indexer(pd.DatetimeIndex(features["timestamp"]))
    pos_z = wide.columns.get_indexer(features["ura_planning_area"])
    if (pos_t < 0).any() or (pos_z < 0).any():
        raise ValueError(
            f"timestamps must sit on the {step} grid; found an off-grid timestamp or unknown zone"
        )
    return pd.Series(wide.to_numpy()[pos_t, pos_z], index=features.index, dtype=object)


def withhold_tier(df: pd.DataFrame) -> pd.DataFrame:
    """
    A copy of a scored table with a ``coverage`` column and no tier where there is no data.

    ``"none"`` rows get ``risk_tier`` "No data" and a NaN ``flood_probability``. ``"ok"`` and
    ``"partial"`` rows keep theirs. Needs the columns ``coverage`` needs, plus the scored ones.
    """
    out = df.copy()
    out["coverage"] = coverage(out)
    none = out["coverage"] == "none"
    out.loc[none, "risk_tier"] = NO_DATA_TIER
    out.loc[none, "flood_probability"] = float("nan")
    return out
