"""Offline tests for the Telegram alert bot: no network, no token needed."""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from floodsense.alerts import telegram_bot as tb
from floodsense.alerts.messages import HONESTY_LINE
from floodsense.serving.gold import GoldUnavailable

SGT = timezone(timedelta(hours=8))
T0 = datetime(2021, 4, 17, 12, 0, tzinfo=SGT)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_rise_to_moderate_and_high_detected() -> None:
    snaps = [
        (at(0), {"BEDOK": "Low", "TANGLIN": "Low"}),
        (at(5), {"BEDOK": "Moderate", "TANGLIN": "Low"}),
        (at(10), {"BEDOK": "Moderate", "TANGLIN": "High"}),
    ]
    alerts = list(tb.detect_alerts(snaps))
    assert [(a.zone, a.tier, a.previous) for a in alerts] == [
        ("BEDOK", "Moderate", "Low"),
        ("TANGLIN", "High", "Low"),
    ]


def test_no_alert_when_steady_falling_or_low() -> None:
    snaps = [
        (at(0), {"A": "Low"}),
        (at(5), {"A": "Low"}),
        (at(10), {"A": "Moderate"}),
        (at(15), {"A": "Moderate"}),
        (at(20), {"A": "Low"}),
    ]
    assert [a.tier for a in tb.detect_alerts(snaps)] == ["Moderate"]


def test_first_snapshot_already_elevated_alerts() -> None:
    assert [a.tier for a in tb.detect_alerts([(at(0), {"A": "High"})])] == ["High"]


def test_no_repeat_within_the_hour() -> None:
    snaps = [
        (at(0), {"A": "Moderate"}),
        (at(5), {"A": "Low"}),
        (at(10), {"A": "Moderate"}),  # rises again inside the hour: dropped
        (at(59), {"A": "Low"}),
        (at(60 - 1), {"A": "Moderate"}),  # still inside the hour
    ]
    assert len(list(tb.detect_alerts(snaps))) == 1


def test_second_rise_after_an_hour_posts_again() -> None:
    snaps = [
        (at(0), {"A": "Moderate"}),
        (at(5), {"A": "Low"}),
        (at(60), {"A": "Moderate"}),
    ]
    assert [a.timestamp for a in tb.detect_alerts(snaps)] == [at(0), at(60)]


def test_rate_limit_is_per_zone() -> None:
    snaps = [(at(0), {"A": "Moderate"}), (at(5), {"A": "Moderate", "B": "Moderate"})]
    assert [a.zone for a in tb.detect_alerts(snaps)] == ["A", "B"]


def test_escalation_to_high_bypasses_limit_by_default_and_strict_mode_drops_it() -> None:
    snaps = [(at(0), {"A": "Moderate"}), (at(10), {"A": "High"})]
    assert [a.tier for a in tb.detect_alerts(snaps)] == ["Moderate", "High"]
    assert [a.tier for a in tb.detect_alerts(snaps, allow_escalation=False)] == ["Moderate"]
    # a second High inside the hour is still limited
    more = [*snaps, (at(15), {"A": "Moderate"}), (at(20), {"A": "High"})]
    assert [a.tier for a in tb.detect_alerts(more)] == ["Moderate", "High"]


def test_all_clear_is_optional() -> None:
    snaps = [(at(0), {"A": "Moderate"}), (at(5), {"A": "Low"})]
    assert [a.tier for a in tb.detect_alerts(snaps)] == ["Moderate"]
    clear = list(tb.detect_alerts(snaps, include_all_clear=True))
    assert [a.tier for a in clear] == ["Moderate", "Low"]
    assert "all-clear" in clear[1].text and "Low" in clear[1].text


def test_unknown_tier_rejected() -> None:
    with pytest.raises(ValueError):
        list(tb.detect_alerts([(at(0), {"A": "Severe"})]))


@pytest.mark.parametrize("tier", ["Moderate", "High"])
def test_message_has_zone_tier_time_advice_and_disclaimer(tier: str) -> None:
    text = tb.format_message("BUKIT MERAH", tier, datetime(2021, 4, 17, 13, 5, tzinfo=SGT))
    assert "Bukit Merah" in text
    assert tier.upper() in text
    assert "13:05" in text and "17 Apr 2021" in text and "SGT" in text
    assert tb.ADVICE[tier] in text
    assert "complements, and does not replace, PUB" in text
    assert HONESTY_LINE in text
    assert "underpasses" in tb.ADVICE["High"] and "PUB" in tb.ADVICE["Moderate"]


