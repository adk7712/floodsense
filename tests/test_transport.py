from datetime import datetime

import pandas as pd

from floodsense.common.config import settings
from floodsense.data.transport import load_station_exits, stations_at_risk
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
