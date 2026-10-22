"""
FloodSense - Live scorecard against PUB: what did FloodSense show before each PUB flash-flood alert?

This is LIVE MONITORING, not an accuracy result. There are few PUB alerts, no matching "quiet"
period is scored, and the scores are rescored from archived gauge readings (the gold table keeps
only the last 24 hours, so it can't answer "what was shown last week"). Every number printed here
is a count with a 90% interval and the reminder that it is too small to judge the model by.

For each PUB "Alert" (a flash flood observed) the scorecard looks at FloodSense's scores for the
planning area containing the alert point in the 60 minutes up to the alert, and reports the highest
tier and probability, and the warning time: minutes from the first Moderate or High step to the
alert. Scores with no data ("No data" tier, or no rows) are never counted as Low.

    python -m floodsense.monitoring.pub_scorecard \\
        --alerts-dir <folder of flood-alert JSON pages> \\
        --rainfall-dir <folder of rainfall JSON pages, with 72 h before the first alert> \\
        [--since 2026-10-06] [--out scorecard.csv]
    python -m floodsense.monitoring.pub_scorecard --alerts-dir ... --scores history.parquet

``--scores`` is a table with the gold columns (``PREDICTION_COLUMNS``) over the alert period, if you
already have one. The pure functions take any alerts and any score history, so the Databricks job
(``databricks/jobs/pub_scorecard.py``) reuses them.
"""

import argparse
import json
import logging
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from shapely.geometry import Point

from floodsense.common.config import settings
from floodsense.common.timeutil import to_sgt
from floodsense.ingestion.flood_alerts import (
    FloodAlert,
    _zone_index,
    parse_flood_alerts,
    zone_for_point,
)
from floodsense.models.evaluation import hit_rate_ci

logger = logging.getLogger("FloodSense.PubScorecard")

SINCE_DEFAULT = date(2026, 10, 6)  # the first PUB alerts were archived from 6 Oct 2026
WINDOW_MINUTES = 60
FLAG_TIERS = ("Moderate", "High")
TIER_RANK = {"Low": 0, "Moderate": 1, "High": 2}
KM_PER_DEGREE = 111.0  # near the equator, so one factor serves latitude and longitude
SMALL_N = 10  # below this many scored alerts a rate says almost nothing

CAVEAT = (
    "Live monitoring, not an accuracy result: few alerts, scores rescored from archived gauge "
    "readings, no quiet periods scored. Do not read the rate as the model's hit rate. Uses 2026 "
    "data inside the 2024-2026 test window, never for tuning: any rate is a second look."
)

SCORECARD_COLUMNS = [
    "alert_id",
    "issued_at",
    "zone",
    "zones_touched",
    "area_desc",
    "window_start",
    "steps_scored",
    "status",
    "max_tier",
    "max_probability",
    "moderate_or_higher",
    "first_flag_at",
    "warning_minutes",
]


def zones_touched(alert: FloodAlert) -> list[str]:
    """Planning areas the alert's circle overlaps, its centre area first (empty without a point).

    The circle is drawn flat in degrees (111 km per degree), which is close enough at 1.3 deg N for
    a radius of about a kilometre. The scorecard scores the centre area only, as the app does."""
    if alert.latitude is None or alert.longitude is None:
        return []
    tree, names, geoms = _zone_index()
    radius_deg = (alert.radius_km or 0.0) / KM_PER_DEGREE
    circle = Point(alert.longitude, alert.latitude).buffer(max(radius_deg, 1e-6))
    hits = {names[int(i)] for i in tree.query(circle) if geoms[int(i)].intersects(circle)}
    centre = alert.zone or zone_for_point(alert.latitude, alert.longitude)
    ordered = sorted(hits - {centre})
    return ([centre] if centre else []) + ordered


