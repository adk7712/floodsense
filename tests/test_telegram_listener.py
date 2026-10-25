"""Offline tests for ``listen`` and ``live --alerts-only``: fake HTTP, fake sleep, no token."""

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from floodsense.alerts import subscriptions as subs
from floodsense.alerts import telegram_bot as tb

SGT = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 22, 12, 0, tzinfo=SGT)
TOKEN = "SECRET:TOKEN"


class FakeResponse:
    def __init__(self, status: int = 200, body: object = None) -> None:
        self.status_code = status
        self._body = body if body is not None else {"ok": status == 200, "result": []}

    def json(self) -> object:
        return self._body


Step = FakeResponse | Exception | Callable[[], Any]


class ScriptedHttp:
    """Stands in for ``requests.post``. getUpdates answers come from ``script`` in order (then
    the queued ``updates``); sendMessage succeeds unless the chat is in ``fail_chats``."""

    def __init__(self, script: list[Step] | None = None) -> None:
        self.script = list(script or [])
        self.updates: list[dict[str, Any]] = []
        self.fail_chats: dict[int, int] = {}
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def __call__(self, url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, json, timeout))
        if method == "getUpdates":
            if self.script:
                step = self.script.pop(0)
                if isinstance(step, BaseException):
                    raise step
                if callable(step):
                    return step()  # type: ignore[no-any-return]
                return step
            offset = json.get("offset", 0)
            return FakeResponse(
                body={"ok": True, "result": [u for u in self.updates if u["update_id"] >= offset]}
            )
        return FakeResponse(status=self.fail_chats.get(json["chat_id"], 200))

    def methods(self) -> list[str]:
        return [m for m, _, _ in self.calls]

    def sent(self) -> list[tuple[int, str]]:
        return [(p["chat_id"], p["text"]) for m, p, _ in self.calls if m == "sendMessage"]


def msg(uid: int, chat: int, text: str) -> dict[str, Any]:
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": "private"}, "text": text}}


def listen(path: Path, http: ScriptedHttp, loops: int, sleeps: list[float] | None = None) -> int:
    api = tb.TelegramApi(token=TOKEN, post=http)
    record = sleeps if sleeps is not None else []
    return tb.run_listen(
        path, api, poll_timeout=25, max_loops=loops, sleep=record.append, now=lambda: T0
    )


def test_listen_replies_and_restart_does_not_rehandle(tmp_path: Path) -> None:
    path = tmp_path / "subs.json"
    http = ScriptedHttp()
    http.updates = [msg(100, 7, "/subscribe Bedok")]
    assert listen(path, http, loops=2) == 0
    assert subs.JsonSubscriptionStore(path).areas_for(7) == ["BEDOK"]
    assert [c for c, _ in http.sent()] == [7]
    first, second = (p for m, p, _ in http.calls if m == "getUpdates")
    assert first["timeout"] == 25 and "offset" not in first and second["offset"] == 101
    assert all(t >= 35 for m, _, t in http.calls if m == "getUpdates")  # HTTP > long poll
    # restart: new store from the same file; update 100 is not handled again
    http.calls.clear()
    assert listen(path, http, loops=1) == 0
    assert http.sent() == []
    assert [p.get("offset") for m, p, _ in http.calls if m == "getUpdates"] == [101]


