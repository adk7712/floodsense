"""
Phase 3 acceptance contract (see docs/phase3-handoff.md).

Phase 3 is done when every test here passes locally with the historical rainfall store present.
Until then:
- rainfall-store tests SKIP when data/raw/rainfall/ is absent (it is gitignored, so CI always skips)
- zone-polygon and flood-event tests XFAIL while their committed files are missing
- calling a loader that still raises NotImplementedError counts as pending (xfail), not a failure
"""

import json
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.data.replay import load_replay
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

PENDING = "Phase 3 deliverable pending (docs/phase3-handoff.md)"


def call_or_pending(fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except NotImplementedError:
        pytest.xfail(f"{fn.__module__}.{fn.__name__} not implemented yet: {PENDING}")


@pytest.fixture(scope="module")
def replay():
    return load_replay(settings.replay_file)


# =============================================================================================
# (a) Historical rainfall store - data/raw/rainfall/ (local only)
# =============================================================================================

needs_rainfall = pytest.mark.skipif(
    not settings.rainfall_dir.exists(),
    reason="historical rainfall store not present locally (data/raw/rainfall/ is gitignored)",
)


@pytest.fixture(scope="module")
def year_2021() -> pd.DataFrame:
    return pd.read_parquet(settings.rainfall_readings_dir, filters=[("year", "=", 2021)])


@needs_rainfall
def test_rainfall_partitions_cover_the_record():
    years = {int(p.name.split("=", 1)[1]) for p in settings.rainfall_readings_dir.glob("year=*")}
    assert set(range(2017, 2027)) <= years, f"missing years: {set(range(2017, 2027)) - years}"


@needs_rainfall
def test_rainfall_schema_and_timezone(year_2021):
    assert {"station_id", "timestamp", "rainfall_mm"} <= set(year_2021.columns)
    assert str(year_2021["timestamp"].dt.tz) == "Asia/Singapore"
    assert pd.api.types.is_float_dtype(year_2021["rainfall_mm"])


@needs_rainfall
def test_rainfall_values_are_physical_and_aligned(year_2021):
    mm = year_2021["rainfall_mm"]
    assert mm.notna().all()
    assert ((mm >= 0) & (mm <= 100)).all(), "5-minute totals must be within 0..100 mm"
    ts = year_2021["timestamp"]
    assert ((ts.dt.minute % 5 == 0) & (ts.dt.second == 0)).all(), "timestamps off the 5-min grid"


@needs_rainfall
def test_rainfall_has_no_duplicate_readings(year_2021):
    dupes = year_2021.duplicated(["station_id", "timestamp"]).sum()
    assert dupes == 0, f"{dupes} duplicate (station_id, timestamp) rows in 2021"


@needs_rainfall
def test_every_station_with_readings_has_metadata(year_2021):
    from floodsense.data.rainfall_store import load_station_table

    stations = call_or_pending(load_station_table)
    assert {"station_id", "name", "latitude", "longitude", "valid_from", "valid_to"} <= set(
        stations.columns
    )
    missing = set(year_2021["station_id"]) - set(stations["station_id"])
    assert not missing, f"stations with readings but no metadata: {sorted(missing)}"


@needs_rainfall
def test_store_matches_the_verified_replay(replay):
    """The 17 Apr 2021 replay was fetched from the data.gov.sg API and verified; a correct loader
    reproduces it exactly. Differences here mean the loader (or the source) is wrong."""
    from floodsense.data.rainfall_store import load_snapshots

    # The whole replay: 72 h warm-up (14-17 Apr) plus the storm window, ~950 five-minute steps.
    start, end = replay.snapshots[0].timestamp, replay.snapshots[-1].timestamp
    snaps = call_or_pending(load_snapshots, start, end)
    expected = replay.snapshots

    assert [s.timestamp for s in snaps] == [s.timestamp for s in expected]
    for got, want in zip(snaps, expected, strict=True):
        assert set(got.readings) == set(want.readings), (
            f"{got.timestamp}: station sets differ; "
            f"only in store {sorted(set(got.readings) - set(want.readings))}, "
            f"only in API {sorted(set(want.readings) - set(got.readings))}"
        )
        for sid, mm in want.readings.items():
            assert got.readings[sid] == pytest.approx(mm, abs=1e-6), f"{got.timestamp} {sid}"
        assert set(got.readings) <= set(got.stations), "snapshot is missing station metadata"


@needs_rainfall
def test_store_snapshots_feed_the_feature_pipeline(replay):
    from floodsense.data.rainfall_store import load_snapshots
    from floodsense.features.zone_features import compute_zone_feature_table

    snaps = call_or_pending(load_snapshots, replay.display_start, replay.display_end)
    stations = {}
    for s in snaps:
        stations.update(s.stations)
    table = compute_zone_feature_table(snaps, stations)
    assert table["ura_planning_area"].nunique() == len(URA_PLANNING_AREAS)


# =============================================================================================
# (b) URA Master Plan 2019 planning-area polygons - data/reference/ (committed)
# =============================================================================================


@pytest.fixture(scope="module")
def zone_polygons():
    if not settings.zone_polygons_file.exists():
        pytest.xfail(f"{settings.zone_polygons_file.name} missing: {PENDING}")
    shapely_geometry = pytest.importorskip("shapely.geometry")
    raw = json.loads(settings.zone_polygons_file.read_text())
    return {f["properties"]["name"]: shapely_geometry.shape(f["geometry"]) for f in raw["features"]}


def test_polygons_exist_for_exactly_the_55_planning_areas(zone_polygons):
    assert set(zone_polygons) == set(URA_PLANNING_AREAS)


def test_polygons_are_valid_real_boundaries(zone_polygons):
    for name, geom in zone_polygons.items():
        assert geom.is_valid, f"{name}: invalid geometry"
        assert geom.geom_type in {"Polygon", "MultiPolygon"}, f"{name}: {geom.geom_type}"
        # The old placeholders were 12-point circles; real boundaries have far more vertices.
        coords = (
            len(geom.exterior.coords)
            if geom.geom_type == "Polygon"
            else sum(len(p.exterior.coords) for p in geom.geoms)
        )
        assert coords > 20, f"{name}: {coords} vertices looks like a placeholder"


def test_zone_reference_points_lie_inside_their_polygons(zone_polygons):
    from shapely.geometry import Point

    outside = [
        name
        for name, meta in URA_PLANNING_AREAS.items()
        if not zone_polygons[name].contains(Point(meta["lon"], meta["lat"]))
    ]
    assert not outside, f"lat/lon outside its own polygon (derive them from polygons): {outside}"


# =============================================================================================
# (c) Sourced flood events - data/reference/flood_events.csv (committed)
# =============================================================================================

EVENT_COLUMNS = {
    "event_id",
    "timestamp_start",
    "timestamp_end",
    "time_precision",
    "location_raw",
    "ura_planning_area",
    "severity",
    "cause",
    "source_url",
    "source_name",
    "notes",
}


@pytest.fixture(scope="module")
def events_csv() -> pd.DataFrame:
    if not settings.flood_events_file.exists():
        pytest.xfail(f"{settings.flood_events_file.name} missing: {PENDING}")
    return pd.read_csv(settings.flood_events_file, dtype=str, keep_default_na=False)


def test_events_have_the_agreed_columns(events_csv):
    assert set(events_csv.columns) == EVENT_COLUMNS
    assert len(events_csv) > 0


def test_every_event_is_sourced(events_csv):
    bad = events_csv.loc[~events_csv["source_url"].str.match(r"^https?://\S+$"), "event_id"]
    assert bad.empty, f"events without a usable source_url: {list(bad)}"
    assert (events_csv["source_name"].str.strip() != "").all()


def test_event_fields_use_allowed_values(events_csv):
    assert set(events_csv["ura_planning_area"]) <= set(URA_PLANNING_AREAS)
    assert set(events_csv["severity"]) <= {"Minor", "Moderate", "Severe"}
    assert set(events_csv["cause"]) <= {"rain", "rain_tide", "other"}
    assert set(events_csv["time_precision"]) <= {"exact", "approx_15min", "approx_hour", "day_only"}
    assert not events_csv.duplicated(["event_id", "ura_planning_area"]).any()


def test_event_times_are_sgt_and_within_the_rainfall_record(events_csv):
    offset = re.compile(r"[+-]\d{2}:\d{2}$")
    for _, row in events_csv.iterrows():
        assert offset.search(row["timestamp_start"]), f"{row['event_id']}: start lacks a UTC offset"
        start = pd.Timestamp(row["timestamp_start"])
        assert start.utcoffset() == timedelta(hours=8), f"{row['event_id']}: not SGT"
        assert pd.Timestamp("2017-01-01T00:00+08:00") <= start, f"{row['event_id']}: before 2017"
        if row["timestamp_end"]:
            assert pd.Timestamp(row["timestamp_end"]) >= start, f"{row['event_id']}: ends early"


def test_events_load_through_the_schema(events_csv):
    from floodsense.data.flood_events import load_flood_events

    events = call_or_pending(load_flood_events)
    assert len(events) == len(events_csv)
    for ev in events:
        assert ev.timestamp_start.utcoffset() == timedelta(hours=8)
        assert getattr(ev, "source_url", "").startswith("http")
        assert getattr(ev, "cause", None) in {"rain", "rain_tide", "other"}


def test_hand_typed_event_list_is_gone(events_csv):
    from floodsense.features import ground_truth_extractor

    assert not hasattr(ground_truth_extractor, "HISTORICAL_FLOOD_EVENTS_BENCHMARK"), (
        "labels must come from flood_events.csv via load_flood_events(), not a Python list"
    )
