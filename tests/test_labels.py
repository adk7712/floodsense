"""Tests for the label policy (Phase 4, workstream A)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from floodsense.labels.policy import (
    certain_labels,
    event_window,
    expand_soft_labels,
    label_rows,
)

SGT = "Asia/Singapore"
H = timedelta(minutes=60)


def _event(zone="BUKIT TIMAH", start="2021-04-17 15:00", precision="exact", end=None, eid=None):
    return SimpleNamespace(
        ura_planning_area=zone,
        timestamp_start=pd.Timestamp(start, tz=SGT).to_pydatetime(),
        timestamp_end=pd.Timestamp(end, tz=SGT).to_pydatetime() if end else None,
        time_precision=precision,
        event_id=eid,
    )


def _grid(start="2021-04-17 12:00", end="2021-04-17 17:30", zones=("BUKIT TIMAH", "BEDOK")):
    stamps = pd.date_range(start, end, freq="5min", tz=SGT)
    return pd.DataFrame(
        [(z, t) for z in zones for t in stamps], columns=["ura_planning_area", "timestamp"]
    )


def _at(df, labels, hhmm, zone="BUKIT TIMAH"):
    row = df[(df["ura_planning_area"] == zone) & (df["timestamp"].dt.strftime("%H:%M") == hhmm)]
    return labels.loc[row.index[0]]


def test_exact_start_labels():
    df = _grid()
    labels, report = label_rows(df, [_event()], horizon=H)
    expect = {
        "13:55": ("neg", 0.0),  # flood starts 65 min later: beyond the horizon
        "14:00": ("pos", 1.0),  # exactly H before the start: (t, t+H] includes S
        "14:30": ("pos", 1.0),
        "14:55": ("pos", 1.0),
        "15:00": ("in_flood", None),  # flooding already: not a forecasting row
        "16:00": ("in_flood", None),  # default duration 60 min, end inclusive
        "16:05": ("neg", 0.0),
    }
    for hhmm, (reason, prob) in expect.items():
        row = _at(df, labels, hhmm)
        assert row["label_reason"] == reason, hhmm
        if prob is None:
            assert np.isnan(row["label_prob"]), hhmm
        else:
            assert row["label_prob"] == prob, hhmm
    assert report.events_used == ["BUKIT TIMAH@2021-04-17T15:00:00+08:00"]


def test_other_zones_are_untouched():
    df = _grid()
    labels, _ = label_rows(df, [_event()], horizon=H)
    bedok = labels[df["ura_planning_area"] == "BEDOK"]
    assert (bedok["label_reason"] == "neg").all()


def test_reported_end_time_extends_the_exclusion():
    df = _grid()
    labels, _ = label_rows(df, [_event(end="2021-04-17 16:30")], horizon=H)
    assert _at(df, labels, "16:30")["label_reason"] == "in_flood"
    assert _at(df, labels, "16:35")["label_reason"] == "neg"


def test_approx_15min_gives_partial_labels():
    # start in [14:45, 15:15], width 30 min
    df = _grid()
    labels, _ = label_rows(df, [_event(precision="approx_15min")], horizon=H)
    assert _at(df, labels, "13:45")["label_prob"] == 0.0  # t+H == start_lo
    assert _at(df, labels, "14:00")["label_prob"] == pytest.approx(0.5)  # (15:00-14:45)/30
    assert _at(df, labels, "14:15")["label_prob"] == pytest.approx(1.0)  # whole interval inside
    assert _at(df, labels, "14:15")["label_reason"] == "pos"
    assert _at(df, labels, "14:45")["label_reason"] == "in_flood"


def test_approx_hour_never_claims_certainty():
    # start known only to +/-60 min: no row can be sure the start falls in its next 60 min
    df = _grid()
    labels, _ = label_rows(df, [_event(precision="approx_hour")], horizon=H)
    assert not (labels["label_reason"] == "pos").any()
    assert _at(df, labels, "13:55")["label_prob"] == pytest.approx(55 / 120)
    assert _at(df, labels, "14:00")["label_reason"] == "in_flood"


def test_day_only_excludes_the_day():
    df = _grid(start="2021-04-16 22:00", end="2021-04-18 02:00", zones=("BUKIT TIMAH",))
    labels, _ = label_rows(df, [_event(precision="day_only")], horizon=H)
    day = df["timestamp"].dt.date == pd.Timestamp("2021-04-17").date()
    assert (labels.loc[day, "label_reason"] == "in_flood").all()
    before = labels[df["timestamp"] < pd.Timestamp("2021-04-16 23:00", tz=SGT)]
    assert (before["label_prob"] == 0.0).all()
    near_midnight = _at(df, labels, "23:55", zone="BUKIT TIMAH")
    assert 0 < near_midnight["label_prob"] < 0.05  # 55 min of a 24 h window


def test_overlapping_events_combine_as_independent():
    # Two approx_15min events whose partial windows overlap at 14:00 (0.5 each) -> 0.75
    df = _grid()
    events = [_event(precision="approx_15min", eid="a"), _event(precision="approx_15min", eid="b")]
    labels, _ = label_rows(df, events, horizon=H)
    assert _at(df, labels, "14:00")["label_prob"] == pytest.approx(0.75)


def test_events_without_rows_are_reported_not_dropped():
    df = _grid()
    events = [_event(zone="TUAS", eid="tuas"), _event(start="2024-01-01 12:00", eid="later")]
    _, report = label_rows(df, events, horizon=H)
    assert sorted(report.events_without_rows) == ["later", "tuas"]
    assert report.events_used == []


def test_naive_timestamps_are_rejected():
    df = _grid()
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        label_rows(df, [_event()], horizon=H)


def _brute_force(df, events, horizon):
    """Literal per-row reading of the policy, for comparison."""
    windows = [event_window(e) for e in events]
    probs, excl = [], []
    for zone, t in zip(df["ura_planning_area"], df["timestamp"], strict=True):
        p, x = 0.0, False
        for w in windows:
            if w.zone != zone:
                continue
            if w.start_lo <= t <= w.end_hi:
                x = True
            elif t < w.start_lo:
                if w.start_hi == w.start_lo:
                    pi = 1.0 if t + horizon >= w.start_lo else 0.0
                else:
                    pi = min(max((t + horizon - w.start_lo) / (w.start_hi - w.start_lo), 0.0), 1.0)
                p = 1 - (1 - p) * (1 - pi)
        probs.append(np.nan if x else p)
        excl.append(x)
    return np.array(probs)


def test_matches_brute_force_on_random_events():
    rng = np.random.default_rng(0)
    df = _grid(start="2021-04-17 00:00", end="2021-04-18 23:55", zones=("BUKIT TIMAH", "BEDOK"))
    df = df.sample(frac=1.0, random_state=1)  # order must not matter
    precisions = ["exact", "approx_15min", "approx_hour", "day_only"]
    events = []
    for i in range(12):
        start = pd.Timestamp("2021-04-17 02:00", tz=SGT) + pd.Timedelta(
            minutes=int(rng.integers(0, 40 * 60))
        )
        end = (
            start + pd.Timedelta(minutes=int(rng.integers(10, 180))) if rng.random() < 0.5 else None
        )
        events.append(
            _event(
                zone=["BUKIT TIMAH", "BEDOK"][i % 2],
                start=str(start.tz_localize(None)),
                end=str(end.tz_localize(None)) if end is not None else None,
                precision=precisions[i % 4],
                eid=f"e{i}",
            )
        )
    labels, _ = label_rows(df, events, horizon=H)
    expected = _brute_force(df, events, pd.Timedelta(H))
    np.testing.assert_allclose(labels["label_prob"].to_numpy(), expected, atol=1e-12)


def test_expand_soft_labels_preserves_weight():
    X = pd.DataFrame({"f": [1.0, 2.0, 3.0, 4.0]})
    p = pd.Series([0.0, 1.0, 0.25, np.nan])
    X2, y, w = expand_soft_labels(X, p)
    assert len(X2) == 4  # 0 -> 1 row, 1 -> 1 row, 0.25 -> 2 rows, NaN dropped
    assert w.sum() == pytest.approx(3.0)  # one unit of weight per kept row
    assert w[y == 1].sum() == pytest.approx(1.25)
    assert sorted(X2.loc[y == 1, "f"]) == [2.0, 3.0]


def test_certain_labels_drop_partial_rows():
    p = pd.Series([0.0, 1.0, 0.4, np.nan])
    out = certain_labels(p)
    assert out.tolist()[:2] == [0.0, 1.0]
    assert out.iloc[2:].isna().all()


def test_event_window_uses_naive_times_as_sgt():
    ev = SimpleNamespace(
        ura_planning_area="BISHAN",
        timestamp_start=datetime(2024, 5, 14, 16, 0),
        timestamp_end=None,
    )
    w = event_window(ev)
    assert w.start_lo.utcoffset() == timedelta(hours=8)
    assert w.precision == "approx_hour"  # no stated precision -> treated as approximate