def test_listen_saves_before_replying(tmp_path: Path) -> None:
    path = tmp_path / "subs.json"
    http = ScriptedHttp()
    http.updates = [msg(5, 1, "/subscribe Bedok")]
    seen: list[int | None] = []
    inner = http.__call__

    def post(url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        if url.endswith("/sendMessage"):
            seen.append(subs.JsonSubscriptionStore(path).last_update_id())
        return inner(url, json, timeout)

    tb.run_listen(path, tb.TelegramApi(token=TOKEN, post=post), max_loops=1, sleep=lambda _s: None)
    assert seen == [5]


def test_listen_429_waits_retry_after(tmp_path: Path) -> None:
    slow = FakeResponse(429, {"ok": False, "parameters": {"retry_after": 17}})
    http = ScriptedHttp([slow])
    sleeps: list[float] = []
    assert listen(tmp_path / "s.json", http, loops=2, sleeps=sleeps) == 1
    assert sleeps == [17.0]


def test_listen_429_without_retry_after_backs_off(tmp_path: Path) -> None:
    http = ScriptedHttp([FakeResponse(429, {"ok": False})])
    sleeps: list[float] = []
    listen(tmp_path / "s.json", http, loops=1, sleeps=sleeps)
    assert sleeps == [1.0]


def test_listen_network_errors_back_off_exponentially_cap_and_reset(tmp_path: Path) -> None:
    boom = tb.requests.ConnectionError
    script: list[Step] = [boom() for _ in range(8)]
    script += [FakeResponse(), FakeResponse(502)]  # success resets the delay
    http = ScriptedHttp(script)
    sleeps: list[float] = []
    assert listen(tmp_path / "s.json", http, loops=10, sleeps=sleeps) == 9
    assert sleeps == [1, 2, 4, 8, 16, 32, 60, 60, 1]


def test_listen_409_is_logged_and_backs_off(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    http = ScriptedHttp([FakeResponse(409), FakeResponse(409)])
    sleeps: list[float] = []
    with caplog.at_level(logging.ERROR, logger="FloodSense.TelegramBot"):
        listen(tmp_path / "s.json", http, loops=2, sleeps=sleeps)
    assert sleeps == [1, 2]
    assert "409" in caplog.text and "only one listener" in caplog.text


def test_listen_errors_never_log_the_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    url_err = tb.requests.ConnectionError(f"failed https://api.telegram.org/bot{TOKEN}/getUpdates")
    leak = FakeResponse(500, {"ok": False, "description": TOKEN})
    http = ScriptedHttp([url_err, leak, FakeResponse(429, {"description": TOKEN})])
    with caplog.at_level(logging.DEBUG):
        listen(tmp_path / "s.json", http, loops=3)
    assert "SECRET" not in caplog.text
    assert "ConnectionError" in caplog.text and "500" in caplog.text


def test_listen_stops_cleanly_on_ctrl_c(tmp_path: Path) -> None:
    http = ScriptedHttp([KeyboardInterrupt()])
    assert listen(tmp_path / "s.json", http, loops=5) == 0


def test_listen_cli_exits_0_on_ctrl_c(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.delenv("DATABRICKS_RUNTIME_VERSION", raising=False)
    monkeypatch.setattr(tb.requests, "post", ScriptedHttp([KeyboardInterrupt()]))
    assert tb.main(["listen", "--subscriptions-file", str(tmp_path / "s.json")]) == 0


def test_listen_save_failure_refetches_instead_of_skipping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "subs.json"
    http = ScriptedHttp()
    http.updates = [msg(10, 1, "/subscribe Bedok")]
    real_save = subs.JsonSubscriptionStore.save
    fails = iter([True, False])

    def flaky(self: subs.JsonSubscriptionStore) -> None:
        if next(fails, False):
            raise OSError("disk full")
        real_save(self)

    monkeypatch.setattr(subs.JsonSubscriptionStore, "save", flaky)
    sleeps: list[float] = []
    assert listen(path, http, loops=2, sleeps=sleeps) == 1
    offsets = [p.get("offset") for m, p, _ in http.calls if m == "getUpdates"]
    assert offsets == [None, None]  # the same update is fetched again, not skipped
    assert len(http.sent()) == 1 and sleeps == [1.0]
    assert subs.JsonSubscriptionStore(path).areas_for(1) == ["BEDOK"]


def test_listen_save_failure_keeps_subscriptions_when_the_file_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing volume must not load as empty and then overwrite every subscription."""
    path = tmp_path / "subs.json"
    _store_with(path, (5, "BEDOK"))
    http = ScriptedHttp()
    http.updates = [msg(10, 1, "/subscribe Tuas")]
    real_save = subs.JsonSubscriptionStore.save
    fails = iter([True, False])

    def flaky(self: subs.JsonSubscriptionStore) -> None:
        if next(fails, False):
            path.write_text("{broken")  # the file reads as garbage while the volume is failing
            path.with_name(path.name + ".bak").write_text("{broken")
            raise OSError("volume error")
        real_save(self)

    monkeypatch.setattr(subs.JsonSubscriptionStore, "save", flaky)
    assert listen(path, http, loops=2) == 1
    store = subs.JsonSubscriptionStore(path)
    assert store.areas_for(5) == ["BEDOK"] and store.areas_for(1) == ["TUAS"]


def test_listen_skips_a_batch_that_keeps_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "subs.json"
    http = ScriptedHttp()
    http.updates = [msg(10, 1, "/subscribe Bedok")]

    def boom(*_: object, **__: object) -> str:
        raise TypeError("odd update")

    monkeypatch.setattr(subs, "handle_message", boom)
    sleeps: list[float] = []
    assert listen(path, http, loops=4, sleeps=sleeps) == tb.MAX_BATCH_FAILURES
    offsets = [p.get("offset") for m, p, _ in http.calls if m == "getUpdates"]
    assert offsets == [None] * tb.MAX_BATCH_FAILURES + [11]  # skipped after the third failure
    assert http.sent() == [] and sleeps == [1.0, 1.0, 1.0]  # each poll succeeds, so no growth


# --- handoff file -------------------------------------------------------------------------------


def _store_with(path: Path, *rows: tuple[int, str]) -> None:
    store = subs.JsonSubscriptionStore(path)
    for chat, area in rows:
        store.add(chat, area, T0)
    store.save()


def test_listen_applies_blocked_file_then_clears_it(tmp_path: Path) -> None:
    path = tmp_path / "subs.json"
    _store_with(path, (1, "BEDOK"), (1, "TANGLIN"), (2, "BEDOK"))
    blocked = tb.blocked_file_for(path)
    assert blocked == tmp_path / "telegram_blocked.json"
    tb.append_blocked_chats(blocked, [1])
    listen(path, ScriptedHttp(), loops=1)
    store = subs.JsonSubscriptionStore(path)
    assert store.areas_for(1) == [] and store.areas_for(2) == ["BEDOK"]
    assert not blocked.exists() and not list(tmp_path.glob("telegram_blocked*"))


def test_blocked_file_kept_when_store_save_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "subs.json"
    _store_with(path, (1, "BEDOK"))
    blocked = tb.blocked_file_for(path)
    tb.append_blocked_chats(blocked, [1])
    store = subs.JsonSubscriptionStore(path)

    def broken() -> None:
        raise OSError("read-only")

    monkeypatch.setattr(store, "save", broken)
    assert tb.apply_blocked_chats(store, blocked) == 1
    claimed = blocked.with_name(blocked.name + ".claimed")
    assert claimed.exists()  # not lost: applied on the next loop
    # meanwhile the alert job adds another chat; the claimed file is finished first
    tb.append_blocked_chats(blocked, [2])
    monkeypatch.undo()
    store2 = subs.JsonSubscriptionStore(path)
    assert tb.apply_blocked_chats(store2, blocked) == 0
    assert subs.JsonSubscriptionStore(path).areas_for(1) == []
    assert not claimed.exists() and json.loads(blocked.read_text())["chat_ids"] == [2]


def test_corrupt_blocked_file_is_set_aside_and_listen_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "subs.json"
    _store_with(path, (1, "BEDOK"))
    blocked = tb.blocked_file_for(path)
    blocked.write_text("{not json")
    http = ScriptedHttp()
    http.updates = [msg(1, 3, "/list")]
    with caplog.at_level(logging.ERROR, logger="FloodSense.TelegramBot"):
        assert listen(path, http, loops=2) == 1
    assert "unreadable" in caplog.text
    (kept,) = tmp_path.glob("telegram_blocked.json.*.corrupt")
    assert kept.read_text() == "{not json"
    assert not blocked.exists() and [c for c, _ in http.sent()] == [3]
    assert subs.JsonSubscriptionStore(path).areas_for(1) == ["BEDOK"]


def test_append_blocked_dedupes_and_replaces_a_corrupt_file(tmp_path: Path) -> None:
    blocked = tmp_path / "telegram_blocked.json"
    tb.append_blocked_chats(blocked, [5, 3])
    tb.append_blocked_chats(blocked, [3, 9])
    assert json.loads(blocked.read_text())["chat_ids"] == [3, 5, 9]
    blocked.write_text("[]")  # wrong shape
    tb.append_blocked_chats(blocked, [4])
    assert json.loads(blocked.read_text())["chat_ids"] == [4]
    assert list(tmp_path.glob("telegram_blocked.json.*.corrupt"))
    assert not list(tmp_path.glob("*.tmp"))


# --- live --alerts-only -------------------------------------------------------------------------


def test_alerts_only_never_reads_messages_or_writes_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "subs.json"
    _store_with(path, (1, "BEDOK"), (2, "BEDOK"), (2, "TANGLIN"))
    before = path.read_bytes()
    http = ScriptedHttp()
    http.fail_chats = {2: 403}
    channel: list[str] = []
    emit = tb.alerts_only_emitter(
        lambda a: channel.append(a.zone), path, tb.TelegramApi(token=TOKEN, post=http)
    )
    problems = tb.run_live(
        lambda: (T0, {"BEDOK": "High", "TANGLIN": "Low"}),
        emit,
        max_polls=2,
        sleep=lambda _s: None,
    )
    assert problems == 0 and channel == ["BEDOK"]
    assert "getUpdates" not in http.methods()
    assert sorted(c for c, _ in http.sent()) == [1, 2]
    assert path.read_bytes() == before  # byte for byte
    assert json.loads(tb.blocked_file_for(path).read_text())["chat_ids"] == [2]


def test_alerts_only_reads_the_listeners_latest_subscriptions(tmp_path: Path) -> None:
    path = tmp_path / "subs.json"
    http = ScriptedHttp()
    emit = tb.alerts_only_emitter(lambda a: None, path, tb.TelegramApi(token=TOKEN, post=http))
    _store_with(path, (8, "BEDOK"))  # written after the emitter was made
    emit(tb.Alert("BEDOK", "High", "Low", T0, "text"))
    assert http.sent() == [(8, "text")]


def test_live_cli_alerts_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "subs.json"
    _store_with(path, (1, "BEDOK"))
    before = path.read_bytes()
    http = ScriptedHttp()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.delenv("DATABRICKS_RUNTIME_VERSION", raising=False)
    monkeypatch.setattr(tb.requests, "post", http)
    monkeypatch.setattr(tb, "gold_latest_risk", lambda: (datetime.now(SGT), {"BEDOK": "High"}))
    args = ["live", "--once", "--alerts-only", "--subscriptions-file", str(path)]
    assert tb.main([*args, "--state-file", str(tmp_path / "state.json")]) == 0
    assert "getUpdates" not in http.methods()
    assert sorted(str(c) for c, _ in http.sent()) == ["-100", "1"]  # channel + subscriber
    assert path.read_bytes() == before
    with pytest.raises(SystemExit):
        tb.main(["live", "--once", "--alerts-only"])


# --- /status ------------------------------------------------------------------------------------


def test_listen_answers_status_from_the_state_file(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    det = tb.AlertDetector()
    det.record_latest(T0, {"BEDOK": "High", "TUAS": "No data"})
    tb.save_detector_state(det, state)
    http = ScriptedHttp()
    http.updates = [msg(100, 7, "/status bedok"), msg(101, 7, "/status tuas")]
    api = tb.TelegramApi(token=TOKEN, post=http)
    tb.run_listen(
        tmp_path / "subs.json",
        api,
        state_path=state,
        max_loops=1,
        sleep=lambda _: None,
        now=lambda: T0,
    )
    assert [t for _, t in http.sent()] == [
        "Flood risk as of 12:00 SGT:\nBedok: High",
        "Flood risk as of 12:00 SGT:\nTuas: no reading (too few rain gauges reporting)",
    ]


def test_listen_status_without_a_state_file_says_no_recent_reading(tmp_path: Path) -> None:
    http = ScriptedHttp()
    http.updates = [msg(100, 7, "/status bedok")]
    api = tb.TelegramApi(token=TOKEN, post=http)
    tb.run_listen(
        tmp_path / "subs.json",
        api,
        state_path=tmp_path / "missing.json",
        max_loops=1,
        sleep=lambda _: None,
        now=lambda: T0,
    )
    assert http.sent() == [(7, "No recent reading. The risk feed may be paused.")]


def test_latest_is_saved_by_run_cycle_with_no_data_and_round_trips(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    det = tb.AlertDetector()
    tiers = {"BEDOK": "Low", "TUAS": "No data"}
    assert tb.run_cycle(det, lambda: (T0, tiers), lambda a: None, state_path=state) == 0
    assert tb.read_latest(state) == (T0, tiers)
    fresh = tb.AlertDetector()
    tb.load_detector_state(fresh, state)
    assert fresh.to_state()["latest"] == det.to_state()["latest"]
    assert fresh.to_state()["last_tier"] == {"BEDOK": "Low"}  # alert state skips "No data"


def test_latest_not_recorded_when_empty_or_too_old(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    det = tb.AlertDetector()
    tb.run_cycle(det, lambda: (T0, {}), lambda a: None, state_path=state)
    tb.run_cycle(
        det,
        lambda: (T0, {"BEDOK": "Low"}),
        lambda a: None,
        state_path=state,
        max_age=timedelta(minutes=30),
        now=lambda: T0 + timedelta(hours=2),
    )
    assert tb.read_latest(state) is None


def test_old_state_without_latest_still_loads(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    old = tb.AlertDetector().to_state()
    del old["latest"]
    state.write_text(json.dumps(old))
    det = tb.AlertDetector()
    tb.load_detector_state(det, state)
    assert det.to_state()["latest"] is None
    assert tb.read_latest(state) is None


def test_read_latest_never_raises(tmp_path: Path) -> None:
    assert tb.read_latest(None) is None
    assert tb.read_latest(tmp_path / "nope.json") is None
    for text in ("{not json", "[]", '{"latest": 5}', '{"latest": {"as_of": "x", "tiers": {}}}'):
        bad = tmp_path / "bad.json"
        bad.write_text(text)
        assert tb.read_latest(bad) is None
