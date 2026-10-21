"""Offline tests for the Telegram alert bot: no network, no token needed."""

import json
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from floodsense.alerts import telegram_bot as tb
from floodsense.alerts.messages import HONESTY_LINE
from floodsense.common.config import settings
from floodsense.data.replay import load_replay
from floodsense.models.scoring import NO_DATA_TIER
from floodsense.serving import pipeline_core as core
from floodsense.serving.coverage import withhold_tier
from floodsense.serving.gold import GoldUnavailable
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

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


def test_run_cycle_survives_unknown_tier_but_accepts_no_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    got: list[tb.Alert] = []
    with caplog.at_level(logging.ERROR, logger="FloodSense.TelegramBot"):
        bad = tb.run_cycle(tb.AlertDetector(), lambda: (at(0), {"A": "Severe"}), got.append)
    assert bad == 1 and got == [] and "Bad risk data" in caplog.text
    ok = tb.run_cycle(tb.AlertDetector(), lambda: (at(0), {"A": NO_DATA_TIER}), got.append)
    assert ok == 0 and got == []


def test_no_data_step_keeps_tier_and_sends_nothing() -> None:
    snaps = [
        (at(0), {"A": "High"}),
        (at(5), {"A": NO_DATA_TIER}),
        (at(10), {"A": "High"}),  # still High: not a new rise
        (at(15), {"A": NO_DATA_TIER}),
        (at(20), {"A": "Low"}),
    ]
    alerts = list(tb.detect_alerts(snaps, include_all_clear=True))
    # the gap makes the next hour partial, so the all-clear at 13:20 waits: only the High is sent
    assert [(a.timestamp, a.tier) for a in alerts] == [(at(0), "High")]
    det = tb.AlertDetector(include_all_clear=True)
    det.feed(at(0), {"A": "High"})
    assert det.feed(at(5), {"A": NO_DATA_TIER}) == []
    assert det.to_state()["last_tier"] == {"A": "High"}
    # a zone first seen with no data stays unknown, not Low
    assert det.feed(at(5), {"B": NO_DATA_TIER}) == []
    assert "B" not in det.to_state()["last_tier"]


def test_partial_step_may_raise_but_never_sends_all_clear_or_downgrades() -> None:
    det = tb.AlertDetector(include_all_clear=True)
    assert [a.tier for a in det.feed(at(0), {"A": "Moderate"}, {"A": "ok"})] == ["Moderate"]
    assert det.feed(at(5), {"A": "Low"}, {"A": "partial"}) == []
    assert det.to_state()["last_tier"] == {"A": "Moderate"}  # not lowered
    assert [a.tier for a in det.feed(at(10), {"A": "High"}, {"A": "partial"})] == ["High"]
    assert [a.tier for a in det.feed(at(15), {"A": "Low"}, {"A": "ok"})] == ["Low"]


def test_live_gap_makes_the_next_hour_partial_without_explicit_coverage() -> None:
    det = tb.AlertDetector(include_all_clear=True)
    det.feed(at(0), {"A": "Moderate"})
    det.feed(at(5), {"A": NO_DATA_TIER})
    assert det.feed(at(10), {"A": "Low"}) == []  # inside 60 min of the gap: partial
    assert [a.tier for a in det.feed(at(66), {"A": "Low"})] == ["Low"]


def test_naive_saved_time_is_read_as_sgt() -> None:
    det = tb.AlertDetector()
    det.feed(at(0), {"Z": "High"})
    state = json.loads(json.dumps(det.to_state()))
    state["last_sent"]["Z"] = "2021-04-17T12:00:00"  # naive, as an older save might hold
    fresh = tb.AlertDetector()
    fresh.load_state(state)
    assert fresh.feed(at(5), {"Z": "Low"}) == []  # no TypeError
    assert fresh.feed(at(10), {"Z": "Moderate"}) == []  # inside the hour of the naive 12:00 send
    assert [a.tier for a in fresh.feed(at(61), {"Z": "High"})] == ["High"]
    assert "12:00" in tb.format_message("Z", "High", datetime(2021, 4, 17, 4, 0, tzinfo=UTC))


