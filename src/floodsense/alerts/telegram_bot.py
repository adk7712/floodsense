"""
FloodSense - Telegram alert bot (plan item A5).

Posts a message when a zone's risk tier rises to Moderate or High. It reuses the existing replay
and scoring path (``replay_days`` / ``data.replay`` -> ``compute_zone_feature_table`` ->
``score_zone_features``); it does not reimplement the model.

Rules (documented choices):
  * An alert fires only when a zone's tier RISES to Moderate or High (Low->Moderate, Low->High,
    Moderate->High). Staying at a tier, or falling, sends nothing.
  * Rate limit: at most one message per zone per rolling hour, with ONE deliberate exception: a
    rise to a HIGHER tier than the last message sent (i.e. Moderate then High) is let through
    (``allow_escalation=True``, the default). Without it the replay of 17 Apr 2021 sends no High
    message at all, because zones go Moderate -> High within the hour. After a High message the
    zone is silent for an hour. ``allow_escalation=False`` gives the strict one-per-hour rule.
    The rate limit does not queue: a dropped rise is not re-sent later.
  * "Back to Low" all-clear is OFF by default (``include_all_clear=True`` turns it on). When on,
    one all-clear is sent when a zone that was alerted drops to Low; it is not rate limited and does
    not reset the hourly clock.
  * Missing zones in a snapshot keep their last known tier; nothing is treated as dry.
  * The token is read from ``TELEGRAM_BOT_TOKEN`` and is never printed or logged.

Usage:
    python -m floodsense.alerts.telegram_bot replay --date 2021-04-17 [--dry-run] [--speed N]
    python -m floodsense.alerts.telegram_bot live [--dry-run] [--interval-seconds 300]
"""

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from floodsense.common.config import settings
from floodsense.data.replay import load_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.models.artifact import load_model
from floodsense.models.scoring import score_zone_features

logger = logging.getLogger("FloodSense.TelegramBot")

TIER_RANK = {"Low": 0, "Moderate": 1, "High": 2}
RATE_LIMIT = timedelta(hours=1)
TELEGRAM_API = "https://api.telegram.org"

Snapshot = tuple[datetime, Mapping[str, str]]

ADVICE = {
    "High": (
        "Avoid underpasses and low-lying roads. Do not drive or walk through floodwater. "
        "Check PUB's alerts for what is happening near you."
    ),
    "Moderate": "Be ready: keep an eye on the weather and check PUB's channels for updates.",
}
DISCLAIMER = "FloodSense complements, and does not replace, PUB's flood alerts."


@dataclass(frozen=True)
class Alert:
    zone: str
    tier: str  # "Moderate", "High", or "Low" (all-clear)
    previous: str
    timestamp: datetime
    text: str


def display_name(zone: str) -> str:
    """'BUKIT MERAH' -> 'Bukit Merah'."""
    return zone.title()


def format_message(zone: str, tier: str, when: datetime) -> str:
    """Plain-language message for a zone reaching ``tier`` at ``when`` (SGT)."""
    stamp = f"{when:%H:%M}, {when:%d %b %Y} SGT"
    name = display_name(zone)
    if tier == "Low":
        return (
            f"FloodSense all-clear: {name} is back to Low flood risk as of {stamp}.\n\n{DISCLAIMER}"
        )
    return (
        f"FloodSense {tier.upper()} flood risk: {name}\n"
        f"Time: {stamp}\n"
        f"Flash flood possible in the next hour.\n\n"
        f"What to do: {ADVICE[tier]}\n\n"
        f"{DISCLAIMER}"
    )


