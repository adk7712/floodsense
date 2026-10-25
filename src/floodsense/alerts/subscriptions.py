"""
FloodSense - Telegram alert subscriptions: who follows which planning area.

People message the bot ``/subscribe Bukit Timah`` and get a private alert when that area rises to
Moderate or High. This module holds the storage and the command handling; the alert rules (rises,
rate limit, no data, no all-clear on missing data) stay in ``telegram_bot.AlertDetector`` and the
bot sends each alert it produces to the zone's subscribers, so nothing is duplicated here.

Privacy: for each subscription we store only the chat id, the area and the time it was added.
No names, usernames or message text. ``/unsubscribe all`` deletes the chat's rows. The only other
stored value is the id of the last Telegram update handled, so no message is handled twice.

Storage sits behind ``SubscriptionStore``. ``JsonSubscriptionStore`` is one JSON file, written
atomically: a gitignored path locally (``data/processed/``) and a file on the Databricks artifacts
volume for the job. Email sign-up is not built.
"""

import json
import logging
import shutil
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from difflib import get_close_matches
from pathlib import Path
from typing import Protocol

from floodsense.alerts.messages import HELP_TEXT, MAX_AREAS_PER_CHAT
from floodsense.common.fileio import write_json_atomic
from floodsense.common.timeutil import to_sgt
from floodsense.models.scoring import NO_DATA_TIER
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS, area_from_slug

logger = logging.getLogger("FloodSense.Subscriptions")

EXAMPLE_AREAS = "Bukit Timah, Ang Mo Kio, Jurong East"

# The live job's staleness limit: a newest gold step older than this sends nothing.
DEFAULT_MAX_AGE = timedelta(minutes=30)
# /status hides a reading older than this. Longer than DEFAULT_MAX_AGE because the state file is
# written once per 30-minute poller run, some minutes after the step it holds; the reply always
# shows the reading's time.
STATUS_MAX_AGE = timedelta(minutes=60)

# Newest saved risk step: (time, {zone: tier}), or None when there is none.
StatusProvider = Callable[[], tuple[datetime, Mapping[str, str]] | None]


def area_label(area: str) -> str:
    """'BUKIT TIMAH' -> 'Bukit Timah'."""
    return area.title()


class SubscriptionStore(Protocol):
    """What the bot needs from storage. Implementations must persist ``save()`` durably."""

    def areas_for(self, chat_id: int) -> list[str]: ...

    def chats_for(self, area: str) -> list[int]: ...

    def add(self, chat_id: int, area: str, when: datetime) -> None: ...

    def remove(self, chat_id: int, area: str) -> bool: ...

    def remove_all(self, chat_id: int) -> int: ...

    def last_update_id(self) -> int | None: ...

    def set_last_update_id(self, update_id: int) -> None: ...

    def save(self) -> None: ...


class JsonSubscriptionStore:
    """Subscriptions and the last handled update id in one JSON file. A missing file is empty.
    Each save first copies the previous file to ``<name>.bak``. A corrupt file is kept aside as
    ``<name>.corrupt`` (never overwritten unread) and the ``.bak`` copy is loaded instead; if that
    is unreadable too, the store starts empty (nobody is messaged wrongly, people re-subscribe)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.backup = path.with_name(path.name + ".bak")
        self._rows: list[dict[str, object]] = []
        self._offset: int | None = None
        self._load()

    @staticmethod
    def _read(path: Path) -> tuple[list[dict[str, object]], int | None]:
        raw = json.loads(path.read_text())
        rows: list[dict[str, object]] = [
            {
                "chat_id": int(r["chat_id"]),
                "area": str(r["area"]),
                "subscribed_at": str(r["subscribed_at"]),
            }
            for r in raw["subscriptions"]
        ]
        offset = raw.get("last_update_id")
        return rows, None if offset is None else int(offset)

    def _load(self) -> None:
        if not self.path.exists():
            return
        unreadable = (OSError, ValueError, TypeError, KeyError, AttributeError)
        try:
            self._rows, self._offset = self._read(self.path)
            return
        except unreadable as exc:
            logger.warning("Subscription file %s unreadable (%s)", self.path, exc)
        try:
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".corrupt"))
        except OSError as exc:
            logger.warning("Could not keep a copy of the unreadable file (%s)", exc)
        try:
            self._rows, self._offset = self._read(self.backup)
            logger.warning("Loaded the backup %s instead", self.backup)
        except unreadable as exc:
            logger.warning("Backup %s unreadable too (%s); starting empty", self.backup, exc)
            self._rows, self._offset = [], None

    def areas_for(self, chat_id: int) -> list[str]:
        return sorted(str(r["area"]) for r in self._rows if r["chat_id"] == chat_id)

    def chats_for(self, area: str) -> list[int]:
        return sorted({int(str(r["chat_id"])) for r in self._rows if r["area"] == area})

    def add(self, chat_id: int, area: str, when: datetime) -> None:
        if area in self.areas_for(chat_id):
            return
        self._rows.append(
            {"chat_id": chat_id, "area": area, "subscribed_at": to_sgt(when).isoformat()}
        )

    def remove(self, chat_id: int, area: str) -> bool:
        keep = [r for r in self._rows if not (r["chat_id"] == chat_id and r["area"] == area)]
        changed = len(keep) != len(self._rows)
        self._rows = keep
        return changed

    def remove_all(self, chat_id: int) -> int:
        keep = [r for r in self._rows if r["chat_id"] != chat_id]
        removed = len(self._rows) - len(keep)
        self._rows = keep
        return removed

    def last_update_id(self) -> int | None:
        return self._offset

    def set_last_update_id(self, update_id: int) -> None:
        self._offset = update_id

    def save(self) -> None:
        """Write atomically (temp file in the same folder, then rename), after copying the
        previous file to the backup. Raises OSError."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.backup)
        data = {"version": 1, "last_update_id": self._offset, "subscriptions": self._rows}
        write_json_atomic(self.path, data)


