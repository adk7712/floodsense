"""
FloodSense - Backtest a stored storm through the scoring path the app uses.

Replays a storm file (warm-up included) through ``compute_zone_feature_table`` and
``predict_probabilities``, then reports when each zone would have received Moderate and High alerts
and, when sourced flood events are available, whether those alerts came in time.

This is the baseline every future model has to beat, so it is deliberately plain:

- Features are computed over the WHOLE replay (so the 72 h warm-up is used) and only then cut to the
  display window, exactly like the app.
- Thresholds apply to unrounded probabilities, as in ``score_zone_features`` and the evaluation,
  so a zone's alert times here match the tier the app shows and the numbers in the final report.
- Events are never invented: with none supplied and no event file, the summary says why.

Usage:
    python -m floodsense.models.backtest --replay data/replay/2021-04-17_western_storm.json \\
        [--model auto|heuristic|<path>] [--event "ZONE|2021-04-17T13:30|approx_hour" ...] \\
        [--out DIR]
"""

import argparse
import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from floodsense.common.config import settings
from floodsense.common.timeutil import to_sgt
from floodsense.data.flood_events import load_flood_events
from floodsense.data.replay import Replay, load_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.labels.policy import EventWindow, event_window
from floodsense.models.artifact import FloodModel, load_model
from floodsense.models.evaluation import alert_episodes, evaluate_alerts
from floodsense.models.scoring import default_thresholds, predict_probabilities

logger = logging.getLogger("FloodSense.Backtest")

PRECISIONS = ("exact", "approx_15min", "approx_hour", "day_only")
TIMELINE_ZONES = 8
DAYS_PER_YEAR = 365.25

ModelLike = FloodModel


@dataclass(frozen=True)
class BacktestEvent:
    """A flood event with the attributes ``event_window`` reads (FloodEvent-compatible)."""

    ura_planning_area: str
    timestamp_start: datetime
    time_precision: str = "approx_hour"
    timestamp_end: datetime | None = None
    event_id: str | None = None


# --------------------------------------------------------------------------------------------
# Model selection
# --------------------------------------------------------------------------------------------


def resolve_model(model: str | Path | None) -> tuple[ModelLike | None, dict[str, Any]]:
    """Turn the ``model`` argument into (model or None for the heuristic, description)."""
    spec = "auto" if model is None else str(model)
    loaded: ModelLike | None
    if spec == "auto":
        loaded = load_model()
        source = "artifact.load_model()"
    elif spec == "heuristic":
        loaded, source = None, "rainfall heuristic"
    else:
        loaded = joblib.load(spec)
        if not isinstance(loaded, FloodModel):
            raise TypeError(f"{spec} is not a FloodModel")
        source = spec

    if loaded is None:
        kind, provenance = "heuristic", {"note": "no trained model"}
    else:
        kind, provenance = "flood_model", loaded.provenance
    return loaded, {"requested": spec, "kind": kind, "source": source, "provenance": provenance}


# --------------------------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------------------------


def _resolve_events(
    events: Sequence[Any] | None, replay: Replay
) -> tuple[list[Any], dict[str, Any]]:
    """Events to evaluate against, and a record of where they came from (or why there are none)."""
    if events is not None:
        if not events:
            return [], {"source": "none", "reason": "an empty event list was passed"}
        return list(events), {"source": "argument", "reason": None}

    try:
        loaded = load_flood_events()
    except NotImplementedError as exc:
        return [], {"source": "none", "reason": f"load_flood_events not implemented yet: {exc}"}
    except FileNotFoundError as exc:
        return [], {"source": "none", "reason": f"flood events file missing: {exc}"}

    lo, hi = pd.Timestamp(replay.display_start), pd.Timestamp(replay.display_end)
    in_window = []
    for e in loaded:
        w = event_window(e)
        if w.start_hi >= lo and w.start_lo <= hi:
            in_window.append(e)
    reason = None if in_window else "no sourced events fall in the display window"
    return in_window, {"source": "flood_events_file", "reason": reason, "loaded": len(loaded)}