class AlertDetector:
    """Stateful tier-change detector. Feed it time-ordered snapshots; it returns new alerts."""

    def __init__(
        self,
        *,
        include_all_clear: bool = False,
        allow_escalation: bool = True,
        rate_limit: timedelta = RATE_LIMIT,
    ) -> None:
        self.include_all_clear = include_all_clear
        self.allow_escalation = allow_escalation
        self.rate_limit = rate_limit
        self._last_tier: dict[str, str] = {}
        self._last_sent: dict[str, datetime] = {}
        self._last_sent_tier: dict[str, str] = {}
        self._alerted: set[str] = set()

    def feed(self, ts: datetime, tiers: Mapping[str, str]) -> list[Alert]:
        out: list[Alert] = []
        for zone in sorted(tiers):
            tier = tiers[zone]
            if tier not in TIER_RANK:
                raise ValueError(f"unknown tier {tier!r} for {zone}")
            prev = self._last_tier.get(zone, "Low")
            self._last_tier[zone] = tier
            if tier != "Low" and TIER_RANK[tier] > TIER_RANK[prev]:
                sent = self._last_sent.get(zone)
                limited = sent is not None and ts - sent < self.rate_limit
                escalation = (
                    self.allow_escalation
                    and TIER_RANK[tier] > TIER_RANK[self._last_sent_tier.get(zone, "Low")]
                )
                if limited and not escalation:
                    continue
                self._last_sent[zone] = ts
                self._last_sent_tier[zone] = tier
                self._alerted.add(zone)
                out.append(Alert(zone, tier, prev, ts, format_message(zone, tier, ts)))
            elif self.include_all_clear and tier == "Low" and zone in self._alerted:
                self._alerted.discard(zone)
                out.append(Alert(zone, tier, prev, ts, format_message(zone, tier, ts)))
        return out


def detect_alerts(
    snapshots: Iterable[Snapshot],
    *,
    include_all_clear: bool = False,
    allow_escalation: bool = True,
    rate_limit: timedelta = RATE_LIMIT,
) -> Iterator[Alert]:
    """Yield alerts from time-ordered (timestamp, {zone: tier}) snapshots. Pure: no I/O."""
    det = AlertDetector(
        include_all_clear=include_all_clear,
        allow_escalation=allow_escalation,
        rate_limit=rate_limit,
    )
    for ts, tiers in snapshots:
        yield from det.feed(ts, tiers)


class TelegramSender:
    """Posts text to a chat via the Bot API ``sendMessage``. Never logs or prints the token."""

    def __init__(
        self, token: str | None = None, chat_id: str | None = None, timeout: float = 15.0
    ) -> None:
        self._token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self._timeout = timeout
        if not self._token or not self._chat_id:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (in .env or the environment). "
                "Use --dry-run to print messages without sending."
            )

    def send(self, text: str) -> None:
        url = f"{TELEGRAM_API}/bot{self._token}/sendMessage"
        try:
            resp = requests.post(
                url, json={"chat_id": self._chat_id, "text": text}, timeout=self._timeout
            )
        except requests.RequestException as exc:
            # str(exc) can contain the URL, which holds the token: report only the class.
            raise RuntimeError(f"Telegram request failed ({type(exc).__name__})") from None
        if resp.status_code != 200:
            raise RuntimeError(f"Telegram rejected the message (HTTP {resp.status_code})")


