from floodsense.ingestion.flood_alerts import active_alerts, parse_flood_alerts, zone_for_point


def _record(identifier, msg_type, references, when, lat=1.33201, lon=103.81, text="Flash flood"):
    return {
        "datetime": when,
        "item": {
            "type": "observation",
            "identifier": identifier,
            "msgType": msg_type,
            "references": references,
            "readings": [
                {
                    "area": {"areaDesc": "at Bt Timah Rd", "circle": [lat, lon, 1]},
                    "description": text,
                    "headline": "Flash Flood Alert",
                    "severity": "Minor",
                }
            ],
        },
    }


def _payload(*records):
    return {"code": 0, "data": {"records": list(records)}}


def test_empty_observations_give_no_alerts():
    payload = _payload({"datetime": "2026-10-06T17:24:54+08:00", "item": {"readings": []}})
    assert parse_flood_alerts(payload) == []


def test_alert_is_parsed_and_mapped_to_its_planning_area():
    payload = _payload(_record("A1", "Alert", "", "2025-05-22T17:05:00+08:00", 1.3245, 103.8130))
    (alert,) = parse_flood_alerts(payload)
    assert alert.msg_type == "Alert"
    assert alert.issued_at.utcoffset().total_seconds() == 8 * 3600
    assert alert.zone == "BUKIT TIMAH"
    assert alert.radius_km == 1


def test_cancel_closes_the_alert_it_references():
    payload = _payload(
        _record("A1", "Alert", "", "2025-05-22T17:05:00+08:00"),
        _record(
            "C1",
            "Cancel",
            "pub_joint_ops_ctr@pub.gov.sg, A1, 2025-05-22T17:05:00+08:00",
            "2025-05-22T17:50:00+08:00",
        ),
        _record("A2", "Alert", "", "2025-05-22T17:30:00+08:00"),
    )
    assert [a.identifier for a in active_alerts(parse_flood_alerts(payload))] == ["A2"]


def test_point_at_sea_has_no_zone():
    assert zone_for_point(1.20, 104.10) is None


def test_cancel_of_a_different_alert_with_a_similar_identifier_does_not_match():
    payload = _payload(
        _record("FL-1", "Alert", "", "2025-05-22T17:05:00+08:00"),
        _record("FL-12", "Alert", "", "2025-05-22T17:10:00+08:00"),
        _record(
            "C1",
            "Cancel",
            "pub@pub.gov.sg,FL-12,2025-05-22T17:10:00+08:00",
            "2025-05-22T17:50:00+08:00",
        ),
    )
    assert [a.identifier for a in active_alerts(parse_flood_alerts(payload))] == ["FL-1"]


def test_cancel_sent_before_the_alert_does_not_cancel_it():
    payload = _payload(
        _record(
            "C1",
            "Cancel",
            "pub@pub.gov.sg,A1,2025-05-22T17:00:00+08:00",
            "2025-05-22T17:00:00+08:00",
        ),
        _record("A1", "Alert", "", "2025-05-22T17:05:00+08:00"),
    )
    assert [a.identifier for a in active_alerts(parse_flood_alerts(payload))] == ["A1"]


def test_empty_identifier_is_not_cancelled_by_anything():
    payload = _payload(
        _record("", "Alert", "", "2025-05-22T17:05:00+08:00"),
        _record("C1", "Cancel", "pub@pub.gov.sg,A1,x", "2025-05-22T17:50:00+08:00"),
    )
    assert len(active_alerts(parse_flood_alerts(payload))) == 1


def test_records_with_missing_datetime_are_skipped_and_naive_is_read_as_sgt():
    good = _record("A1", "Alert", "", "2025-05-22T17:05:00+08:00")
    no_dt = _record("A2", "Alert", "", "2025-05-22T17:05:00+08:00")
    del no_dt["datetime"]
    naive = _record("A3", "Alert", "", "2025-05-22T17:05:00")
    bad = _record("A4", "Alert", "", "not a date")
    alerts = parse_flood_alerts(_payload(good, no_dt, naive, bad))
    assert sorted(a.identifier for a in alerts) == ["A1", "A3"]
    naive_alert = next(a for a in alerts if a.identifier == "A3")
    assert naive_alert.issued_at.utcoffset().total_seconds() == 8 * 3600


def test_midnight_window_queries_the_previous_date_too(monkeypatch):
    from datetime import datetime, timedelta

    import floodsense.ingestion.flood_alerts as fa
    from floodsense.common.config import settings

    class Poller:
        def __init__(self):
            self.dates = []

        def _get_json(self, url, params):
            self.dates.append(params["date"])
            return {"data": {"records": [], "paginationToken": None}}

    since = datetime(2026, 10, 22, 0, 30, tzinfo=settings.tzinfo) - timedelta(hours=3)
    poller = Poller()
    fa.fetch_flood_alert_payloads(day=since.date() + timedelta(days=1), poller=poller, since=since)
    assert poller.dates == ["2026-10-22", "2026-10-21"]


def test_repeating_pagination_token_terminates():
    import floodsense.ingestion.flood_alerts as fa

    class Poller:
        calls = 0

        def _get_json(self, url, params):
            self.calls += 1
            return {"data": {"records": [], "paginationToken": "same"}}

    from datetime import date

    poller = Poller()
    pages = fa.fetch_flood_alert_payloads(day=date(2026, 10, 22), poller=poller)
    assert len(pages) == 2 and poller.calls == 2


def test_failed_query_for_an_earlier_date_keeps_todays_pages_but_todays_failure_raises():
    from datetime import date, datetime

    import pytest

    import floodsense.ingestion.flood_alerts as fa
    from floodsense.common.config import settings
    from floodsense.ingestion.poller import LiveFeedUnavailable

    class Poller:
        def __init__(self, failing):
            self.failing = failing

        def _get_json(self, url, params):
            if params["date"] in self.failing:
                raise LiveFeedUnavailable("HTTP 404")
            return {"data": {"records": [], "paginationToken": None}}

    since = datetime(2026, 10, 21, 22, 0, tzinfo=settings.tzinfo)
    pages = fa.fetch_flood_alert_payloads(date(2026, 10, 22), Poller({"2026-10-21"}), since)
    assert len(pages) == 1
    with pytest.raises(LiveFeedUnavailable):
        fa.fetch_flood_alert_payloads(date(2026, 10, 22), Poller({"2026-10-22"}), since)
