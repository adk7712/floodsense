from datetime import datetime

import pandas as pd

from floodsense.common.config import settings
from floodsense.data.transport import load_station_exits, pin_zone_stations, stations_at_risk
from floodsense.ingestion.flood_alerts import FloodAlert


def _zone_risk(**tiers):
    return pd.DataFrame(
        {
            "ura_planning_area": list(tiers),
            "risk_tier": list(tiers.values()),
            "flood_probability": [0.01 if t == "High" else 0.004 for t in tiers.values()],
        }
    )


def test_committed_exits_map_to_planning_areas():
    exits = load_station_exits()
    assert exits["station"].nunique() > 150
    assert exits["zone"].notna().all()
    assert {"Beauty World MRT", "Sixth Avenue MRT"} <= set(
        exits[exits["zone"] == "BUKIT TIMAH"]["station"]
    )


def test_only_moderate_and_high_zones_flag_stations():
    out = stations_at_risk(_zone_risk(**{"BUKIT TIMAH": "High", "BEDOK": "Low"}))
    assert set(out["tier"]) == {"High"}
    assert "Beauty World MRT" in set(out["station"])
    assert not (out["zone"] == "BEDOK").any()


def test_nothing_at_risk_on_a_dry_day():
    assert stations_at_risk(_zone_risk(**{"BUKIT TIMAH": "Low"})).empty


def test_station_inside_a_pub_alert_circle_is_flagged():
    exits = load_station_exits()
    beauty_world = exits[exits["station"] == "Beauty World MRT"].iloc[0]
    alert = FloodAlert(
        identifier="A1",
        msg_type="Alert",
        references="",
        issued_at=datetime(2025, 5, 22, 17, 5, tzinfo=settings.tzinfo),
        headline="Flash Flood Alert",
        description="Flash flood at Upper Bukit Timah Rd",
        area_desc="at Upper Bukit Timah Rd",
        latitude=float(beauty_world["latitude"]),
        longitude=float(beauty_world["longitude"]),
        radius_km=0.3,
        severity="Minor",
        zone="BUKIT TIMAH",
    )
    out = stations_at_risk(_zone_risk(**{"BUKIT TIMAH": "Low"}), [alert])
    assert "Beauty World MRT" in set(out["station"])
    assert set(out["tier"]) == {"PUB alert"}


def _exits():
    return pd.DataFrame(
        {
            "station": ["A MRT", "B MRT", "C MRT"],
            "latitude": [1.3, 1.3, 1.3],
            "longitude": [103.8, 103.8, 103.8],
            "zone": ["BEDOK", "TAMPINES", "TAMPINES"],
        }
    )


def test_selected_low_area_stations_are_pinned_with_their_tier():
    risk = _zone_risk(**{"BEDOK": "High", "TAMPINES": "Low"})
    base = stations_at_risk(risk, exits=_exits())
    assert list(base["station"]) == ["A MRT"]  # unchanged: only at-risk stations
    out = pin_zone_stations(base, "TAMPINES", risk, _exits())
    assert list(out["station"]) == ["B MRT", "C MRT", "A MRT"]
    assert list(out["selected"]) == [True, True, False]
    assert list(out["tier"][:2]) == ["Low", "Low"]


def test_pinning_keeps_a_missing_probability_missing():
    risk = _zone_risk(**{"TAMPINES": "Low"})
    risk["flood_probability"] = float("nan")
    out = pin_zone_stations(stations_at_risk(risk, exits=_exits()), "TAMPINES", risk, _exits())
    assert out["probability"].isna().all()


def test_pinning_a_risky_area_puts_it_first_and_no_zone_changes_nothing():
    risk = _zone_risk(**{"BEDOK": "Moderate", "TAMPINES": "High"})
    base = stations_at_risk(risk, exits=_exits())
    assert list(base["station"])[0] != "A MRT"
    out = pin_zone_stations(base, "BEDOK", risk, _exits())
    assert out["station"].iloc[0] == "A MRT" and out["selected"].tolist() == [True, False, False]
    none = pin_zone_stations(base, None, risk, _exits())
    assert list(none["station"]) == list(base["station"]) and not none["selected"].any()


def test_station_in_a_moderate_zone_inside_an_alert_circle_keeps_both_facts():
    alert = FloodAlert(
        identifier="A2",
        msg_type="Alert",
        references="",
        issued_at=datetime(2025, 5, 22, 17, 5, tzinfo=settings.tzinfo),
        headline="Flash Flood Alert",
        description="Flash flood near A MRT",
        area_desc="near A MRT",
        latitude=1.3,
        longitude=103.8,
        radius_km=0.5,
        severity="Minor",
        zone="BEDOK",
    )
    out = stations_at_risk(_zone_risk(**{"BEDOK": "Moderate"}), [alert], _exits())
    row = out[out["station"] == "A MRT"].iloc[0]
    assert row["tier"] == "Moderate"
    assert bool(row["near_alert"]) is True
    assert "near_alert" in stations_at_risk(_zone_risk(**{"BEDOK": "Moderate"}), exits=_exits())