def load_dotenv_file(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from the gitignored .env into os.environ (existing vars win)."""
    env = path or settings.root_dir / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# --- replay -------------------------------------------------------------------------------------


def replay_snapshots(day: date, model: object | None = None) -> list[Snapshot]:
    """Per-step {zone: tier} for a replayed day, from the existing replay + scoring path."""
    if model is None:
        model = load_model()
    if day == date(2021, 4, 17) and settings.replay_file.exists():
        replay = load_replay(settings.replay_file)
        table = compute_zone_feature_table(replay.snapshots, replay.stations)
        stamps = pd.to_datetime(table["timestamp"])
        table = table[(stamps >= replay.display_start) & (stamps <= replay.display_end)]
    else:
        from floodsense.app import replay_days  # lazy: needs the large rainfall store

        if not replay_days.store_available():
            raise FileNotFoundError("No replay file or rainfall store for that date")
        table = replay_days.day_view(day).features
        if table.empty:
            raise ValueError(f"No rainfall readings for {day}: nothing to replay")
    scored = score_zone_features(table.reset_index(drop=True), model)
    out: list[Snapshot] = []
    for ts, g in scored.groupby("timestamp", sort=True):
        out.append(
            (
                pd.Timestamp(ts).to_pydatetime(),
                dict(zip(g["ura_planning_area"], g["risk_tier"], strict=True)),
            )
        )
    return out


# --- live ---------------------------------------------------------------------------------------

LatestRiskProvider = Callable[[], tuple[datetime, Mapping[str, str]]]


def databricks_latest_risk() -> tuple[datetime, Mapping[str, str]]:
    """
    STUB. Return (timestamp, {zone: tier}) for the newest scored step.

    A teammate must replace this with a function that reads the latest rows of the Databricks gold
    table (``PREDICTION_COLUMNS`` in ``serving.pipeline_core``: ``ura_planning_area``, ``timestamp``,
    ``risk_tier``). Pass it to ``run_live(provider=...)``. It deliberately raises rather than
    returning made-up data.
    """
    raise NotImplementedError(
        "No live risk provider is wired up yet. Supply a function returning "
        "(timestamp, {zone: tier}) from the Databricks gold table and pass it to run_live()."
    )


def run_live(
    provider: LatestRiskProvider,
    emit: Callable[[Alert], None],
    *,
    interval_seconds: float = 300.0,
    max_polls: int | None = None,
    include_all_clear: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Poll ``provider`` and emit alerts. State is kept across polls (fresh timestamps only)."""
    det = AlertDetector(include_all_clear=include_all_clear)
    last_ts: datetime | None = None
    polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        ts, tiers = provider()
        if last_ts is None or ts > last_ts:  # only fresh steps; a repeat poll changes nothing
            last_ts = ts
            for alert in det.feed(ts, tiers):
                emit(alert)
        if max_polls is None or polls < max_polls:
            sleep(interval_seconds)


# --- CLI ----------------------------------------------------------------------------------------


def _emitter(dry_run: bool) -> Callable[[Alert], None]:
    if dry_run:

        def emit(alert: Alert) -> None:
            print(
                f"--- [{alert.timestamp:%Y-%m-%d %H:%M}] {alert.zone} {alert.previous} -> {alert.tier}"
            )
            print(alert.text)
            print()

        return emit
    sender = TelegramSender()

    def send(alert: Alert) -> None:
        sender.send(alert.text)
        print(f"sent: {alert.zone} {alert.tier} {alert.timestamp:%H:%M}")

    return send


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="feed a replayed storm through the alert logic")
    r.add_argument("--date", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    r.add_argument(
        "--dry-run", action="store_true", help="print instead of sending; no token needed"
    )
    r.add_argument(
        "--speed", type=float, default=0.0, help="replayed minutes per real second (0 = instant)"
    )
    r.add_argument("--all-clear", action="store_true", help="also send back-to-Low messages")
    li = sub.add_parser("live", help="poll the latest zone risk (provider is a stub)")
    li.add_argument("--dry-run", action="store_true")
    li.add_argument("--interval-seconds", type=float, default=300.0)
    li.add_argument("--all-clear", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv_file()
    try:
        emit = _emitter(args.dry_run)
        if args.cmd == "replay":
            snaps = replay_snapshots(args.date)
            prev_ts: datetime | None = None
            count = 0
            for alert in detect_alerts(snaps, include_all_clear=args.all_clear):
                if args.speed > 0 and prev_ts is not None:
                    wait = (alert.timestamp - prev_ts).total_seconds() / 60 / args.speed
                    time.sleep(max(wait, 0.0))
                prev_ts = alert.timestamp
                emit(alert)
                count += 1
            print(f"{count} alert(s) for {args.date}", file=sys.stderr)
        else:
            run_live(
                databricks_latest_risk,
                emit,
                interval_seconds=args.interval_seconds,
                include_all_clear=args.all_clear,
            )
    except (RuntimeError, NotImplementedError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