def _window_record(w: EventWindow) -> dict[str, Any]:
    return {
        "event_id": w.event_id,
        "zone": w.zone,
        "precision": w.precision,
        "start_lo": w.start_lo,
        "start_hi": w.start_hi,
        "end_hi": w.end_hi,
    }


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _jsonable(obj: Any) -> Any:
    """Recursively convert to JSON-safe values: ISO timestamps, NaN -> None, numpy -> python."""
    if isinstance(obj, Mapping):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if obj is pd.NaT:
        return None
    if isinstance(obj, datetime):  # includes pd.Timestamp
        return obj.isoformat()
    if isinstance(obj, np.generic):
        return _jsonable(obj.item())
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, str | int | bool) or obj is None:
        return obj
    return str(obj)


def _first_at_or_above(scored: pd.DataFrame, threshold: float) -> pd.Series:
    """First timestamp per zone with prob >= threshold (zones never alerting are absent)."""
    hit = scored.loc[scored["prob"] >= threshold]
    return hit.groupby("ura_planning_area")["timestamp"].min()


def _evaluate(
    scored: pd.DataFrame, windows: list[EventWindow], threshold: float, zone_years: float
) -> dict[str, Any]:
    summary, outcomes, _ = evaluate_alerts(scored, windows, threshold, zone_years)
    known = set(scored["ura_planning_area"].unique())
    return {
        "summary": summary,
        "outcomes": [
            {
                "event_id": o.event_id,
                "zone": o.zone,
                "zone_in_replay": o.zone in known,
                "hit": bool(o.hit),
                "first_alert": o.first_alert,
                "lead_minutes": o.lead_minutes,
            }
            for o in outcomes
        ],
    }


# --------------------------------------------------------------------------------------------
# Backtest
# --------------------------------------------------------------------------------------------


