"""
FloodSense - Label policy for "will this zone start flooding within the next H minutes?"

Each flood event is turned into a window:

    start interval [start_lo, start_hi]   when flooding began, widened by the report's precision
    end            end_hi                 when the zone is assumed dry again

For a feature row of zone z at time t (horizon H = ``settings.prediction_lead_time_minutes``):

- t in [start_lo, end_hi]          -> excluded (NaN): the zone may already be flooding, so the row
                                      is not a forecasting situation. This removes the leakage
                                      where rows *during* a flood were labelled as predicting it.
- t < start_lo                     -> label_prob = P(start in (t, t+H]), assuming the true start is
                                      uniform over the start interval:
                                        exact start S:  1 if S - H <= t < S, else 0
                                        interval:       clip((t + H - start_lo) / width, 0, 1)
- otherwise                        -> 0

Imprecise reports ("around 3pm") therefore give partial labels instead of invented exact times.
Training uses ``label_prob`` as a soft target (see ``expand_soft_labels``); row-level metrics use
only rows whose label is certain (0 or 1). Several events in one zone combine as independent:
p = 1 - prod(1 - p_i), and any event's exclusion wins.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.common.timeutil import to_sgt

# Half-width of the start-time uncertainty for each reporting precision.
PRECISION_HALF_WIDTH = {
    "exact": timedelta(0),
    "approx_15min": timedelta(minutes=15),
    "approx_hour": timedelta(minutes=60),
}
# Events without a stated precision (the legacy hand-typed list) are treated as approximate.
DEFAULT_PRECISION = "approx_hour"
# Assumed flood duration when a report gives no end time (PUB: flash floods usually subside
# within 30-60 minutes).
DEFAULT_FLOOD_DURATION = timedelta(minutes=60)


@dataclass(frozen=True)
class EventWindow:
    event_id: str
    zone: str
    start_lo: pd.Timestamp
    start_hi: pd.Timestamp
    end_hi: pd.Timestamp
    precision: str


@dataclass
class LabelReport:
    """What happened to each event while labelling, so nothing is dropped silently."""

    horizon: timedelta
    events_used: list[str] = field(default_factory=list)
    events_without_rows: list[str] = field(default_factory=list)  # zone or period not in features
    counts: dict[str, int] = field(default_factory=dict)  # rows per label_reason


def event_window(event: Any) -> EventWindow:
    """Build the window for one event (a ``FloodEvent`` or anything with the same attributes)."""
    start = pd.Timestamp(to_sgt(event.timestamp_start))
    precision = getattr(event, "time_precision", None) or DEFAULT_PRECISION
    if precision == "day_only":
        day = start.normalize()
        start_lo, start_hi = day, day + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)
    elif precision in PRECISION_HALF_WIDTH:
        half = PRECISION_HALF_WIDTH[precision]
        start_lo, start_hi = start - half, start + half
    else:
        raise ValueError(f"Unknown time_precision {precision!r}")

    end = getattr(event, "timestamp_end", None)
    end_hi = pd.Timestamp(to_sgt(end)) if end is not None else start_hi + DEFAULT_FLOOD_DURATION
    end_hi = max(end_hi, start_hi)
    event_id = getattr(event, "event_id", None) or f"{event.ura_planning_area}@{start.isoformat()}"
    return EventWindow(
        event_id=event_id,
        zone=event.ura_planning_area,
        start_lo=start_lo,
        start_hi=start_hi,
        end_hi=end_hi,
        precision=precision,
    )


def _start_probability(t: np.ndarray, w: EventWindow, horizon_ns: int) -> np.ndarray:
    """P(start in (t, t+H]) for rows with t < start_lo. ``t`` is int64 nanoseconds."""
    lo, hi = w.start_lo.value, w.start_hi.value
    if hi == lo:  # exact start: positive iff S - H <= t < S
        return ((t + horizon_ns) >= lo).astype(np.float64)
    return np.clip((t + horizon_ns - lo) / (hi - lo), 0.0, 1.0)


def label_rows(
    features: pd.DataFrame,
    events: Iterable[Any],
    horizon: timedelta | None = None,
) -> tuple[pd.DataFrame, LabelReport]:
    """
    Label feature rows. ``features`` needs ``ura_planning_area`` and tz-aware ``timestamp``.

    Returns a frame aligned to ``features.index`` with ``label_prob`` (float in [0, 1], NaN when
    excluded) and ``label_reason`` (``neg`` / ``pos`` / ``partial`` / ``in_flood``), plus a report.
    """
    horizon = horizon or timedelta(minutes=settings.prediction_lead_time_minutes)
    horizon_ns = pd.Timedelta(horizon).value
    report = LabelReport(horizon=horizon)

    stamps = pd.to_datetime(features["timestamp"])
    if stamps.dt.tz is None:
        raise ValueError("feature timestamps must be timezone-aware")
    # UTC nanoseconds, comparable with pd.Timestamp.value regardless of the column's unit.
    t_all = pd.DatetimeIndex(stamps).as_unit("ns").asi8
    zones = features["ura_planning_area"].to_numpy()

    prob = np.zeros(len(features))
    excluded = np.zeros(len(features), dtype=bool)

    windows: dict[str, list[EventWindow]] = {}
    for ev in events:
        w = event_window(ev)
        windows.setdefault(w.zone, []).append(w)

    for zone, zone_windows in windows.items():
        rows = np.flatnonzero(zones == zone)
        if rows.size == 0:
            report.events_without_rows += [w.event_id for w in zone_windows]
            continue
        order = rows[np.argsort(t_all[rows], kind="stable")]
        t = t_all[order]
        for w in zone_windows:
            first_pre = np.searchsorted(t, w.start_lo.value - horizon_ns, side="left")
            flood_lo = np.searchsorted(t, w.start_lo.value, side="left")
            flood_hi = np.searchsorted(t, w.end_hi.value, side="right")
            if first_pre == flood_hi:
                report.events_without_rows.append(w.event_id)
                continue
            report.events_used.append(w.event_id)
            pre = order[first_pre:flood_lo]
            p = _start_probability(t[first_pre:flood_lo], w, horizon_ns)
            prob[pre] = 1.0 - (1.0 - prob[pre]) * (1.0 - p)
            excluded[order[flood_lo:flood_hi]] = True

    label_prob = np.where(excluded, np.nan, prob)
    reason = np.select(
        [excluded, prob >= 1.0, prob <= 0.0], ["in_flood", "pos", "neg"], default="partial"
    )
    out = pd.DataFrame({"label_prob": label_prob, "label_reason": reason}, index=features.index)
    report.counts = out["label_reason"].value_counts().to_dict()
    return out, report


def expand_soft_labels(
    X: pd.DataFrame, label_prob: pd.Series, sample_weight: pd.Series | None = None
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """
    Turn soft labels into a weighted binary training set.

    Excluded rows (NaN) are dropped. A row with probability p becomes a positive with weight p and
    a negative with weight 1 - p (rows with p in {0, 1} appear once).
    """
    keep = label_prob.notna().to_numpy()
    X, p = X.loc[keep], label_prob.to_numpy()[keep]
    base = np.ones(len(p)) if sample_weight is None else sample_weight.to_numpy()[keep]
    pos, neg = p > 0, p < 1
    X_out = pd.concat([X.loc[pos], X.loc[neg]], ignore_index=True)
    y = np.concatenate([np.ones(pos.sum()), np.zeros(neg.sum())])
    w = np.concatenate([base[pos] * p[pos], base[neg] * (1 - p[neg])])
    return X_out, y, w


def certain_labels(label_prob: pd.Series) -> pd.Series:
    """Hard 0/1 labels where the label is certain; NaN for excluded or partial rows."""
    return label_prob.where(label_prob.isin([0.0, 1.0]))
