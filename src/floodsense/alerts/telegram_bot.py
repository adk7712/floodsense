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
    A rise dropped by the rate limit is remembered and re-checked once the hour has passed: if the
    zone is then still at that tier or higher, it is sent (a zone that fell back is forgotten).
  * "Back to Low" all-clear is OFF by default (``include_all_clear=True`` turns it on). When on,
    one all-clear is sent when a zone that was alerted drops to Low; it is not rate limited and does
    not reset the hourly clock.
  * Missing zones in a snapshot keep their last known tier; nothing is treated as dry.
  * Data coverage (``serving.coverage``): a zone-step with too few gauges has tier "No data". It
    never changes the remembered tier and never sends anything. A "partial" step (the last 60
    minutes had a gap) may still raise an alert, but never sends an all-clear or lowers the
    remembered tier; an all-clear needs an "ok" step. Live, gold carries only the current step's
    gauge count, so the bot tracks the last "No data" time per zone itself and treats the next
    60 minutes as partial.
  * The token is read from ``TELEGRAM_BOT_TOKEN`` and is never printed or logged.

Live mode (reads the Databricks gold table):
  * Provider: ``gold_latest_risk`` wraps ``serving.gold.latest_zone_risk()`` (newest gold row per
    zone, always all 55). If the zones are not all at one timestamp it logs a warning and sends
    nothing, rather than leave some zones on stale tiers. Zones with too few gauges are given the tier "No data".
  * State: each zone's last tier, last send time and last sent tier are saved to a small JSON file
    (``--state-file``, default ``data/processed/telegram_state.json``, gitignored) after every
    change, written to a temp file then renamed. A restart reloads it, so a zone already alerted at
    the same tier inside the rate-limit window is not re-sent. A missing or corrupt file means a
    fresh start with a logged warning (a fresh start can re-send: the first snapshot counts as a
    rise from Low). ``--dry-run`` neither reads nor writes the state file.
  * Errors: a failed gold read is logged and the cycle skipped; the loop keeps running. A failed
    send is logged (error class or HTTP status only, never the token) and the alert is NOT marked
    as sent: the zone's state is rolled back so the same rise is detected again next cycle and
    retried, and the hourly limit only starts once a send succeeds. Retries stop when the newest
    gold step is older than ``--max-age-minutes`` (default 30), so a stalled pipeline never
    produces stale alerts. ``live --once`` runs one cycle and exits with status 1 if the gold read
    or any send failed (so a scheduled job shows as failed), else 0.
  * Where and how often: as a Databricks job, ``databricks/jobs/telegram_alerts_job.json`` runs
    ``python -m floodsense.alerts.telegram_bot live --once --state-file <volume path>`` every 5
    minutes (gold has 5-minute steps). The token and chat id come from the secrets scope
    ``floodsense``, keys ``telegram_bot_token`` and ``telegram_chat_id``, exposed as
    ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID``. The job ships PAUSED. Alerts appear only while
    the rainfall poller and the gold pipeline are running; with them off, gold goes stale and the
    bot sends nothing. A 5-minute schedule means a compute start every 5 minutes: use serverless
    or a small job cluster and mind the quota, or run ``live`` in a loop on one always-on machine
    (``--interval-seconds 300``) instead.

Usage:
    python -m floodsense.alerts.telegram_bot replay --date 2021-04-17 [--dry-run] [--speed N]
    python -m floodsense.alerts.telegram_bot live [--dry-run] [--interval-seconds 300]
        [--once] [--state-file PATH] [--max-age-minutes 30] [--subscriptions-file PATH]

Per-area subscriptions (``--subscriptions-file``): people message the bot /subscribe <area>,
/unsubscribe <area>, /unsubscribe all, /list, /start or /help. Each live cycle first reads new
messages with ``getUpdates`` (the last handled update id is kept in the subscriptions file, so no
message is handled twice), then sends every alert the detector produces to the zone's subscribers
as well as to the channel. The rules are the detector's, not copies: rises only, the same
one-per-zone-per-hour limit, "No data" never alerts, no all-clear on missing data. A subscriber
send never blocks or retracts the channel alert; a chat that blocked the bot (HTTP 403) is
dropped. Without the flag the bot behaves exactly as before. Email sign-up is not built.
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from floodsense.alerts.messages import ADVICE, DISCLAIMER, HONESTY_LINE
from floodsense.alerts.subscriptions import (
    JsonSubscriptionStore,
    SubscriptionStore,
    process_updates,
)
from floodsense.common.config import MIN_REPORTING_STATIONS, settings
from floodsense.common.timeutil import to_sgt
from floodsense.data.replay import load_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.models.artifact import load_model
from floodsense.models.scoring import NO_DATA_TIER, score_zone_features
from floodsense.serving.coverage import coverage
from floodsense.serving.gold import GoldUnavailable, latest_zone_risk
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