def unique_alerts(
    alerts: Iterable[FloodAlert], since: datetime | date | None = None
) -> list[FloodAlert]:
    """ "Alert" messages (not Cancels), one per identifier, oldest first, issued at or after ``since``
    (a date means 00:00 SGT that day)."""
    cutoff: datetime | None = None
    if isinstance(since, datetime):
        cutoff = to_sgt(since)
    elif since is not None:
        cutoff = datetime(since.year, since.month, since.day, tzinfo=settings.tzinfo)
    seen: set[str] = set()
    out = []
    for a in sorted(alerts, key=lambda a: a.issued_at):
        if a.msg_type != "Alert" or (cutoff is not None and a.issued_at < cutoff):
            continue
        key = a.identifier or f"{a.issued_at.isoformat()}|{a.latitude}|{a.longitude}"
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def _window_scores(
    history: pd.DataFrame, zone: str, issued: pd.Timestamp, window_minutes: int
) -> pd.DataFrame:
    """The zone's score rows with ``issued - window <= timestamp <= issued``, oldest first, with
    the "No data" tier and missing probabilities left out (unknown is not Low)."""
    ts = pd.to_datetime(history["timestamp"])
    start = issued - pd.Timedelta(minutes=window_minutes)
    keep = (
        (history["ura_planning_area"].astype(str).str.upper() == zone.upper())
        & (ts >= start)
        & (ts <= issued)
        & history["risk_tier"].isin(TIER_RANK)
        & history["flood_probability"].notna()
    )
    return history.loc[keep].assign(timestamp=ts[keep]).sort_values("timestamp")


def score_alert(
    alert: FloodAlert, history: pd.DataFrame, window_minutes: int = WINDOW_MINUTES
) -> dict[str, Any]:
    """One scorecard row for an alert. ``history`` has ``ura_planning_area``, ``timestamp`` (SGT),
    ``flood_probability`` and ``risk_tier`` (the gold columns), one row per zone and 5-minute step.

    ``status`` is "scored", or "no_score_data" when the alert has no area (e.g. at sea) or FloodSense
    has no usable score for the area in the window; such alerts have empty tier, probability and
    warning, and are left out of the rate."""
    issued = pd.Timestamp(alert.issued_at)
    touched = zones_touched(alert)
    row: dict[str, Any] = {
        "alert_id": alert.identifier,
        "issued_at": alert.issued_at,
        "zone": alert.zone,
        "zones_touched": ", ".join(touched),
        "area_desc": alert.area_desc,
        "window_start": alert.issued_at - timedelta(minutes=window_minutes),
        "steps_scored": 0,
        "status": "no_score_data",
        "max_tier": None,
        "max_probability": None,
        "moderate_or_higher": None,
        "first_flag_at": None,
        "warning_minutes": None,
    }
    if not alert.zone:
        return row
    rows = _window_scores(history, alert.zone, issued, window_minutes)
    row["steps_scored"] = len(rows)
    if rows.empty:
        return row
    row["status"] = "scored"
    ranks = rows["risk_tier"].map(TIER_RANK)
    row["max_tier"] = next(t for t, r in TIER_RANK.items() if r == int(ranks.max()))
    row["max_probability"] = float(rows["flood_probability"].max())
    flagged = rows[rows["risk_tier"].isin(FLAG_TIERS)]
    row["moderate_or_higher"] = not flagged.empty
    if not flagged.empty:
        first = pd.Timestamp(flagged["timestamp"].iloc[0])
        row["first_flag_at"] = first.to_pydatetime()
        row["warning_minutes"] = (issued - first).total_seconds() / 60.0
    return row


def build_scorecard(
    alerts: Iterable[FloodAlert],
    history: pd.DataFrame,
    since: datetime | date | None = SINCE_DEFAULT,
    window_minutes: int = WINDOW_MINUTES,
) -> pd.DataFrame:
    """One row per PUB alert since ``since`` (``SCORECARD_COLUMNS``), oldest first."""
    rows = [score_alert(a, history, window_minutes) for a in unique_alerts(alerts, since)]
    return pd.DataFrame(rows, columns=SCORECARD_COLUMNS)


@dataclass(frozen=True)
class ScorecardSummary:
    n_alerts: int  # every alert since the start date
    n_scored: int  # alerts with a usable FloodSense score in the window
    n_no_score_data: int
    n_moderate_or_higher: int  # of the scored alerts
    rate: float  # nan when nothing was scored
    ci_low: float
    ci_high: float  # 90% bootstrap interval (evaluation.hit_rate_ci), nan when nothing was scored

    def text(self) -> str:
        """Plain-language headline with raw counts, the interval and the caveat."""
        if self.n_alerts == 0:
            return f"No PUB flash-flood alerts in the period. {CAVEAT}"
        if self.n_scored == 0:
            return f"{self.n_alerts} PUB alert(s), none with a usable FloodSense score. {CAVEAT}"
        text = (
            f"{self.n_moderate_or_higher} of {self.n_scored} PUB alerts had Moderate or High in "
            f"that area in the 60 minutes before "
            f"({self.rate:.0%}, 90% interval {self.ci_low:.0%} to {self.ci_high:.0%})"
        )
        if self.n_no_score_data:
            text += f"; {self.n_no_score_data} more had no usable score and are not counted"
        if self.n_scored < SMALL_N:
            text += f". Only {self.n_scored} alerts: far too few to judge the model"
        return f"{text}. {CAVEAT}"


