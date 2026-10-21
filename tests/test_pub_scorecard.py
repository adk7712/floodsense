"""Offline tests for the PUB live scorecard, on small synthetic alerts and score histories."""

import json
import math
from datetime import date, datetime, timedelta

import pandas as pd

from floodsense.common.config import settings
from floodsense.ingestion.flood_alerts import FloodAlert, zone_for_point
from floodsense.monitoring.pub_scorecard import (
    SCORECARD_COLUMNS,
    build_scorecard,
    main,
    summarise,
    unique_alerts,
    zones_touched,
)

SGT = settings.tzinfo
LAT, LON = 1.3245, 103.8130  # inside BUKIT TIMAH (tests/test_flood_alerts.py)
ISSUED = datetime(2026, 10, 20, 17, 5, tzinfo=SGT)


def alert(identifier="A1", when=ISSUED, msg_type="Alert", lat=LAT, lon=LON, radius=0.5):
    zone = zone_for_point(lat, lon)
    return FloodAlert(
        identifier, msg_type, "", when, "Flash Flood", "Flash flood at Bt Timah Rd", "Bt Timah Rd",
        lat, lon, radius, "Minor", zone,
    )  # fmt: skip


def history(zone="BUKIT TIMAH", start=ISSUED - timedelta(minutes=90), tiers=()):
    """One row per 5 minutes from ``start``; ``tiers`` is (minutes before the alert, tier, prob)."""
    rows = []
    for minutes_before, tier, prob in tiers:
        rows.append((zone, ISSUED - timedelta(minutes=minutes_before), prob, tier))
    return pd.DataFrame(
        rows, columns=["ura_planning_area", "timestamp", "flood_probability", "risk_tier"]
    )


def test_warning_time_is_from_first_moderate_or_high_step():
    h = history(
        tiers=[(50, "Low", 0.001), (40, "Moderate", 0.006), (30, "High", 0.02), (5, "Moderate", 0.007)]
    )  # fmt: skip
    (row,) = build_scorecard([alert()], h).to_dict("records")
    assert row["status"] == "scored"
    assert row["max_tier"] == "High"
    assert row["max_probability"] == 0.02
    assert row["moderate_or_higher"] is True
    assert row["warning_minutes"] == 40.0
    assert row["steps_scored"] == 4


def test_low_only_has_no_warning():
    h = history(tiers=[(30, "Low", 0.001), (10, "Low", 0.002)])
    (row,) = build_scorecard([alert()], h).to_dict("records")
    assert row["max_tier"] == "Low"
    assert row["moderate_or_higher"] is False
    assert row["warning_minutes"] is None or math.isnan(row["warning_minutes"])


def test_only_the_hour_before_the_alert_in_the_alert_area_counts():
    h = pd.concat(
        [
            history(tiers=[(75, "High", 0.9)]),  # too early
            history(tiers=[(-5, "High", 0.9)]),  # after the alert
            history(zone="TAMPINES", tiers=[(10, "High", 0.9)]),  # another area
            history(tiers=[(20, "Low", 0.001)]),
        ]
    )
    (row,) = build_scorecard([alert()], h).to_dict("records")
    assert row["max_tier"] == "Low"
    assert row["steps_scored"] == 1


def test_no_data_is_never_counted_as_low_or_as_a_miss():
    h = history(tiers=[(20, "No data", float("nan")), (10, "No data", float("nan"))])
    card = build_scorecard([alert()], h)
    (row,) = card.to_dict("records")
    assert row["status"] == "no_score_data"
    assert row["max_tier"] is None
    s = summarise(card)
    assert (s.n_alerts, s.n_scored, s.n_no_score_data, s.n_moderate_or_higher) == (1, 0, 1, 0)
    assert math.isnan(s.rate)


def test_alert_at_sea_has_no_area_and_is_not_scored():
    (row,) = build_scorecard([alert(lat=1.20, lon=104.10)], history()).to_dict("records")
    assert row["zone"] is None
    assert row["status"] == "no_score_data"


def test_cancels_duplicates_and_old_alerts_are_dropped():
    old = alert("OLD", datetime(2026, 10, 5, 12, 0, tzinfo=SGT))
    got = unique_alerts(
        [alert("A1"), alert("A1"), alert("C1", msg_type="Cancel"), old], date(2026, 10, 6)
    )
    assert [a.identifier for a in got] == ["A1"]


def test_summary_counts_and_interval():
    alerts = [alert(f"A{i}", ISSUED + timedelta(hours=i)) for i in range(4)]
    rows = []
    for i, a in enumerate(alerts):
        tier = "Moderate" if i < 3 else "Low"
        rows.append(("BUKIT TIMAH", a.issued_at - timedelta(minutes=10), 0.006, tier))
    h = pd.DataFrame(
        rows, columns=["ura_planning_area", "timestamp", "flood_probability", "risk_tier"]
    )
    s = summarise(build_scorecard(alerts, h))
    assert (s.n_alerts, s.n_scored, s.n_moderate_or_higher) == (4, 4, 3)
    assert s.rate == 0.75
    assert 0.0 <= s.ci_low <= 0.75 <= s.ci_high <= 1.0
    text = s.text()
    assert "3 of 4" in text and "90% interval" in text
    assert "far too few" in text and "not an accuracy result" in text


def test_no_alerts_gives_an_empty_card_and_an_honest_summary():
    card = build_scorecard([], history())
    assert list(card.columns) == SCORECARD_COLUMNS and card.empty
    s = summarise(card)
    assert s.n_alerts == 0
    assert "No PUB flash-flood alerts" in s.text()


def test_circle_lists_centre_area_first():
    got = zones_touched(alert(radius=3.0))
    assert got[0] == "BUKIT TIMAH"
    assert len(got) > 1


def test_cli_with_scores_file(tmp_path):
    page = {
        "data": {
            "records": [
                {
                    "datetime": "2026-10-20T17:05:00+08:00",
                    "item": {
                        "identifier": "A1",
                        "msgType": "Alert",
                        "references": "",
                        "readings": [
                            {
                                "area": {"areaDesc": "Bt Timah", "circle": [LAT, LON, 0.5]},
                                "description": "Flash flood",
                                "headline": "Flash Flood Alert",
                                "severity": "Minor",
                            }
                        ],
                    },
                }
            ]
        }
    }
    (tmp_path / "alerts").mkdir()
    (tmp_path / "alerts" / "p0.json").write_text(json.dumps(page))
    h = history(tiers=[(30, "Moderate", 0.006)])
    h["timestamp"] = h["timestamp"].dt.tz_convert("UTC")
    h.to_csv(tmp_path / "scores.csv", index=False)
    out = tmp_path / "card.csv"
    code = main(
        ["--alerts-dir", str(tmp_path / "alerts"), "--scores", str(tmp_path / "scores.csv"), "--out", str(out)]
    )  # fmt: skip
    assert code == 0
    card = pd.read_csv(out)
    assert card.loc[0, "warning_minutes"] == 30.0