logger = logging.getLogger("FloodSense.TelegramBot")

TIER_RANK = {"Low": 0, "Moderate": 1, "High": 2}
COVERAGE_VALUES = {"ok", "partial", "none"}
GAP_WINDOW = timedelta(minutes=60)  # the rain_60m window: how long after a gap data is "partial"
RATE_LIMIT = timedelta(hours=1)
TELEGRAM_API = "https://api.telegram.org"

# (timestamp, {zone: tier}) or (timestamp, {zone: tier}, {zone: "ok" | "partial" | "none"}).
Snapshot = (
    tuple[datetime, Mapping[str, str]] | tuple[datetime, Mapping[str, str], Mapping[str, str]]
)


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
    when = to_sgt(when)
    stamp = f"{when:%H:%M}, {when:%d %b %Y} SGT"
    name = display_name(zone)
    if tier == "Low":
        return (
            f"FloodSense all-clear: {name} is back to Low flood risk as of {stamp}.\n\n"
            f"{DISCLAIMER} {HONESTY_LINE}"
        )
    return (
        f"FloodSense {tier.upper()} flood risk: {name}\n"
        f"Time: {stamp}\n"
        # Deliberate: Moderate and High share this line. Calibrated probabilities are about
        # 0.5-0.7%, which does not justify "likely".
        f"Flash flood possible in the next hour.\n\n"
        f"What to do: {ADVICE[tier]}\n\n"
        f"{DISCLAIMER} {HONESTY_LINE}"
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
        self._pending: dict[str, str] = {}  # tier of a rise the rate limit dropped
        self._last_gap: dict[str, datetime] = {}  # last "No data" step per zone
        self._undo: dict[str, tuple[str | None, datetime | None, str | None, bool, str | None]] = {}

    def to_state(self) -> dict[str, object]:
        """JSON-able copy of the per-zone state (tiers, send times)."""
        return {
            "version": 1,
            "last_tier": dict(self._last_tier),
            "last_sent": {z: t.isoformat() for z, t in self._last_sent.items()},
            "last_sent_tier": dict(self._last_sent_tier),
            "alerted": sorted(self._alerted),
            "pending": dict(self._pending),
            "last_gap": {z: t.isoformat() for z, t in self._last_gap.items()},
        }

    def load_state(self, state: Mapping[str, object]) -> None:
        """Restore ``to_state()`` output. Raises ValueError/TypeError/KeyError if malformed."""
        last_tier = dict(state["last_tier"])  # type: ignore[call-overload]
        sent_tier = dict(state["last_sent_tier"])  # type: ignore[call-overload]
        # A naive saved time is read as SGT, so it can be compared with aware step times.
        last_sent = {
            str(z): to_sgt(datetime.fromisoformat(str(t)))
            for z, t in dict(state["last_sent"]).items()  # type: ignore[call-overload]
        }
        alerted = {str(z) for z in state["alerted"]}  # type: ignore[attr-defined]
        pending = {str(z): str(t) for z, t in dict(state.get("pending", {})).items()}  # type: ignore[call-overload]
        last_gap = {
            str(z): to_sgt(datetime.fromisoformat(str(t)))
            for z, t in dict(state.get("last_gap", {})).items()  # type: ignore[call-overload]
        }
        for tier in [*last_tier.values(), *sent_tier.values(), *pending.values()]:
            if tier not in TIER_RANK:
                raise ValueError(f"unknown tier {tier!r} in saved state")
        self._last_tier = {str(z): str(t) for z, t in last_tier.items()}
        self._last_sent_tier = {str(z): str(t) for z, t in sent_tier.items()}
        self._last_sent = last_sent
        self._alerted = alerted
        self._pending = pending
        self._last_gap = last_gap

    def retract(self, alert: Alert) -> None:
        """Undo the state change that produced ``alert`` (its send failed), so it can retry."""
        undo = self._undo.get(alert.zone)
        if undo is None:
            return
        tier, sent, sent_tier, alerted, pending = undo
        for table, value in (
            (self._last_tier, tier),
            (self._last_sent, sent),
            (self._last_sent_tier, sent_tier),
            (self._pending, pending),
        ):
            if value is None:
                table.pop(alert.zone, None)  # type: ignore[attr-defined]
            else:
                table[alert.zone] = value  # type: ignore[assignment]
        (self._alerted.add if alerted else self._alerted.discard)(alert.zone)

    def _coverage(self, zone: str, tier: str, ts: datetime, given: str | None) -> str:
        """ "none", "partial" or "ok" for this zone-step. "No data" is always "none"."""
        if tier == NO_DATA_TIER or given == "none":
            return "none"
        if given == "partial":
            return "partial"
        gap = self._last_gap.get(zone)
        if given is None and gap is not None and ts - gap < GAP_WINDOW:
            return "partial"
        return "ok"

    def feed(
        self,
        ts: datetime,
        tiers: Mapping[str, str],
        coverage: Mapping[str, str] | None = None,
    ) -> list[Alert]:
        """Alerts for one step. ``coverage`` maps zone -> "ok" | "partial" | "none" (default: from
        the tier "No data" and the gaps this detector has seen)."""
        ts = to_sgt(ts)
        cov_in = coverage or {}
        for zone, tier in tiers.items():
            if tier not in TIER_RANK and tier != NO_DATA_TIER:
                raise ValueError(f"unknown tier {tier!r} for {zone}")
            if cov_in.get(zone, "ok") not in COVERAGE_VALUES:
                raise ValueError(f"unknown coverage {cov_in[zone]!r} for {zone}")
        out: list[Alert] = []
        for zone in sorted(tiers):
            tier = tiers[zone]
            cov = self._coverage(zone, tier, ts, cov_in.get(zone))
            if cov == "none":
                self._last_gap[zone] = ts  # rain unknown, not dry: remember nothing else
                continue
            prev = self._last_tier.get(zone, "Low")
            self._undo[zone] = (
                self._last_tier.get(zone),
                self._last_sent.get(zone),
                self._last_sent_tier.get(zone),
                zone in self._alerted,
                self._pending.get(zone),
            )
            if cov == "partial" and TIER_RANK[tier] < TIER_RANK[prev]:
                continue  # a thin window can only understate rain: no downgrade, no all-clear
            self._last_tier[zone] = tier
            pending = self._pending.get(zone)
            if pending is not None and TIER_RANK[tier] < TIER_RANK[pending]:
                del self._pending[zone]  # fell back below the dropped rise: forget it
                pending = None
            rose = TIER_RANK[tier] > TIER_RANK[prev]
            if tier != "Low" and (rose or pending is not None):
                sent = self._last_sent.get(zone)
                limited = sent is not None and ts - sent < self.rate_limit
                escalation = (
                    self.allow_escalation
                    and TIER_RANK[tier] > TIER_RANK[self._last_sent_tier.get(zone, "Low")]
                )
                if limited and not escalation:
                    # Dropped for now; re-checked on later steps once the hour has passed.
                    self._pending[zone] = tier
                    continue
                self._pending.pop(zone, None)
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
    for snap in snapshots:
        yield from det.feed(*snap)


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


class TelegramSendError(RuntimeError):
    """A Bot API call failed. Carries the HTTP status (None if no response); never the token."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TelegramApi:
    """``getUpdates`` and ``sendMessage`` for subscriptions. ``post`` is injectable for tests.
    Never logs or prints the token; errors name only the class or HTTP status."""

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 15.0,
        post: Callable[..., requests.Response] | None = None,
    ) -> None:
        self._token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self._token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN must be set (in .env or the environment).")
        self._timeout = timeout
        self._post = post

    def _call(self, method: str, payload: dict[str, object]) -> object:
        post = self._post or requests.post
        try:
            resp = post(
                f"{TELEGRAM_API}/bot{self._token}/{method}", json=payload, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise TelegramSendError(f"Telegram request failed ({type(exc).__name__})") from None
        if resp.status_code != 200:
            raise TelegramSendError(
                f"Telegram rejected {method} (HTTP {resp.status_code})", resp.status_code
            )
        try:
            return resp.json().get("result")
        except (ValueError, AttributeError):
            raise TelegramSendError(f"Telegram sent an unreadable {method} reply") from None

    def get_updates(self, offset: int | None) -> list[Mapping[str, object]]:
        payload: dict[str, object] = {"timeout": 0, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload)
        return [u for u in result if isinstance(u, dict)] if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text})


SECRET_SCOPE = "floodsense"
SECRET_KEYS = {"TELEGRAM_BOT_TOKEN": "telegram_bot_token", "TELEGRAM_CHAT_ID": "telegram_chat_id"}


def load_databricks_secrets() -> None:
    """On Databricks, fill missing TELEGRAM_* env vars from the secrets scope ``floodsense``.

    Serverless job tasks cannot map secrets to env vars, so the bot reads them itself. Values go
    straight into ``os.environ``; nothing is printed or logged. Quietly does nothing elsewhere.
    """
    missing = {k: v for k, v in SECRET_KEYS.items() if not os.environ.get(k)}
    if not missing or "DATABRICKS_RUNTIME_VERSION" not in os.environ:
        return
    try:
        from databricks.sdk import WorkspaceClient

        secrets = WorkspaceClient().dbutils.secrets
        for env_name, key in missing.items():
            os.environ[env_name] = secrets.get(SECRET_SCOPE, key)
    except Exception as exc:
        logger.error("Could not read Databricks secrets (%s)", type(exc).__name__)


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
    """Per-step ({zone: tier}, {zone: coverage}) for a replayed day, via the replay + scoring path.

    Coverage is judged on the table including its warm-up, then steps with too few gauges get the
    tier "No data", so a gauge gap is never replayed as a dry spell (and never an all-clear).
    """
    if model is None:
        model = load_model()
    if day == date(2021, 4, 17) and settings.replay_file.exists():
        replay = load_replay(settings.replay_file)
        table = compute_zone_feature_table(replay.snapshots, replay.stations)
        table["coverage"] = coverage(table)
        stamps = pd.to_datetime(table["timestamp"])
        table = table[(stamps >= replay.display_start) & (stamps <= replay.display_end)]
    else:
        from floodsense.app import replay_days  # lazy: needs the large rainfall store

        if not replay_days.store_available():
            raise FileNotFoundError("No replay file or rainfall store for that date")
        table = replay_days.day_view(day).features
        if table.empty:
            raise ValueError(f"No rainfall readings for {day}: nothing to replay")
        if "coverage" not in table.columns:
            table = table.assign(coverage=coverage(table))
    return _score_snapshots(table, model)


def _score_snapshots(table: pd.DataFrame, model: object) -> list[Snapshot]:
    """Score a feature table that has a ``coverage`` column into per-step snapshots."""
    table = table.reset_index(drop=True)
    scored = score_zone_features(table, model)
    scored["coverage"] = table["coverage"].to_numpy()
    scored.loc[scored["coverage"] == "none", "risk_tier"] = NO_DATA_TIER
    out: list[Snapshot] = []
    for ts, g in scored.groupby("timestamp", sort=True):
        zones = g["ura_planning_area"]
        out.append(
            (
                pd.Timestamp(ts).to_pydatetime(),
                dict(zip(zones, g["risk_tier"], strict=True)),
                dict(zip(zones, g["coverage"], strict=True)),
            )
        )
    return out


# --- live ---------------------------------------------------------------------------------------

LatestRiskProvider = Callable[[], tuple[datetime, Mapping[str, str]]]

DEFAULT_STATE_FILE = settings.root_dir / "data" / "processed" / "telegram_state.json"
DEFAULT_MAX_AGE = timedelta(minutes=30)


def gold_latest_risk() -> tuple[datetime, Mapping[str, str]]:
    """(timestamp, {zone: tier}) for the newest scored step in the gold table.

    Wraps ``latest_zone_risk()`` (one row per zone). If the 55 rows do not share one timestamp it
    warns and returns no tiers. Zones with too few gauges get "No data". Raises ``GoldUnavailable``.
    """
    df = latest_zone_risk()
    if df.empty:
        raise GoldUnavailable("the gold table returned no rows")
    newest = df["timestamp"].max()
    df = df.copy()
    if "reporting_stations" in df.columns:  # too few gauges: the rain is unknown, not dry
        thin = df["reporting_stations"] < MIN_REPORTING_STATIONS
        df.loc[thin, "risk_tier"] = NO_DATA_TIER
    if df["timestamp"].nunique() != 1 or df["ura_planning_area"].nunique() != len(
        URA_PLANNING_AREAS
    ):
        logger.warning("Zones are not all at one step (newest %s); nothing sent this cycle", newest)
        return pd.Timestamp(newest).to_pydatetime(), {}
    tiers = dict(zip(df["ura_planning_area"], df["risk_tier"], strict=True))
    return pd.Timestamp(newest).to_pydatetime(), tiers


def load_detector_state(det: AlertDetector, path: Path | None) -> None:
    """Load saved state into ``det``. Missing file: quiet fresh start. Corrupt: warn, fresh."""
    if path is None or not path.exists():
        if path is not None:
            logger.info("No state file at %s; starting fresh", path)
        return
    try:
        det.load_state(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning("State file %s is unreadable (%s); starting fresh", path, type(exc).__name__)
        det.load_state(AlertDetector().to_state())


def save_detector_state(det: AlertDetector, path: Path | None) -> None:
    """Write state atomically (temp file in the same folder, then rename). Never raises."""
    if path is None:
        return
    tmp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False
        ) as tmp:
            tmp_name = tmp.name
            json.dump(det.to_state(), tmp)
        os.replace(tmp_name, path)
    except OSError as exc:
        logger.error("Could not save state to %s (%s)", path, type(exc).__name__)
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)


def _error_text(exc: Exception) -> str:
    # Our own sender raises RuntimeError with only the class / HTTP status. Anything else: class only.
    return str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__


def run_cycle(
    det: AlertDetector,
    provider: LatestRiskProvider,
    emit: Callable[[Alert], None],
    *,
    state_path: Path | None = None,
    max_age: timedelta | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    """One poll. Returns the number of problems (a failed gold read, or failed sends). Never raises."""
    try:
        ts, tiers = provider()
    except GoldUnavailable as exc:
        logger.error("Gold read failed, skipping this cycle: %s", exc)
        return 1
    except Exception as exc:
        logger.error("Gold read failed (%s), skipping this cycle", type(exc).__name__)
        return 1
    if max_age is not None and now is not None and to_sgt(now()) - to_sgt(ts) > max_age:
        logger.warning("Newest gold step %s is older than %s; nothing sent", ts, max_age)
        return 0
    problems = 0
    try:
        alerts = det.feed(ts, tiers)
    except ValueError as exc:  # an unknown tier or coverage value: log it, skip the cycle
        logger.error("Bad risk data, skipping this cycle: %s", exc)
        return 1
    for alert in alerts:
        try:
            emit(alert)
        except Exception as exc:
            problems += 1
            det.retract(alert)  # not sent: roll back so the next cycle detects the rise again
            logger.error(
                "Send failed for %s %s (%s); will retry next cycle",
                alert.zone,
                alert.tier,
                _error_text(exc),
            )
    save_detector_state(det, state_path)
    return problems


def process_commands(
    store: SubscriptionStore,
    api: TelegramApi,
    *,
    now: Callable[[], datetime] | None = None,
) -> int:
    """Read new bot messages, apply the commands, save, then send the replies. Returns the number
    of problems; never raises. The store is saved before replying so no update is handled twice."""
    when = now() if now is not None else datetime.now(settings.tzinfo)
    last = store.last_update_id()
    try:
        updates = api.get_updates(None if last is None else last + 1)
    except Exception as exc:
        logger.error("Could not read bot messages (%s)", _error_text(exc))
        return 1
    if not updates:
        return 0
    replies = process_updates(store, updates, when)
    try:
        store.save()
    except OSError as exc:
        logger.error("Could not save subscriptions (%s); replies not sent", type(exc).__name__)
        return 1
    problems = 0
    for chat_id, text in replies:
        try:
            api.send_message(chat_id, text)
        except Exception as exc:
            problems += 1
            logger.error("Reply failed (%s)", _error_text(exc))
    return problems


def subscriber_emitter(
    channel_emit: Callable[[Alert], None], store: SubscriptionStore, api: TelegramApi
) -> Callable[[Alert], None]:
    """Send each alert to the channel first, then privately to that zone's subscribers.

    A channel failure raises (the detector retracts and retries, subscribers not yet messaged).
    A subscriber failure is logged only; HTTP 403 (the chat blocked the bot) drops that chat."""

    def emit(alert: Alert) -> None:
        channel_emit(alert)
        dropped = False
        for chat_id in store.chats_for(alert.zone):
            try:
                api.send_message(chat_id, alert.text)
            except TelegramSendError as exc:
                logger.error("Subscriber send failed (%s)", exc)
                if exc.status == 403:
                    store.remove_all(chat_id)
                    dropped = True
            except Exception as exc:
                logger.error("Subscriber send failed (%s)", type(exc).__name__)
        if dropped:
            try:
                store.save()
            except OSError as exc:
                logger.error("Could not save subscriptions (%s)", type(exc).__name__)

    return emit


def run_live(
    provider: LatestRiskProvider,
    emit: Callable[[Alert], None],
    *,
    interval_seconds: float = 300.0,
    max_polls: int | None = None,
    include_all_clear: bool = False,
    state_path: Path | None = None,
    max_age: timedelta | None = None,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    pre_cycle: Callable[[], int] | None = None,
) -> int:
    """Poll ``provider`` and emit alerts; survives bad cycles. Returns the total problem count.

    ``max_polls=1`` is the ``--once`` mode. Re-feeding an unchanged step is harmless (no rise, no
    alert), which is also what makes a failed send retry on the next poll.
    """
    det = AlertDetector(include_all_clear=include_all_clear)
    load_detector_state(det, state_path)
    problems = 0
    polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        if pre_cycle is not None:  # e.g. handle subscription commands before alerts go out
            problems += pre_cycle()
        problems += run_cycle(det, provider, emit, state_path=state_path, max_age=max_age, now=now)
        if max_polls is None or polls < max_polls:
            sleep(interval_seconds)
    return problems


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


def _commands_hook(store: SubscriptionStore, api: TelegramApi) -> Callable[[], int]:
    return lambda: process_commands(store, api, now=lambda: datetime.now(settings.tzinfo))


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
    li = sub.add_parser("live", help="poll the latest zone risk from the Databricks gold table")
    li.add_argument("--dry-run", action="store_true", help="print only; no state file is used")
    li.add_argument("--once", action="store_true", help="run one cycle and exit (scheduled job)")
    li.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"JSON file with per-zone state (default {DEFAULT_STATE_FILE})",
    )
    li.add_argument(
        "--max-age-minutes",
        type=float,
        default=DEFAULT_MAX_AGE.total_seconds() / 60,
        help="ignore a newest gold step older than this",
    )
    li.add_argument(
        "--subscriptions-file",
        type=Path,
        default=None,
        help="JSON file for per-area subscriptions; turns subscriptions on (default: off). "
        "Locally use data/processed/telegram_subscriptions.json (gitignored).",
    )
    li.add_argument("--interval-seconds", type=float, default=300.0)
    li.add_argument("--all-clear", action="store_true")
    args = parser.parse_args(argv)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    load_dotenv_file()
    if not args.dry_run:
        load_databricks_secrets()
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
            pre_cycle: Callable[[], int] | None = None
            if args.subscriptions_file is not None and not args.dry_run:
                store = JsonSubscriptionStore(args.subscriptions_file)
                api = TelegramApi()
                emit = subscriber_emitter(emit, store, api)
                pre_cycle = _commands_hook(store, api)
            problems = run_live(
                gold_latest_risk,
                emit,
                interval_seconds=args.interval_seconds,
                max_polls=1 if args.once else None,
                include_all_clear=args.all_clear,
                state_path=None if args.dry_run else args.state_file,
                max_age=timedelta(minutes=args.max_age_minutes),
                now=lambda: datetime.now(settings.tzinfo),
                pre_cycle=pre_cycle,
            )
            if args.once and problems:
                return 1
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