def test_all_clear_message_has_honesty_line() -> None:
    text = tb.format_message("BEDOK", "Low", datetime(2021, 4, 17, 13, 5, tzinfo=SGT))
    assert "all-clear" in text
    assert HONESTY_LINE in text
    assert HONESTY_LINE == (
        "FloodSense is not an official warning; alerts can be late or missed; follow PUB."
    )


def test_sender_posts_to_bot_api_without_network(caplog: pytest.LogCaptureFixture) -> None:
    ok = mock.Mock(status_code=200)
    with mock.patch.object(tb.requests, "post", return_value=ok) as post:
        tb.TelegramSender(token="SECRET:TOKEN", chat_id="-100123").send("hello")
    url = post.call_args.args[0]
    assert url == "https://api.telegram.org/botSECRET:TOKEN/sendMessage"
    assert post.call_args.kwargs["json"] == {"chat_id": "-100123", "text": "hello"}
    assert "SECRET" not in caplog.text


def test_sender_errors_never_leak_token() -> None:
    sender = tb.TelegramSender(token="SECRET:TOKEN", chat_id="1")
    boom = tb.requests.ConnectionError("failed for url https://api.telegram.org/botSECRET:TOKEN/x")
    with (
        mock.patch.object(tb.requests, "post", side_effect=boom),
        pytest.raises(RuntimeError) as err,
    ):
        sender.send("x")
    assert "SECRET" not in str(err.value) and "SECRET" not in repr(err.value.__cause__)
    denied = mock.Mock(status_code=401)
    with (
        mock.patch.object(tb.requests, "post", return_value=denied),
        pytest.raises(RuntimeError) as err2,
    ):
        sender.send("x")
    assert "SECRET" not in str(err2.value) and "401" in str(err2.value)


def test_sender_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        tb.TelegramSender()


def test_run_live_with_fake_provider_ignores_repeat_polls() -> None:
    feed = iter([(at(0), {"A": "Low"}), (at(5), {"A": "High"}), (at(5), {"A": "High"})])
    got: list[tb.Alert] = []
    tb.run_live(lambda: next(feed), got.append, max_polls=3, sleep=lambda _s: None)
    assert [a.tier for a in got] == ["High"]


