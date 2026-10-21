"""Offline tests for Telegram subscriptions: fake HTTP client, no network, no token."""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from floodsense.alerts import subscriptions as subs
from floodsense.alerts import telegram_bot as tb
from floodsense.alerts.messages import HELP_TEXT, HONESTY_LINE, MAX_AREAS_PER_CHAT, PRIVACY_NOTE

SGT = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 22, 12, 0, tzinfo=SGT)


class FakeResponse:
    def __init__(self, status: int = 200, result: object = None) -> None:
        self.status_code = status
        self._result = result

    def json(self) -> dict[str, object]:
        return {"ok": True, "result": self._result}


class FakeHttp:
    """Stands in for ``requests.post``: records calls, serves queued getUpdates results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[dict[str, Any]] = []
        self.fail_chats: dict[int, int] = {}  # chat id -> HTTP status for sendMessage

    def __call__(self, url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, json))
        if method == "getUpdates":
            offset = json.get("offset", 0)
            return FakeResponse(result=[u for u in self.updates if u["update_id"] >= offset])
        status = self.fail_chats.get(json["chat_id"], 200)
        return FakeResponse(status=status)

    def sent(self) -> list[tuple[int, str]]:
        return [(p["chat_id"], p["text"]) for m, p in self.calls if m == "sendMessage"]


def msg(uid: int, chat: int, text: str, kind: str = "private") -> dict[str, Any]:
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": kind}, "text": text}}


@pytest.fixture
def store(tmp_path: Path) -> subs.JsonSubscriptionStore:
    return subs.JsonSubscriptionStore(tmp_path / "subs.json")


def test_subscribe_accepts_any_case_and_separators(store: subs.JsonSubscriptionStore) -> None:
    for i, text in enumerate(
        ["/subscribe Bukit Timah", "/subscribe BUKIT_MERAH", "/subscribe ang-mo-kio"]
    ):
        reply = subs.handle_message(store, 1, text, T0)
        assert reply.startswith("Subscribed to"), (i, reply)
    assert store.areas_for(1) == ["ANG MO KIO", "BUKIT MERAH", "BUKIT TIMAH"]
    assert subs.parse_command("/Subscribe@fs_tele_alert_bot  Bukit Timah") == (
        "subscribe",
        "Bukit Timah",
    )


def test_unknown_area_gets_helpful_reply(store: subs.JsonSubscriptionStore) -> None:
    reply = subs.handle_message(store, 1, "/subscribe Bukit Timh", T0)
    assert "Did you mean Bukit Timah" in reply and "planning area" in reply
    assert store.areas_for(1) == []
    assert "Bukit Timah" in subs.handle_message(store, 1, "/subscribe Narnia", T0)
    assert "which area" in subs.handle_message(store, 1, "/subscribe", T0)


def test_duplicate_and_cap(store: subs.JsonSubscriptionStore) -> None:
    areas = ["Bedok", "Tampines", "Clementi", "Queenstown", "Tanglin"]
    assert len(areas) == MAX_AREAS_PER_CHAT
    for a in areas:
        subs.handle_message(store, 7, f"/subscribe {a}", T0)
    assert "already follow" in subs.handle_message(store, 7, "/subscribe bedok", T0)
    assert "at most 5" in subs.handle_message(store, 7, "/subscribe Yishun", T0)
    assert len(store.areas_for(7)) == 5
    subs.handle_message(store, 8, "/subscribe Yishun", T0)  # the cap is per chat
    assert store.areas_for(8) == ["YISHUN"]


def test_unsubscribe_one_and_all(store: subs.JsonSubscriptionStore) -> None:
    for a in ("Bedok", "Tampines"):
        subs.handle_message(store, 1, f"/subscribe {a}", T0)
    subs.handle_message(store, 2, "/subscribe Bedok", T0)
    assert subs.handle_message(store, 1, "/unsubscribe bedok", T0) == "Unsubscribed from Bedok."
    assert "not following" in subs.handle_message(store, 1, "/unsubscribe Bedok", T0)
    assert "deleted your data" in subs.handle_message(store, 1, "/unsubscribe ALL", T0)
    assert store.areas_for(1) == [] and store.areas_for(2) == ["BEDOK"]
    assert "no subscriptions" in subs.handle_message(store, 1, "/unsubscribe all", T0)


def test_list(store: subs.JsonSubscriptionStore) -> None:
    assert "not following" in subs.handle_message(store, 1, "/list", T0)
    subs.handle_message(store, 1, "/subscribe Bedok", T0)
    assert "Bedok" in subs.handle_message(store, 1, "/list", T0)


@pytest.mark.parametrize("cmd", ["/start", "/help"])
def test_start_and_help_text(store: subs.JsonSubscriptionStore, cmd: str) -> None:
    reply = subs.handle_message(store, 1, cmd, T0)
    assert reply == HELP_TEXT
    for needle in ("/subscribe", "/unsubscribe", "/unsubscribe all", "/list", "/help"):
        assert needle in reply
    assert HONESTY_LINE in reply
    assert PRIVACY_NOTE in reply
    assert PRIVACY_NOTE == (
        "We store only your chat ID and the areas you pick, to send you alerts. "
        "Send /unsubscribe all to delete them."
    )


def test_store_keeps_only_chat_area_time(tmp_path: Path) -> None:
    path = tmp_path / "sub.json"
    s = subs.JsonSubscriptionStore(path)
    s.add(5, "BEDOK", T0)
    s.set_last_update_id(41)
    s.save()
    raw = json.loads(path.read_text())
    assert set(raw) == {"version", "last_update_id", "subscriptions"}
    assert set(raw["subscriptions"][0]) == {"chat_id", "area", "subscribed_at"}
    again = subs.JsonSubscriptionStore(path)
    assert again.areas_for(5) == ["BEDOK"] and again.last_update_id() == 41
    assert again.chats_for("BEDOK") == [5]


def test_corrupt_file_starts_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "sub.json"
    path.write_text("{not json")
    with caplog.at_level(logging.WARNING, logger="FloodSense.Subscriptions"):
        s = subs.JsonSubscriptionStore(path)
    assert s.areas_for(1) == [] and s.last_update_id() is None and "unreadable" in caplog.text


def test_process_updates_skips_groups_and_old_ids(store: subs.JsonSubscriptionStore) -> None:
    store.set_last_update_id(10)
    updates = [
        msg(10, 1, "/subscribe Bedok"),  # already handled
        msg(11, 2, "/subscribe Bedok", kind="group"),
        {"update_id": 12, "message": {"chat": {"id": 3, "type": "private"}}},  # no text
        {"update_id": 13, "edited_message": {}},
        msg(14, 4, "/subscribe Bedok"),
    ]
    replies = subs.process_updates(store, updates, T0)
    assert [c for c, _ in replies] == [4]
    assert store.chats_for("BEDOK") == [4] and store.last_update_id() == 14


def test_process_commands_offset_and_no_double_handling(tmp_path: Path) -> None:
    http = FakeHttp()
    http.updates = [msg(100, 1, "/subscribe Bedok"), msg(101, 1, "/list")]
    api = tb.TelegramApi(token="SECRET:TOKEN", post=http)
    path = tmp_path / "s.json"
    store = subs.JsonSubscriptionStore(path)
    assert tb.process_commands(store, api, now=lambda: T0) == 0
    assert "offset" not in http.calls[0][1]  # first run: no offset yet
    assert len(http.sent()) == 2
    # next job run: fresh store from disk, asks for offset 102, nothing is handled twice
    store2 = subs.JsonSubscriptionStore(path)
    assert store2.last_update_id() == 101
    http.calls.clear()
    assert tb.process_commands(store2, api, now=lambda: T0) == 0
    assert http.calls == [
        ("getUpdates", {"timeout": 0, "allowed_updates": ["message"], "offset": 102})
    ]
    assert http.sent() == []


def test_process_commands_failures_are_problems_not_crashes(
    store: subs.JsonSubscriptionStore, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        raise tb.requests.ConnectionError(f"failed {url}")

    api = tb.TelegramApi(token="SECRET:TOKEN", post=boom)
    with caplog.at_level(logging.ERROR, logger="FloodSense.TelegramBot"):
        assert tb.process_commands(store, api, now=lambda: T0) == 1
    assert "SECRET" not in caplog.text


def test_subscriber_gets_alert_after_channel_and_other_areas_do_not(
    store: subs.JsonSubscriptionStore,
) -> None:
    store.add(1, "BEDOK", T0)
    store.add(2, "TANGLIN", T0)
    http = FakeHttp()
    api = tb.TelegramApi(token="SECRET:TOKEN", post=http)
    order: list[str] = []
    emit = tb.subscriber_emitter(lambda a: order.append("channel"), store, api)
    det = tb.AlertDetector()
    for alert in det.feed(T0, {"BEDOK": "Moderate", "TANGLIN": "Low"}):
        emit(alert)
    assert order == ["channel"]
    assert [c for c, _ in http.sent()] == [1]
    text = http.sent()[0][1]
    assert "MODERATE flood risk: Bedok" in text and HONESTY_LINE in text


def test_subscribers_follow_the_detector_rules(store: subs.JsonSubscriptionStore) -> None:
    """Rate limit, no-data and no-all-clear are the detector's: subscribers see the same alerts."""
    store.add(1, "BEDOK", T0)
    http = FakeHttp()
    api = tb.TelegramApi(token="SECRET:TOKEN", post=http)
    emit = tb.subscriber_emitter(lambda a: None, store, api)
    det = tb.AlertDetector()
    steps = [
        (0, "Moderate"),  # alert
        (5, "Low"),
        (10, "Moderate"),  # rise again inside the hour: rate limited
        (15, "No data"),  # missing data: nothing
        (20, "Low"),  # no all-clear (off by default)
        (25, "High"),  # escalation: let through
    ]
    for minutes, tier in steps:
        for alert in det.feed(T0 + timedelta(minutes=minutes), {"BEDOK": tier}):
            emit(alert)
    tiers = [t.split()[1] for _, t in http.sent()]
    assert tiers == ["MODERATE", "HIGH"]