def run_backtest(
    replay_path: str | Path,
    model: str | Path | None = "auto",
    events: Sequence[Any] | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Backtest ``replay_path``; write ``summary.json`` and ``timeline.html``; return the summary."""
    replay_path = Path(replay_path)
    out = (
        Path(out_dir)
        if out_dir is not None
        else settings.root_dir / "data" / "reports" / "backtests" / replay_path.stem
    )

    replay = load_replay(replay_path)
    scorer, model_info = resolve_model(model)
    event_list, event_info = _resolve_events(events, replay)
    windows = [event_window(e) for e in event_list]

    # Whole replay first (warm-up), then cut to the display window.
    features = compute_zone_feature_table(replay.snapshots, replay.stations)
    stamps = pd.to_datetime(features["timestamp"])
    in_window = (stamps >= pd.Timestamp(replay.display_start)) & (
        stamps <= pd.Timestamp(replay.display_end)
    )
    features = features.loc[in_window].reset_index(drop=True)
    if features.empty:
        raise ValueError("No feature rows fall inside the replay's display window")

    thresholds_from_model = getattr(scorer, "thresholds", None)
    thresholds = dict(thresholds_from_model) if thresholds_from_model else default_thresholds()
    probs = predict_probabilities(features, scorer)  # unrounded, as the app and evaluation
    scored = pd.DataFrame(
        {
            "ura_planning_area": features["ura_planning_area"],
            "timestamp": pd.to_datetime(features["timestamp"]),
            "prob": probs,
        }
    )

    # Per zone
    peak_idx = scored.groupby("ura_planning_area")["prob"].idxmax()  # first occurrence of the max
    first_mod = _first_at_or_above(scored, thresholds["moderate"])
    first_high = _first_at_or_above(scored, thresholds["high"])
    episodes = {
        name: alert_episodes(scored, thresholds[name]).groupby("ura_planning_area")
        for name in ("moderate", "high")
    }
    zones: dict[str, Any] = {}
    for zone, idx in peak_idx.items():
        zone_eps: dict[str, list[dict[str, Any]]] = {}
        for name, grouped in episodes.items():
            if zone in grouped.groups:
                eps = grouped.get_group(zone)
                zone_eps[name] = [
                    {"start": r.start, "end": r.end, "steps": int(r.steps)}
                    for r in eps.itertuples()
                ]
            else:
                zone_eps[name] = []
        zones[str(zone)] = {
            "peak_probability": float(scored.at[idx, "prob"]),
            "peak_time": scored.at[idx, "timestamp"],
            "first_moderate": first_mod.get(zone),
            "first_high": first_high.get(zone),
            "episodes": zone_eps,
        }

    # Events
    evaluation: dict[str, Any] | None = None
    if windows:
        years = (replay.display_end - replay.display_start) / timedelta(days=DAYS_PER_YEAR)
        zone_years = len(zones) * years
        evaluation = {
            "zone_years": zone_years,
            "high": _evaluate(scored, windows, thresholds["high"], zone_years),
            "moderate": _evaluate(scored, windows, thresholds["moderate"], zone_years),
        }

    out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "replay": {
            "path": str(replay_path),
            "event_name": replay.event_name,
            "source": replay.source,
        },
        "display_window": {"start": replay.display_start, "end": replay.display_end},
        "model": model_info,
        "thresholds": {
            "values": thresholds,
            "source": "model" if thresholds_from_model else "default",
        },
        "n_zones": len(zones),
        "n_timesteps": int(scored["timestamp"].nunique()),
        "events": {
            **event_info,
            "count": len(windows),
            "windows": [_window_record(w) for w in windows],
        },
        "zones": zones,
        "evaluation": evaluation,
        "outputs": {
            "summary_json": str(out / "summary.json"),
            "timeline_html": str(out / "timeline.html"),
        },
    }
    summary = _jsonable(summary)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    _write_timeline(out / "timeline.html", scored, zones, thresholds, windows, model_info, replay)
    return summary


def _write_timeline(
    path: Path,
    scored: pd.DataFrame,
    zones: dict[str, Any],
    thresholds: dict[str, float],
    windows: list[EventWindow],
    model_info: dict[str, Any],
    replay: Replay,
) -> None:
    import plotly.graph_objects as go

    top = sorted(zones, key=lambda z: zones[z]["peak_probability"], reverse=True)[:TIMELINE_ZONES]
    fig = go.Figure()
    for zone in top:
        rows = scored.loc[scored["ura_planning_area"] == zone].sort_values("timestamp")
        fig.add_trace(
            go.Scatter(
                x=rows["timestamp"].dt.tz_localize(None),
                y=rows["prob"],
                mode="lines",
                name=zone,
                hovertemplate="%{x|%H:%M}  p=%{y:.3f}<extra>" + zone + "</extra>",
            )
        )
    for name in ("moderate", "high"):
        fig.add_hline(
            y=thresholds[name],
            line_dash="dash",
            annotation_text=f"{name.capitalize()} {thresholds[name]:.2f}",
            annotation_position="top left",
        )
    for w in windows:
        lo = w.start_lo.tz_localize(None).to_pydatetime()
        hi = w.start_hi.tz_localize(None).to_pydatetime()
        fig.add_vrect(x0=lo, x1=hi, opacity=0.2, line_width=0, fillcolor="grey")
        mid = lo + (hi - lo) / 2
        fig.add_vline(x=mid, line_width=1, line_dash="dot")
        fig.add_annotation(
            x=mid, y=1.0, yref="paper", text=w.zone, showarrow=False, textangle=-90, yanchor="top"
        )

    fig.update_layout(
        title=(
            f"{replay.event_name}: backtest with {model_info['kind']} model; "
            f"top {len(top)} zones by peak probability"
        ),
        xaxis_title="Time (SGT)",
        yaxis_title="Flood probability",
        yaxis_range=[0, 1.02],
        hovermode="x unified",
    )
    fig.write_html(str(path), include_plotlyjs="cdn")


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def parse_event(text: str) -> BacktestEvent:
    """Parse ``ZONE|ISO time|precision`` (naive time = SGT; precision defaults to approx_hour)."""
    parts = [p.strip() for p in text.split("|")]
    if len(parts) not in (2, 3) or not parts[0]:
        raise ValueError(f"Event must look like 'ZONE|2021-04-17T13:30|approx_hour', got {text!r}")
    precision = parts[2] if len(parts) == 3 else "approx_hour"
    if precision not in PRECISIONS:
        raise ValueError(f"Unknown precision {precision!r}; expected one of {PRECISIONS}")
    return BacktestEvent(
        ura_planning_area=parts[0].upper(),
        timestamp_start=to_sgt(datetime.fromisoformat(parts[1])),
        time_precision=precision,
    )


def _fmt_time(value: str | None) -> str:
    return "-" if value is None else datetime.fromisoformat(value).strftime("%H:%M")


def format_report(summary: dict[str, Any], top: int = 15) -> str:
    """Compact text report: zone table (top zones by peak, plus event zones) and event outcomes."""
    m, t, w = summary["model"], summary["thresholds"], summary["display_window"]
    lines = [
        f"Backtest: {summary['replay']['event_name']}  "
        f"({_fmt_time(w['start'])}-{_fmt_time(w['end'])} SGT, {summary['n_zones']} zones)",
        f"Model: {m['kind']} ({m['source']})",
        f"Thresholds ({t['source']}): moderate={t['values']['moderate']:.3f} "
        f"high={t['values']['high']:.3f}",
    ]
    zones = summary["zones"]
    order = sorted(zones, key=lambda z: zones[z]["peak_probability"], reverse=True)
    event_zones = {x["zone"] for x in summary["events"]["windows"]}
    shown = order if top <= 0 else order[:top] + [z for z in order[top:] if z in event_zones]
    lines.append("")
    lines.append(f"{'ZONE':<24}{'PEAK':>6}  {'AT':>5}  {'1ST MODERATE':>12}  {'1ST HIGH':>8}")
    for z in shown:
        r = zones[z]
        lines.append(
            f"{z:<24}{r['peak_probability']:>7.4f}  {_fmt_time(r['peak_time']):>5}  "
            f"{_fmt_time(r['first_moderate']):>12}  {_fmt_time(r['first_high']):>8}"
        )
    if len(shown) < len(order):
        lines.append(f"... {len(order) - len(shown)} more zones in summary.json")

    lines.append("")
    ev = summary["events"]
    if summary["evaluation"] is None:
        lines.append(f"Events: none ({ev['reason']})")
    else:
        lines.append(f"Events: {ev['count']} ({ev['source']})")
        for level in ("high", "moderate"):
            res = summary["evaluation"][level]
            s = res["summary"]
            # Raw counts: annualising a single storm window (e.g. per zone-year) is meaningless.
            lines.append(
                f"  {level.upper()} >= {s['threshold']:.3f}: hits {s['hits']:.0f}/{s['events']:.0f}, "
                f"false alarm episodes {s['false_episodes']:.0f} of {s['episodes']:.0f}"
            )
            for o in res["outcomes"]:
                lead = "-" if o["lead_minutes"] is None else f"{o['lead_minutes']:.0f} min"
                note = "" if o["zone_in_replay"] else "  (zone not in replay)"
                lines.append(
                    f"    {o['event_id']}: {'HIT' if o['hit'] else 'MISS'}  "
                    f"first alert {_fmt_time(o['first_alert'])}  lead {lead}{note}"
                )
    lines.append("")
    lines.append(f"Wrote {summary['outputs']['summary_json']}")
    lines.append(f"Wrote {summary['outputs']['timeline_html']}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="Backtest a stored storm replay.")
    parser.add_argument("--replay", type=Path, default=settings.replay_file)
    parser.add_argument(
        "--model", default="auto", help="auto | heuristic | path to a FloodModel joblib"
    )
    parser.add_argument(
        "--event",
        action="append",
        default=None,
        metavar="ZONE|ISO_TIME|PRECISION",
        help=f"Recorded flood; repeatable. PRECISION is one of {', '.join(PRECISIONS)}",
    )
    parser.add_argument("--out", type=Path, default=None, help="Output directory")
    parser.add_argument("--top", type=int, default=15, help="Zones to print (0 = all)")
    args = parser.parse_args(argv)

    events: list[BacktestEvent] | None = None
    if args.event:
        try:
            events = [parse_event(e) for e in args.event]
        except ValueError as exc:
            parser.error(str(exc))
    summary = run_backtest(args.replay, model=args.model, events=events, out_dir=args.out)
    print(format_report(summary, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