def _gold_frame(rows: list[tuple[str, datetime, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ura_planning_area", "timestamp", "risk_tier"])


def test_gold_provider_uses_newest_step_and_skips_older_zones(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    df = _gold_frame(
        [("BEDOK", at(5), "High"), ("TANGLIN", at(5), "Low"), ("CLEMENTI", at(0), "High")]
    )
    monkeypatch.setattr(tb, "latest_zone_risk", lambda: df)
    with caplog.at_level(logging.WARNING, logger="FloodSense.TelegramBot"):
        ts, tiers = tb.gold_latest_risk()
    assert ts == at(5)
    assert dict(tiers) == {"BEDOK": "High", "TANGLIN": "Low"}  # CLEMENTI never invented
    assert "skipped" in caplog.text


def _raise_gold() -> pd.DataFrame:
    raise GoldUnavailable("no warehouse")


def test_gold_unavailable_skips_cycle_and_loop_survives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = iter([None, (at(5), {"A": "High"})])

    def provider() -> tuple[datetime, dict[str, str]]:
        item = next(calls)
        if item is None:
            raise GoldUnavailable("down")
        return item

    got: list[tb.Alert] = []
    with caplog.at_level(logging.ERROR, logger="FloodSense.TelegramBot"):
        problems = tb.run_live(provider, got.append, max_polls=2, sleep=lambda _s: None)
    assert problems == 1 and [a.tier for a in got] == ["High"]
    assert "skipping this cycle" in caplog.text


def test_once_returns_1_when_gold_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tb, "latest_zone_risk", _raise_gold)
    assert tb.main(["live", "--once", "--dry-run"]) == 1


def test_send_failure_is_logged_not_marked_sent_and_retried(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    sender = tb.TelegramSender(token="SECRET:TOKEN", chat_id="1")
    state = tmp_path / "s.json"
    boom = tb.requests.ConnectionError("https://api.telegram.org/botSECRET:TOKEN/x")
    feed = iter([(at(0), {"A": "Moderate"}), (at(5), {"A": "Moderate"})])
    sent: list[str] = []

    def emit(alert: tb.Alert) -> None:
        sender.send(alert.text)
        sent.append(alert.zone)

    with caplog.at_level(logging.INFO, logger="FloodSense.TelegramBot"):
        with mock.patch.object(tb.requests, "post", side_effect=boom):
            p1 = tb.run_live(lambda: next(feed), emit, max_polls=1, state_path=state)
        assert p1 == 1 and not sent
        saved = json.loads(state.read_text())
        assert saved["last_sent"] == {} and saved["last_tier"] == {}  # not marked sent
        with mock.patch.object(tb.requests, "post", return_value=mock.Mock(status_code=200)):
            p2 = tb.run_live(lambda: next(feed), emit, max_polls=1, state_path=state)
    assert p2 == 0 and sent == ["A"]  # retried on the next cycle
    assert "SECRET" not in caplog.text and "ConnectionError" in caplog.text
    assert "Send failed" in caplog.text


def test_http_failure_logs_status_only(caplog: pytest.LogCaptureFixture) -> None:
    sender = tb.TelegramSender(token="SECRET:TOKEN", chat_id="1")
    with (
        caplog.at_level(logging.ERROR, logger="FloodSense.TelegramBot"),
        mock.patch.object(tb.requests, "post", return_value=mock.Mock(status_code=429)),
    ):
        tb.run_live(
            lambda: (at(0), {"A": "High"}),
            lambda a: sender.send(a.text),
            max_polls=1,
        )
    assert "429" in caplog.text and "SECRET" not in caplog.text


def test_state_round_trip(tmp_path: Path) -> None:
    det = tb.AlertDetector(include_all_clear=True)
    det.feed(at(0), {"A": "Moderate", "B": "High"})
    path = tmp_path / "sub" / "state.json"
    tb.save_detector_state(det, path)
    assert not list(path.parent.glob("*.tmp"))
    fresh = tb.AlertDetector(include_all_clear=True)
    tb.load_detector_state(fresh, path)
    assert fresh.to_state() == det.to_state()


def test_restart_with_saved_state_does_not_resend(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    snap = (at(0), {"A": "High", "B": "Moderate"})
    first: list[tb.Alert] = []
    tb.run_live(lambda: snap, first.append, max_polls=1, state_path=path)
    assert len(first) == 2
    again: list[tb.Alert] = []  # "restart": new detector, same file, 10 minutes later
    tb.run_live(lambda: (at(10), snap[1]), again.append, max_polls=1, state_path=path)
    assert again == []
    # escalation after restart is still allowed
    esc: list[tb.Alert] = []
    tb.run_live(lambda: (at(15), {"B": "High"}), esc.append, max_polls=1, state_path=path)
    assert [(a.zone, a.tier) for a in esc] == [("B", "High")]


def test_corrupt_or_missing_state_starts_fresh(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json")
    got: list[tb.Alert] = []
    with caplog.at_level(logging.WARNING, logger="FloodSense.TelegramBot"):
        tb.run_live(lambda: (at(0), {"A": "High"}), got.append, max_polls=1, state_path=path)
    assert "starting fresh" in caplog.text and len(got) == 1
    assert json.loads(path.read_text())["last_tier"] == {"A": "High"}  # rewritten, valid
    path.write_text(json.dumps({"last_tier": {"A": "Severe"}}))  # wrong shape
    det = tb.AlertDetector()
    tb.load_detector_state(det, path)
    assert det.to_state()["last_tier"] == {}
    tb.load_detector_state(det, tmp_path / "missing.json")  # no file: no error


def test_stale_gold_step_sends_nothing() -> None:
    got: list[tb.Alert] = []
    tb.run_live(
        lambda: (at(0), {"A": "High"}),
        got.append,
        max_polls=1,
        max_age=timedelta(minutes=30),
        now=lambda: at(45),
    )
    assert got == []


def test_databricks_secrets_fill_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_RUNTIME_VERSION", raising=False)
    tb.load_databricks_secrets()  # not on Databricks: nothing happens
    assert "TELEGRAM_BOT_TOKEN" not in tb.os.environ


def test_replay_dry_run_17_apr_2021_has_high_messages(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    snaps = tb.replay_snapshots(date(2021, 4, 17))
    highs = [a for a in tb.detect_alerts(snaps) if a.tier == "High"]
    assert highs
    assert all(a.timestamp.date() == date(2021, 4, 17) for a in highs)
    with mock.patch.object(tb.requests, "post") as post:
        assert tb.main(["replay", "--date", "2021-04-17", "--dry-run"]) == 0
    post.assert_not_called()
    out = capsys.readouterr().out
    assert "HIGH flood risk" in out and "SGT" in out