def test_channel_failure_skips_subscribers_and_retracts(
    store: subs.JsonSubscriptionStore,
) -> None:
    store.add(1, "BEDOK", T0)
    http = FakeHttp()
    api = tb.TelegramApi(token="SECRET:TOKEN", post=http)

    def bad_channel(alert: tb.Alert) -> None:
        raise RuntimeError("channel down")

    det = tb.AlertDetector()
    got: list[tb.Alert] = []
    problems = tb.run_cycle(
        det, lambda: (T0, {"BEDOK": "High"}), tb.subscriber_emitter(bad_channel, store, api)
    )
    assert problems == 1 and http.sent() == []
    # retried next cycle, now delivered to the subscriber
    emit = tb.subscriber_emitter(got.append, store, api)
    assert tb.run_cycle(det, lambda: (T0, {"BEDOK": "High"}), emit) == 0
    assert len(got) == 1 and len(http.sent()) == 1


def test_blocked_chat_is_dropped_and_others_still_served(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "s.json"
    store = subs.JsonSubscriptionStore(path)
    store.add(1, "BEDOK", T0)
    store.add(2, "BEDOK", T0)
    http = FakeHttp()
    http.fail_chats = {1: 403}
    api = tb.TelegramApi(token="SECRET:TOKEN", post=http)
    emit = tb.subscriber_emitter(lambda a: None, store, api)
    with caplog.at_level(logging.ERROR, logger="FloodSense.TelegramBot"):
        for alert in tb.AlertDetector().feed(T0, {"BEDOK": "High"}):
            emit(alert)
    assert store.chats_for("BEDOK") == [2]
    assert subs.JsonSubscriptionStore(path).chats_for("BEDOK") == [2]  # drop was saved
    assert "SECRET" not in caplog.text


def test_run_live_runs_pre_cycle_each_poll() -> None:
    calls: list[int] = []

    def hook() -> int:
        calls.append(1)
        return 0

    tb.run_live(
        lambda: (T0, {"A": "Low"}),
        lambda a: None,
        max_polls=2,
        sleep=lambda _s: None,
        pre_cycle=hook,
    )
    assert len(calls) == 2


def test_subscriptions_are_off_without_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing behaviour is unchanged: no flag, no getUpdates."""
    monkeypatch.setattr(tb, "gold_latest_risk", lambda: (T0, {"BEDOK": "Low"}))
    monkeypatch.setattr(tb, "load_dotenv_file", lambda: None)
    monkeypatch.setattr(tb, "load_databricks_secrets", lambda: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SECRET:TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    posts: list[str] = []
    monkeypatch.setattr(tb.requests, "post", lambda url, **kw: posts.append(url) or FakeResponse())
    assert tb.main(["live", "--once", "--max-age-minutes", "1e9"]) == 0
    assert not any("getUpdates" in u for u in posts)
