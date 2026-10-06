"""
FloodSense - Evaluation metrics at three levels.

Row level      certain-label rows only: PR-AUC, Brier, precision/recall, the false-positive rate
               FP/(FP+TN) and the false-alarm ratio FP/(FP+TP). (The original code reported the
               first under the name of the second: 1.3% instead of 93%.)

Event level    did a High alert fire in time for each flood, and how early?
               An event is hit when an alert occurs at some t in [start_lo - H, start_hi): the flood
               could start within that alert's horizon. Lead time is measured from the start of
               the alert episode containing the first such alert to the reported start (the
               midpoint of the start interval, i.e. the start itself for exact reports).

Alert episodes contiguous runs of alert steps in one zone - what people actually experience.
               An episode is a false alarm unless a flood's span [start_lo - H, end_hi] overlaps it.
               False alarms are also normalised per zone-year so a budget can be set on them.

Rows absent from the scored timeline (e.g. pruned dry periods) count as "no alert".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss

from floodsense.common.config import settings
from floodsense.labels.policy import EventWindow

# --------------------------------------------------------------------------------------------
# Row level
# --------------------------------------------------------------------------------------------


def row_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    threshold: float,
    sample_weight: np.ndarray | None = None,
) -> dict[str, float]:
    """Metrics on rows with certain labels (0/1). Pass ``certain_labels(...)``-filtered data."""
    y_true = np.asarray(y_true, dtype=float)
    y_prob = np.asarray(y_prob, dtype=float)
    if np.isnan(y_true).any():
        raise ValueError("row_metrics needs certain labels only; filter NaN / partial rows first")
    w = np.ones_like(y_true) if sample_weight is None else np.asarray(sample_weight, dtype=float)

    pred = y_prob >= threshold
    pos = y_true == 1
    tp = float(w[pred & pos].sum())
    fp = float(w[pred & ~pos].sum())
    fn = float(w[~pred & pos].sum())
    tn = float(w[~pred & ~pos].sum())

    def ratio(a: float, b: float) -> float:
        return a / b if b > 0 else float("nan")

    has_both = pos.any() and (~pos).any()
    return {
        "pr_auc": float(average_precision_score(y_true, y_prob, sample_weight=w))
        if has_both
        else float("nan"),
        "brier": float(brier_score_loss(y_true, y_prob, sample_weight=w)),
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "false_positive_rate": ratio(fp, fp + tn),
        "false_alarm_ratio": ratio(fp, fp + tp),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "positives": float(w[pos].sum()),
        "rows": float(w.sum()),
    }


# --------------------------------------------------------------------------------------------
# Alert episodes and events
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EventOutcome:
    event_id: str
    zone: str
    hit: bool
    first_alert: pd.Timestamp | None
    # reported start - alert episode start; None when missed, or when the report gives only a
    # date (the flood may have started any time that day, so a warning time would be invented)
    lead_minutes: float | None


def alert_episodes(
    scored: pd.DataFrame, threshold: float, step: timedelta | None = None
) -> pd.DataFrame:
    """
    Contiguous runs of ``prob >= threshold`` per zone.

    ``scored`` needs ``ura_planning_area``, tz-aware ``timestamp`` and ``prob``. Returns one row per
    episode: ``ura_planning_area``, ``start``, ``end``, ``steps``.
    """
    step_td = pd.Timedelta(step or timedelta(minutes=settings.step_minutes))
    alerts = scored.loc[scored["prob"] >= threshold, ["ura_planning_area", "timestamp"]]
    if alerts.empty:
        return pd.DataFrame(columns=["ura_planning_area", "start", "end", "steps"])
    alerts = alerts.sort_values(["ura_planning_area", "timestamp"])
    zone = alerts["ura_planning_area"].to_numpy()
    ts = alerts["timestamp"]
    new = np.ones(len(alerts), dtype=bool)
    new[1:] = (zone[1:] != zone[:-1]) | (ts.diff().to_numpy()[1:] > step_td.to_timedelta64())
    episode_id = np.cumsum(new)
    grouped = alerts.assign(_ep=episode_id).groupby("_ep")
    return pd.DataFrame(
        {
            "ura_planning_area": grouped["ura_planning_area"].first(),
            "start": grouped["timestamp"].min(),
            "end": grouped["timestamp"].max(),
            "steps": grouped.size(),
        }
    ).reset_index(drop=True)


def evaluate_alerts(
    scored: pd.DataFrame,
    windows: Sequence[EventWindow],
    threshold: float,
    zone_years: float,
    horizon: timedelta | None = None,
) -> tuple[dict[str, float], list[EventOutcome], pd.DataFrame]:
    """
    Event- and episode-level evaluation of one threshold.

    ``zone_years`` = number of zones x years covered by ``scored`` (the denominator for the
    false-alarm budget). Returns (summary, per-event outcomes, episodes with an ``is_false`` flag).
    """
    horizon_td = pd.Timedelta(horizon or timedelta(minutes=settings.prediction_lead_time_minutes))
    episodes = alert_episodes(scored, threshold)
    episodes["is_false"] = True

    by_zone = {z: g for z, g in episodes.groupby("ura_planning_area")} if len(episodes) else {}
    outcomes: list[EventOutcome] = []
    for w in windows:
        eps = by_zone.get(w.zone)
        if eps is None or eps.empty:
            outcomes.append(EventOutcome(w.event_id, w.zone, False, None, None))
            continue
        # Episodes overlapping the event's span are true alarms (including late ones).
        overlaps = (eps["start"] <= w.end_hi) & (eps["end"] >= w.start_lo - horizon_td)
        episodes.loc[eps.index[overlaps.to_numpy()], "is_false"] = False
        # Hit: an alert step inside [start_lo - H, start_hi). Episodes are runs of steps, so the
        # earliest qualifying step is max(episode start, start_lo - H) for an episode reaching it.
        window_lo, window_hi = w.start_lo - horizon_td, w.start_hi
        timely = eps[(eps["end"] >= window_lo) & (eps["start"] < window_hi)]
        if timely.empty:
            outcomes.append(EventOutcome(w.event_id, w.zone, False, None, None))
            continue
        first = timely.sort_values("start").iloc[0]
        first_alert = max(first["start"], window_lo)
        if w.precision == "day_only":
            outcomes.append(EventOutcome(w.event_id, w.zone, True, first_alert, None))
            continue
        reported_start = w.start_lo + (w.start_hi - w.start_lo) / 2
        lead = (reported_start - first["start"]) / pd.Timedelta(minutes=1)
        outcomes.append(EventOutcome(w.event_id, w.zone, True, first_alert, float(lead)))

    n_events = len(outcomes)
    hits = sum(o.hit for o in outcomes)
    leads = [o.lead_minutes for o in outcomes if o.lead_minutes is not None]
    n_eps = len(episodes)
    n_false = int(episodes["is_false"].sum()) if n_eps else 0
    false_steps = int(episodes.loc[episodes["is_false"], "steps"].sum()) if n_eps else 0
    summary = {
        "threshold": float(threshold),
        "events": float(n_events),
        "hits": float(hits),
        "hit_rate": hits / n_events if n_events else float("nan"),
        "median_lead_minutes": float(np.median(leads)) if leads else float("nan"),
        "episodes": float(n_eps),
        "false_episodes": float(n_false),
        "episode_false_alarm_ratio": n_false / n_eps if n_eps else float("nan"),
        "false_episodes_per_zone_year": n_false / zone_years if zone_years > 0 else float("nan"),
        "false_alert_hours": false_steps * settings.step_minutes / 60.0,
    }
    return summary, outcomes, episodes


def ranking_check(
    scored: pd.DataFrame,
    windows: Sequence[EventWindow],
    n_zones: int,
    top_fraction: float = 0.1,
    lookback: timedelta = timedelta(hours=3),
) -> dict[str, float]:
    """
    Did the flooded zone rank among the riskiest zones during its storm?

    For each event, every zone's peak probability over ``[start_lo - lookback, start_hi]`` is
    compared (zones with no rows in that window score 0). The event counts as ranked in the top
    when its zone is in the top ``top_fraction`` of ``n_zones`` with a peak above 0; ties count
    in its favour only for zones strictly above it. Threshold-free, so it complements hit rate.
    """
    cutoff = max(1, int(np.floor(top_fraction * n_zones)))
    ranks = []
    for w in windows:
        in_window = scored[
            (scored["timestamp"] >= w.start_lo - lookback) & (scored["timestamp"] <= w.start_hi)
        ]
        peaks = in_window.groupby("ura_planning_area")["prob"].max()
        own = float(peaks.get(w.zone, 0.0))
        rank = int((peaks > own).sum()) + 1 if own > 0 else n_zones
        ranks.append(rank)
    arr = np.asarray(ranks)
    return {
        "events": float(len(arr)),
        "top_fraction": top_fraction,
        "top_n_zones": float(cutoff),
        "in_top": float((arr <= cutoff).sum()),
        "share_in_top": float((arr <= cutoff).mean()) if len(arr) else float("nan"),
        "median_rank": float(np.median(arr)) if len(arr) else float("nan"),
    }


def hit_rate_ci(
    hits: Sequence[bool], n_boot: int = 2000, alpha: float = 0.1, seed: int = 0
) -> tuple[float, float]:
    """Bootstrap (1 - alpha) interval for the event hit rate. Wide by design: events are few."""
    h = np.asarray(hits, dtype=float)
    if h.size == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = rng.choice(h, size=(n_boot, h.size), replace=True).mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


# --------------------------------------------------------------------------------------------
# Threshold trade-off and selection
# --------------------------------------------------------------------------------------------


def default_threshold_grid() -> np.ndarray:
    """41 log-spaced thresholds from 1e-5 to 0.9, rounded to 3 significant figures."""
    grid = np.geomspace(1e-5, 0.9, 41)
    return np.array([float(f"{t:.3g}") for t in grid])


def tradeoff_curve(
    scored: pd.DataFrame,
    windows: Sequence[EventWindow],
    zone_years: float,
    thresholds: Sequence[float] | None = None,
    horizon: timedelta | None = None,
) -> pd.DataFrame:
    """
    Event hit rate against false alarms across thresholds (one row per threshold).

    The default grid is log-spaced from 1e-5 to 0.9: flood rows are rare (about 1 in 30,000 on the
    real store), so well-calibrated probabilities that are worth alerting on sit far below 0.05.
    """
    grid = default_threshold_grid() if thresholds is None else thresholds
    rows = [evaluate_alerts(scored, windows, float(t), zone_years, horizon)[0] for t in grid]
    return pd.DataFrame(rows)


def select_threshold(curve: pd.DataFrame, budget_per_zone_year: float) -> pd.Series | None:
    """
    Best threshold within a false-alarm budget: highest hit rate among thresholds whose false
    episodes per zone-year fit the budget; ties go to the higher (quieter) threshold.
    Returns None when no threshold fits.
    """
    feasible = curve[curve["false_episodes_per_zone_year"] <= budget_per_zone_year]
    if feasible.empty:
        return None
    best = feasible.sort_values(["hit_rate", "threshold"], ascending=[False, False]).iloc[0]
    return best