def summarise(scorecard: pd.DataFrame) -> ScorecardSummary:
    """Counts of alerts flagged beforehand and a 90% interval (the repo's ``hit_rate_ci``)."""
    scored = scorecard[scorecard["status"] == "scored"]
    hits = [bool(v) for v in scored["moderate_or_higher"]]
    nan = float("nan")
    low, high = hit_rate_ci(hits) if hits else (nan, nan)
    return ScorecardSummary(
        n_alerts=len(scorecard),
        n_scored=len(scored),
        n_no_score_data=len(scorecard) - len(scored),
        n_moderate_or_higher=sum(hits),
        rate=sum(hits) / len(hits) if hits else nan,
        ci_low=low,
        ci_high=high,
    )


# --------------------------------------------------------------------------------------------
# Local inputs (CLI)
# --------------------------------------------------------------------------------------------


def load_alerts(alerts_dir: Path) -> list[FloodAlert]:
    """All alert readings in the API pages (``*.json``) under ``alerts_dir``."""
    alerts: list[FloodAlert] = []
    for path in sorted(alerts_dir.rglob("*.json")):
        try:
            alerts.extend(parse_flood_alerts(json.loads(path.read_text())))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable alert file %s: %s", path, exc)
    return alerts


def rescore_history(
    rainfall_dir: Path,
    alerts: Sequence[FloodAlert],
    window_minutes: int = WINDOW_MINUTES,
    model: Any = None,
) -> pd.DataFrame:
    """Score archived rainfall pages (``*.json`` under ``rainfall_dir``) for the steps in the
    ``window_minutes`` before each alert. The pages must cover 72 hours before the first of those
    steps (the wet-ground feature). Uses ``load_model()`` unless a ``model`` is given."""
    from floodsense.serving.pipeline_core import payloads_to_readings, score_window

    if not alerts:
        return pd.DataFrame()
    payloads = []
    for path in sorted(rainfall_dir.rglob("*.json")):
        try:
            payloads.append(json.loads(path.read_text()))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable rainfall file %s: %s", path, exc)
    if not payloads:
        raise ValueError(f"no rainfall JSON files under {rainfall_dir}")
    readings, stations = payloads_to_readings(payloads)
    first = min(a.issued_at for a in alerts) - timedelta(minutes=window_minutes)
    last = max(a.issued_at for a in alerts)
    return score_window(readings, stations, model, emit_from=first, emit_to=last)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--alerts-dir", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--rainfall-dir", type=Path, help="rainfall API pages to rescore")
    group.add_argument("--scores", type=Path, help="parquet or csv with the gold columns")
    parser.add_argument("--since", type=date.fromisoformat, default=SINCE_DEFAULT)
    parser.add_argument("--window-minutes", type=int, default=WINDOW_MINUTES)
    parser.add_argument("--out", type=Path, help="write the scorecard as csv")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    alerts = unique_alerts(load_alerts(args.alerts_dir), args.since)
    if args.scores:
        history = (
            pd.read_parquet(args.scores)
            if args.scores.suffix == ".parquet"
            else pd.read_csv(args.scores)
        )
        history["timestamp"] = pd.to_datetime(history["timestamp"], utc=True).dt.tz_convert(
            settings.tzinfo
        )
    elif alerts:
        history = rescore_history(args.rainfall_dir, alerts, args.window_minutes)
    else:
        history = pd.DataFrame()
    if history.empty:
        history = pd.DataFrame(
            columns=["ura_planning_area", "timestamp", "flood_probability", "risk_tier"]
        )
    card = build_scorecard(alerts, history, args.since, args.window_minutes)
    if args.out:
        card.to_csv(args.out, index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(card.drop(columns=["area_desc", "window_start"]).to_string(index=False))
    print(summarise(card).text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