def test_suppressed_rise_is_resent_after_the_hour_if_still_elevated() -> None:
    snaps = [(at(0), {"A": "Moderate"}), (at(10), {"A": "Low"}), (at(20), {"A": "Moderate"})]
    snaps += [(at(m), {"A": "Moderate"}) for m in range(25, 200, 5)]
    alerts = list(tb.detect_alerts(snaps))
    assert [(a.timestamp, a.tier) for a in alerts] == [(at(0), "Moderate"), (at(60), "Moderate")]
    # a zone that fell back to Low before the hour was up is forgotten
    gone = [(at(0), {"A": "Moderate"}), (at(10), {"A": "Low"}), (at(20), {"A": "Moderate"})]
    gone += [(at(25), {"A": "Low"}), *[(at(m), {"A": "Low"}) for m in range(60, 100, 5)]]
    assert [a.timestamp for a in tb.detect_alerts(gone)] == [at(0)]
    # and a restart keeps the pending rise
    det = tb.AlertDetector()
    det.feed(at(0), {"A": "Moderate"})
    det.feed(at(10), {"A": "Low"})
    det.feed(at(20), {"A": "Moderate"})
    fresh = tb.AlertDetector()
    fresh.load_state(json.loads(json.dumps(det.to_state())))
    assert [a.timestamp for a in fresh.feed(at(61), {"A": "Moderate"})] == [at(61)]


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


def _gold_frame(rows: list[tuple[str, datetime, str]], stations: int = 50) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ura_planning_area", "timestamp", "risk_tier"])
    df["reporting_stations"] = stations
    return df


def _all_zones(ts: datetime, tier: str = "Low") -> list[tuple[str, datetime, str]]:
    return [(z, ts, tier) for z in URA_PLANNING_AREAS]


def test_gold_provider_uses_the_step_when_all_zones_share_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    df = _gold_frame(_all_zones(at(5), "High"))
    monkeypatch.setattr(tb, "latest_zone_risk", lambda: df)
    ts, tiers = tb.gold_latest_risk()
    assert ts == at(5) and len(tiers) == len(URA_PLANNING_AREAS)
    assert set(tiers.values()) == {"High"}