def parse_command(text: str) -> tuple[str, str]:
    """'/Subscribe@MyBot  Bukit Timah' -> ('subscribe', 'Bukit Timah'). Not a command: ('', text)."""
    text = text.strip()
    if not text.startswith("/"):
        return "", text
    head, _, rest = text.partition(" ")
    cmd = head[1:].split("@", 1)[0].lower()
    return cmd, rest.strip()


def _unknown_area_reply(name: str) -> str:
    shown = name if len(name) <= 40 else name[:40] + "..."
    names = {area_label(a): a for a in URA_PLANNING_AREAS}
    close = get_close_matches(name.strip().title(), list(names), n=3, cutoff=0.6)
    hint = f" Did you mean {', '.join(close)}?" if close else ""
    return (
        f'I don\'t know "{shown}" as a Singapore planning area.{hint} '
        f"Use a planning area name such as {EXAMPLE_AREAS}. Capitals, spaces, hyphens and "
        "underscores don't matter."
    )


def _list_reply(areas: Sequence[str]) -> str:
    if not areas:
        return "You are not following any areas. Send /subscribe followed by an area name."
    names = ", ".join(area_label(a) for a in areas)
    return f"You follow {len(areas)} of {MAX_AREAS_PER_CHAT} areas: {names}."


def _status_line(area: str, tier: str | None) -> str:
    if tier is None or tier == NO_DATA_TIER:  # unknown is never shown as Low
        return f"{area_label(area)}: no reading (too few rain gauges reporting)"
    return f"{area_label(area)}: {tier}"


def _status_reply(
    areas: Sequence[str],
    arg: str,
    status: StatusProvider | None,
    when: datetime,
    max_age: timedelta,
) -> str:
    if arg:
        area = area_from_slug(arg)
        if area is None:
            return _unknown_area_reply(arg)
        areas = [area]
    elif not areas:
        return (
            "You are not following any areas. Send /subscribe followed by an area name, "
            "or /status followed by an area name."
        )
    if status is None:
        return "Status isn't available right now."
    snap = status()
    if snap is None or to_sgt(when) - to_sgt(snap[0]) > max_age:
        return "No recent reading. The risk feed may be paused."
    as_of, tiers = snap
    lines = [_status_line(a, tiers.get(a)) for a in areas]
    return "\n".join([f"Flood risk as of {to_sgt(as_of):%H:%M} SGT:", *lines])


def handle_message(
    store: SubscriptionStore,
    chat_id: int,
    text: str,
    when: datetime,
    status: StatusProvider | None = None,
    status_max_age: timedelta = STATUS_MAX_AGE,
) -> str:
    """Apply one command to ``store`` (caller saves) and return the reply text. ``status``
    returns the newest saved risk step for /status."""
    cmd, arg = parse_command(text)
    if cmd in ("start", "help"):
        return HELP_TEXT
    if cmd == "list":
        return _list_reply(store.areas_for(chat_id))
    if cmd == "status":
        return _status_reply(store.areas_for(chat_id), arg, status, when, status_max_age)
    if cmd == "subscribe":
        if not arg:
            return "Tell me which area, for example: /subscribe Bukit Timah"
        area = area_from_slug(arg)
        if area is None:
            return _unknown_area_reply(arg)
        mine = store.areas_for(chat_id)
        if area in mine:
            return f"You already follow {area_label(area)}."
        if len(mine) >= MAX_AREAS_PER_CHAT:
            return (
                f"You can follow at most {MAX_AREAS_PER_CHAT} areas. Send /unsubscribe followed "
                "by an area name to free a place, then try again."
            )
        store.add(chat_id, area, when)
        return (
            f"Subscribed to {area_label(area)}. I will message you when its flood risk rises to "
            "Moderate or High."
        )
    if cmd == "unsubscribe":
        if not arg:
            return "Tell me which area, for example: /unsubscribe Bukit Timah, or /unsubscribe all"
        if arg.lower() == "all":
            n = store.remove_all(chat_id)
            return (
                "Removed all your subscriptions and deleted your data."
                if n
                else "You had no subscriptions. Nothing is stored about this chat."
            )
        area = area_from_slug(arg)
        if area is None:
            return _unknown_area_reply(arg)
        if store.remove(chat_id, area):
            return f"Unsubscribed from {area_label(area)}."
        return f"You were not following {area_label(area)}."
    return "I didn't understand that. Send /help to see what I can do."


def process_updates(
    store: SubscriptionStore,
    updates: Sequence[Mapping[str, object]],
    when: datetime,
    status: StatusProvider | None = None,
) -> list[tuple[int, str]]:
    """Handle Telegram ``getUpdates`` results in order. Returns ``[(chat_id, reply)]``.

    Only text messages in private chats are handled (groups and channels are ignored). Advances the
    stored update id past every update seen, handled or not. The caller saves the store BEFORE
    sending the replies, so an update is never handled twice (a lost reply is not retried).
    """
    replies: list[tuple[int, str]] = []
    for upd in updates:
        uid = upd.get("update_id")
        if not isinstance(uid, int):
            continue
        last = store.last_update_id()
        if last is not None and uid <= last:
            continue
        store.set_last_update_id(uid)
        msg = upd.get("message")
        if not isinstance(msg, dict):
            continue
        chat = msg.get("chat")
        text = msg.get("text")
        if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(text, str):
            continue
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            continue
        replies.append((chat_id, handle_message(store, chat_id, text, when, status)))
    return replies
