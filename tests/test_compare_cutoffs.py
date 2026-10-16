"""Offline tests for the Moderate cut-off comparison: threshold sweep on synthetic data."""

import pandas as pd
import pytest

from floodsense.labels.policy import EventWindow
from floodsense.models import compare_cutoffs as cc

SGT = "Asia/Singapore"


def ts(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"2021-04-17 {hhmm}", tz=SGT)


def timeline(zone: str, start: str, probs: list[float]) -> pd.DataFrame:
    stamps = pd.date_range(ts(start), periods=len(probs), freq="5min")
    return pd.DataFrame({"ura_planning_area": zone, "timestamp": stamps, "prob": probs})


def event(eid: str, zone: str) -> EventWindow:
    return EventWindow(eid, zone, ts("12:00"), ts("12:00"), ts("13:00"), "exact")


@pytest.fixture
def scene() -> tuple[pd.DataFrame, list[EventWindow]]:
    # Zone A: 0.004 from 11:30, 0.008 from 11:50 (11:00..12:00 = 13 steps).
    a = [0.0] * 6 + [0.004] * 4 + [0.008] * 3
    # Zone B: 0.004 only from 11:30. Zone C: a false episode at 0.004 far from any flood.
    b = [0.0] * 6 + [0.004] * 7
    c = [0.0] * 3 + [0.004] * 2 + [0.0] * 8
    scored = pd.concat(
        [timeline("A", "11:00", a), timeline("B", "11:00", b), timeline("C", "08:00", c)]
    )
    return scored, [event("ev_a", "A"), event("ev_b", "B")]


def test_cutoff_row_counts_hits_and_false_episodes(scene) -> None:
    scored, windows = scene
    row, _ = cc.evaluate_cutoff(scored, windows, 0.003, zone_years=2.0)
    assert (row["floods_caught"], row["floods"]) == (2, 2)
    assert row["false_episodes"] == 1  # the zone-C episode
    assert row["false_episodes_per_zone_year"] == pytest.approx(0.5)
    lo, hi = row["hit_rate_ci90"]
    assert 0.0 <= lo <= hi <= 1.0
    row_hi, _ = cc.evaluate_cutoff(scored, windows, 0.006, zone_years=2.0)
    assert (row_hi["floods_caught"], row_hi["false_episodes"]) == (1, 0)


def test_caught_here_but_not_at_high(scene) -> None:
    scored, windows = scene
    _, high = cc.evaluate_cutoff(scored, windows, 0.007, zone_years=2.0)
    high_hits = {i: o.hit for i, o in high.items()}
    assert high_hits == {"ev_a": True, "ev_b": False}
    row, _ = cc.evaluate_cutoff(scored, windows, 0.003, zone_years=2.0, high_hits=high_hits)
    assert row["caught_here_not_at_high"] == ["ev_b"]
    assert row["n_caught_here_not_at_high"] == 1


def test_differing_floods_later_and_missed(scene) -> None:
    scored, windows = scene
    _, low = cc.evaluate_cutoff(scored, windows, 0.003, zone_years=2.0)
    _, mid = cc.evaluate_cutoff(scored, windows, 0.006, zone_years=2.0)
    diffs = {d["event_id"]: d for d in cc.differing_floods(low, mid)}
    assert diffs["ev_a"]["change"] == "earlier at a"  # 11:30 vs 11:50
    assert diffs["ev_b"]["change"] == "caught only at a"
    assert cc.differing_floods(low, low) == []


def test_monotone_in_cutoff(scene) -> None:
    scored, windows = scene
    hits = [
        cc.evaluate_cutoff(scored, windows, c, zone_years=2.0)[0]["floods_caught"]
        for c in (0.001, 0.003, 0.006, 0.01)
    ]
    assert hits == sorted(hits, reverse=True)


def test_mm_equivalents_match_refit_points() -> None:
    assert cc._mm(cc.REFIT_MODERATE) == pytest.approx(37.65)
    assert cc._mm(cc.HIGH) == pytest.approx(43.15)


def test_parser_defaults() -> None:
    args = cc.build_parser().parse_args(["--date", "2026-10-17"])
    assert args.date == "2026-10-17"
    assert 0.003 in args.cutoffs and 0.00531 in args.cutoffs and 0.00706 in args.cutoffs
