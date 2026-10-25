"""Gauge coverage for serving (floodsense.serving.coverage): outages are "No data", not dry."""

import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import MIN_REPORTING_STATIONS
from floodsense.models.scoring import NO_DATA_TIER
from floodsense.serving.coverage import coverage, withhold_tier

T0 = pd.Timestamp("2026-10-20 12:00", tz="Asia/Singapore")


def table(counts, zones=("A", "B")):
    """One row per zone and 5-minute step; ``counts[i]`` gauges reported at step i."""
    rows = [
        {
            "ura_planning_area": z,
            "timestamp": T0 + pd.Timedelta(minutes=5 * i),
            "reporting_stations": n,
            "flood_probability": 0.02,
            "risk_tier": "High",
        }
        for z in zones
        for i, n in enumerate(counts)
        if n is not None  # None leaves the step out of the frame
    ]
    return pd.DataFrame(rows)


def cov(counts, zone="A"):
    f = table(counts)
    c = coverage(f)
    return list(c[f["ura_planning_area"] == zone])


def test_complete_frame_is_ok():
    assert set(cov([60] * 20)) == {"ok"}


def test_outage_step_is_none_and_next_hour_is_partial():
    out = cov([60] * 3 + [0] + [60] * 14)
    assert out[3] == "none"
    assert out[:3] == ["ok"] * 3  # steps before the gap are untouched
    assert out[4:15] == ["partial"] * 11  # the next 11 steps still have the gap in their hour
    assert out[15:] == ["ok"] * 3


def test_single_gauge_step_is_below_the_floor():
    assert cov([60, 1, 60])[1] == "none"
    assert cov([60, MIN_REPORTING_STATIONS - 1, 60])[1] == "none"
    assert cov([60, MIN_REPORTING_STATIONS, 60])[1] == "ok"


def test_step_missing_from_the_frame_makes_later_steps_partial():
    out = cov([60, 60, None, 60, 60])  # rows exist for steps 0, 1, 3 and 4
    assert out == ["ok", "ok", "partial", "partial"]


def test_steps_before_the_frame_starts_are_not_missing():
    assert cov([60]) == ["ok"]
    assert set(cov([60] * 4)) == {"ok"}


def test_single_step_frame_like_gold():
    f = table([5, 60], zones=("A",)).iloc[[0]]
    assert list(coverage(f)) == ["none"]


def test_missing_reporting_stations_counts_as_none():
    f = table([60, np.nan, 60])
    assert list(coverage(f)[f["ura_planning_area"] == "A"])[1] == "none"


def test_withhold_tier_blanks_only_none_rows():
    f = table([60, 0, 60, 60])
    out = withhold_tier(f)
    a = out[out["ura_planning_area"] == "A"].reset_index(drop=True)
    assert a.loc[1, "risk_tier"] == NO_DATA_TIER and np.isnan(a.loc[1, "flood_probability"])
    assert a.loc[2, "coverage"] == "partial" and a.loc[2, "risk_tier"] == "High"
    assert a.loc[0, "risk_tier"] == "High" and a.loc[0, "flood_probability"] == 0.02
    assert f["risk_tier"].eq("High").all()  # the input is not changed


def test_empty_frame():
    f = table([60]).iloc[0:0]
    assert len(coverage(f)) == 0


def test_off_grid_timestamp_is_refused():
    f = table([60, 60, 60])
    f.loc[f.index[1], "timestamp"] += pd.Timedelta(minutes=2)
    with pytest.raises(ValueError, match="grid"):
        coverage(f)
