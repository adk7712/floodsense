"""Offline tests for the Telegram alert bot: no network, no token needed."""

from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pytest

from floodsense.alerts import telegram_bot as tb

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
    assert "underpasses" in tb.ADVICE["High"] and "PUB" in tb.ADVICE["Moderate"]


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


def test_live_stub_raises_clearly_and_main_reports_it(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(NotImplementedError):
        tb.databricks_latest_risk()
    assert tb.main(["live", "--dry-run"]) == 1
    assert "No live risk provider" in capsys.readouterr().err


def test_run_live_with_fake_provider_ignores_repeat_polls() -> None:
    feed = iter([(at(0), {"A": "Low"}), (at(5), {"A": "High"}), (at(5), {"A": "High"})])
    got: list[tb.Alert] = []
    tb.run_live(lambda: next(feed), got.append, max_polls=3, sleep=lambda _s: None)
    assert [a.tier for a in got] == ["High"]


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
