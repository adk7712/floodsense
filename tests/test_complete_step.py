"""Live risk uses the newest step NEA has filled in, not one with only a few gauges so far.

NEA publishes each 5-minute step with a handful of gauges and adds the rest a few minutes later
(4 of 90, then 90 of 90, on 16 Oct 2026). Both serving paths share ``newest_complete_step``.
"""

import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.data.replay import load_replay
from floodsense.features.zone_features import newest_complete_step
from floodsense.serving import pipeline_core as core

SGT = "Asia/Singapore"


def _counts(*tail: int, usual: int = 90, steps: int = 30) -> pd.Series:
    """``steps`` steps of ``usual`` gauges, then ``tail``. Shuffled: order must not matter."""
    values = [usual] * steps + list(tail)
    index = pd.date_range("2026-10-16 00:00", periods=len(values), freq="5min", tz=SGT)
    return pd.Series(values, index=index).sample(frac=1.0, random_state=0)


def _newest(series: pd.Series, back: int = 0) -> pd.Timestamp:
    return series.sort_index().index[-1 - back]


def test_a_complete_newest_step_is_used():
    s = _counts(88)
    assert newest_complete_step(s) == _newest(s)


@pytest.mark.parametrize("short_steps", [1, 2])
def test_short_newest_steps_are_skipped(short_steps):
    s = _counts(*[4] * short_steps)
    assert newest_complete_step(s) == _newest(s, back=short_steps)


def test_the_share_is_of_the_usual_count():
    """45 of 90 is exactly half: used. 44 is not."""
    assert newest_complete_step(s := _counts(45)) == _newest(s)
    assert newest_complete_step(s := _counts(44)) == _newest(s, back=1)


def test_an_outage_is_scored_as_it_is_not_hidden():
    """More short steps than ``max_incomplete_steps``: gauges are down, not late."""
    s = _counts(*[4] * (settings.max_incomplete_steps + 1))
    assert newest_complete_step(s) == _newest(s)


def test_a_fleet_that_usually_reports_less_is_judged_against_itself():
    s = _counts(50, usual=55)
    assert newest_complete_step(s) == _newest(s)


def test_too_little_history_uses_the_newest_step():
    s = _counts(4, steps=settings.max_incomplete_steps - 1)
    assert newest_complete_step(s) == _newest(s)


def test_turned_off_uses_the_newest_step(monkeypatch):
    monkeypatch.setattr(settings, "max_incomplete_steps", 0)
    s = _counts(4)
    assert newest_complete_step(s) == _newest(s)


def test_no_steps_is_an_error():
    with pytest.raises(ValueError):
        newest_complete_step(pd.Series([], dtype=int))


def test_reporting_counts_count_empty_steps_as_zero():
    t = pd.Timestamp("2026-10-16 01:00", tz=SGT)
    readings = pd.DataFrame(
        {
            "station_id": ["S1", "S2", "S1"],
            "timestamp": [t, t, t + pd.Timedelta(minutes=10)],
            "rainfall_mm": [0.0, 0.2, 0.4],
        }
    )
    assert core.reporting_counts(readings).tolist() == [2, 0, 1]


@pytest.fixture(scope="module")
def silver():
    replay = load_replay(settings.replay_file)
    return core.payloads_to_readings(core.snapshot_to_payload(s) for s in replay.snapshots)


def test_gold_leaves_a_still_filling_step_for_the_next_run(silver):
    """A newest step from 4 gauges (raining hard) is not emitted; gold ends at the step before,
    with the same rows it has without the short step."""
    readings, stations = silver
    newest = readings["timestamp"].max()
    stub = pd.DataFrame(
        {
            "station_id": stations["station_id"].iloc[:4].tolist(),
            "timestamp": newest + pd.Timedelta(minutes=5),
            "rainfall_mm": 20.0,
        }
    )
    with_stub = pd.concat([readings, stub], ignore_index=True)
    got = core.gold_from_silver(with_stub, stations, SGT, emit_hours=2)
    want = core.gold_from_silver(readings, stations, SGT, emit_hours=2)
    assert got["timestamp"].max() == newest
    pd.testing.assert_frame_equal(got, want)
