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