def test_gold_provider_returns_nothing_when_zones_are_at_different_steps(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    df = _gold_frame([*_all_zones(at(0))[:30], *_all_zones(at(5), "High")[30:]])
    monkeypatch.setattr(tb, "latest_zone_risk", lambda: df)
    with caplog.at_level(logging.WARNING, logger="FloodSense.TelegramBot"):
        ts, tiers = tb.gold_latest_risk()
    assert ts == at(5) and dict(tiers) == {}
    assert "nothing sent" in caplog.text


def test_gold_provider_marks_thin_gauge_coverage_as_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    df = _gold_frame(_all_zones(at(0), "High"), stations=3)
    monkeypatch.setattr(tb, "latest_zone_risk", lambda: df)
    assert set(tb.gold_latest_risk()[1].values()) == {NO_DATA_TIER}


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


# --- the 17 Apr 2021 storm with a gauge outage (stress-test H1) ---------------------------------


def _bukit_timah_steps(drop_outage: bool) -> list[tb.Snapshot]:
    """Scored BUKIT TIMAH steps for 12:00-14:00, with coverage; optionally 12:45-13:00 removed."""
    replay = load_replay(settings.replay_file)
    readings, stations = core.payloads_to_readings(
        core.snapshot_to_payload(x) for x in replay.snapshots
    )
    peak = pd.Timestamp("2021-04-17 13:00", tz="Asia/Singapore")
    if drop_outage:
        gone = {peak - pd.Timedelta(minutes=5 * k) for k in range(4)}
        readings = readings[~readings["timestamp"].isin(gone)]
    out = core.score_window(
        readings,
        stations,
        emit_from=peak - pd.Timedelta(minutes=60),
        emit_to=peak + pd.Timedelta(minutes=60),
    )
    zone = withhold_tier(out).query("ura_planning_area == 'BUKIT TIMAH'")
    return [
        (t.to_pydatetime(), {"BUKIT TIMAH": tier}, {"BUKIT TIMAH": cov})
        for t, tier, cov in zip(zone["timestamp"], zone["risk_tier"], zone["coverage"], strict=True)
    ]


def _summary(alerts: list[tb.Alert]) -> list[tuple[str, str, str]]:
    return [(f"{a.timestamp:%H:%M}", a.previous, a.tier) for a in alerts]


def test_replay_complete_data_sequence_is_unchanged() -> None:
    alerts = list(tb.detect_alerts(_bukit_timah_steps(False), include_all_clear=True))
    assert _summary(alerts) == [
        ("12:35", "Low", "Moderate"),
        ("12:45", "Moderate", "High"),
        ("13:45", "Moderate", "Low"),
    ]


def test_replay_outage_sends_no_all_clear_while_partial_and_high_once_complete() -> None:
    steps = _bukit_timah_steps(True)
    by_time = {f"{t:%H:%M}": (tiers, cov) for t, tiers, cov in steps}  # type: ignore[misc]
    assert by_time["12:50"][0] == {"BUKIT TIMAH": NO_DATA_TIER}
    assert by_time["13:10"][1] == {"BUKIT TIMAH": "partial"}
    assert by_time["13:10"][0] == {"BUKIT TIMAH": "Low"}  # the understated rain the old bot trusted
    det = tb.AlertDetector(include_all_clear=True)
    alerts: list[tb.Alert] = []
    for snap in steps:
        if snap[0] > at(120):  # up to 14:00 SGT
            break
        got = det.feed(*snap)
        if snap[2]["BUKIT TIMAH"] != "ok":
            assert all(a.tier != "Low" for a in got)  # no all-clear on gappy data
        alerts += got
    assert _summary(alerts)[0] == ("12:35", "Low", "Moderate")
    assert not any(a.timestamp == at(70) and a.tier == "Low" for a in alerts)  # 13:10
    # complete data now shows High: the bot sends it
    late = det.feed(at(125), {"BUKIT TIMAH": "High"}, {"BUKIT TIMAH": "ok"})
    assert [a.tier for a in late] == ["High"]


def test_replay_snapshots_withhold_gap_steps_and_carry_coverage() -> None:
    replay = load_replay(settings.replay_file)
    gone = {replay.display_start + pd.Timedelta(minutes=5 * k) for k in range(40, 52)}
    snaps = [x for x in replay.snapshots if pd.Timestamp(x.timestamp) not in gone]
    table = tb.compute_zone_feature_table(snaps, replay.stations)
    table["coverage"] = tb.coverage(table)
    stamps = pd.to_datetime(table["timestamp"])
    table = table[(stamps >= replay.display_start) & (stamps <= replay.display_end)]
    steps = tb._score_snapshots(table, tb.load_model())
    assert all(len(x) == 3 for x in steps)
    gap = [x for x in steps if set(x[1].values()) == {NO_DATA_TIER}]
    assert gap and all(set(x[2].values()) == {"none"} for x in gap)
    assert any(set(x[2].values()) == {"partial"} for x in steps)
    alerts = list(tb.detect_alerts(steps, include_all_clear=True))
    gap_times = {x[0] for x in gap}
    assert not any(a.tier == "Low" and a.timestamp in gap_times for a in alerts)
    # no all-clear is sent on a step flagged partial
    partial_times = {x[0] for x in steps if "partial" in x[2].values()}
    assert not any(a.tier == "Low" and a.timestamp in partial_times for a in alerts)
