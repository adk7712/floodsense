"""
Phase 3 acceptance contract (see docs/phase3-handoff.md).

Phase 3 is done when every test here passes locally with the historical rainfall store present,
and ``pytest -m network tests/test_phase3_contract.py`` passes too. Until then:
- rainfall-store tests SKIP when data/raw/rainfall/ is absent (it is gitignored, so CI always skips)
- zone-polygon and flood-event tests XFAIL while their committed files are missing
- calling a loader that still raises NotImplementedError counts as pending (xfail), not a failure

The checks are deliberately about the *content* of the data, not just its shape: the first version
of this contract was satisfied by a store holding one copied storm and empty zero-rain filler.
Coverage, plausible annual totals, distinct years and a spot-check against the live API now make
that impossible, and every event needs a source someone has opened and signed off.
"""

import json
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.data.replay import load_replay
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

PENDING = "Phase 3 deliverable pending (docs/phase3-handoff.md)"
FIRST_YEAR = 2017
STEPS_PER_DAY = 288
# The API publishes 2 decimals; the bulk CSVs keep the gauges' 3 (e.g. 0.408 vs 0.41).
API_ROUNDING_MM = 0.0051


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


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=settings.tzinfo)


def _store_years() -> list[int]:
    return list(range(FIRST_YEAR, _now().year + 1))


@pytest.fixture(scope="module")
def year_2021() -> pd.DataFrame:
    return pd.read_parquet(settings.rainfall_readings_dir / "year=2021")


@pytest.fixture(scope="module")
def daily_totals() -> dict[int, pd.DataFrame]:
    """Per year: station x day matrix of rainfall totals, plus each station's step coverage."""
    out = {}
    for year in _store_years():
        path = settings.rainfall_readings_dir / f"year={year}"
        if not path.exists():
            continue
        df = pd.read_parquet(path, columns=["station_id", "timestamp", "rainfall_mm"])
        df["day"] = df["timestamp"].dt.tz_convert(settings.tzinfo).dt.date
        out[year] = df.groupby(["station_id", "day"])["rainfall_mm"].agg(["sum", "size", "max"])
    return out


def _year(daily_totals: dict[int, pd.DataFrame], year: int) -> pd.DataFrame:
    assert year in daily_totals, f"{year}: no readings in the store"
    return daily_totals[year]


def _year_days(year: int, totals: pd.DataFrame) -> int:
    """Days in the year, or up to the last day present for the current, unfinished year."""
    if year < _now().year:
        return 366 if pd.Timestamp(year=year, month=12, day=31).dayofyear == 366 else 365
    last = max(totals.index.get_level_values("day"))
    return (pd.Timestamp(last) - pd.Timestamp(year=year, month=1, day=1)).days + 1


@needs_rainfall
def test_rainfall_partitions_cover_the_record():
    years = {int(p.name.split("=", 1)[1]) for p in settings.rainfall_readings_dir.glob("year=*")}
    missing = set(_store_years()) - years
    assert not missing, f"missing years: {sorted(missing)}"


@needs_rainfall
def test_every_year_is_densely_covered(daily_totals):
    """At least 40 stations report >= 80% of the year's 5-minute steps (NEA runs ~60-70)."""
    for year in _store_years():
        totals = _year(daily_totals, year)
        steps = totals["size"].groupby(level="station_id").sum()
        coverage = steps / (_year_days(year, totals) * STEPS_PER_DAY)
        dense = int((coverage >= 0.8).sum())
        assert dense >= 40, f"{year}: only {dense} stations cover >= 80% of the year"


@needs_rainfall
def test_the_record_reaches_the_present(daily_totals):
    last = max(_year(daily_totals, _now().year).index.get_level_values("day"))
    assert (_now().date() - last).days <= 31, f"store ends {last}; run the backfill"


@needs_rainfall
def test_annual_rainfall_is_plausible(daily_totals):
    """Singapore gets roughly 1,400-3,500 mm a year; wet days and intense bursts every year."""
    for year in _store_years():
        totals = _year(daily_totals, year)
        if year == _now().year:
            continue  # a partial year has no meaningful annual total
        per_station = totals["sum"].groupby(level="station_id").sum()
        steps = totals["size"].groupby(level="station_id").sum()
        full = per_station[steps >= 0.9 * _year_days(year, totals) * STEPS_PER_DAY]
        assert len(full) >= 20, f"{year}: too few full-coverage stations to judge"
        assert 1000 <= full.median() <= 4500, f"{year}: median annual total {full.median():.0f} mm"
        island = totals["sum"].groupby(level="day").mean()
        wet_share = (island > 0.1).mean()
        assert 0.25 <= wet_share <= 0.9, f"{year}: {wet_share:.0%} of days wet"
        assert totals["max"].max() >= 10, f"{year}: no 5-minute burst of 10 mm or more"


@needs_rainfall
def test_years_are_not_copies_of_each_other(daily_totals):
    island = {
        y: t["sum"].groupby(level="day").mean().to_numpy()[:360] for y, t in daily_totals.items()
    }
    years = sorted(y for y, v in island.items() if len(v) == 360)
    for i, a in enumerate(years):
        for b in years[i + 1 :]:
            corr = np.corrcoef(island[a], island[b])[0, 1]
            assert corr < 0.9, f"{a} and {b} daily rainfall correlate at {corr:.2f}"


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


def _assert_same_readings(got: list, want: list, label: str) -> None:
    assert [s.timestamp for s in got] == [s.timestamp for s in want], f"{label}: steps differ"
    for g, w in zip(got, want, strict=True):
        assert set(g.readings) == set(w.readings), (
            f"{label} {g.timestamp}: station sets differ; "
            f"only in store {sorted(set(g.readings) - set(w.readings))}, "
            f"only in API {sorted(set(w.readings) - set(g.readings))}"
        )
        for sid, mm in w.readings.items():
            assert g.readings[sid] == pytest.approx(mm, abs=API_ROUNDING_MM), f"{g.timestamp} {sid}"
        assert set(g.readings) <= set(g.stations), "snapshot is missing station metadata"


@needs_rainfall
def test_store_matches_the_verified_replay(replay):
    """The 17 Apr 2021 replay was fetched from the data.gov.sg API; the store must agree with it.
    (Necessary, not sufficient: the coverage and network checks guard against a copied replay.)"""
    from floodsense.data.rainfall_store import load_snapshots

    start, end = replay.snapshots[0].timestamp, replay.snapshots[-1].timestamp
    snaps = call_or_pending(load_snapshots, start, end)
    _assert_same_readings(snaps, replay.snapshots, "replay")


@needs_rainfall
@pytest.mark.network
def test_store_matches_the_live_api_on_random_days():
    """Random evening windows across the record must match what the API serves today."""
    from floodsense.data.rainfall_store import load_snapshots
    from floodsense.ingestion.poller import NEAPoller

    rng = np.random.default_rng(20260401)
    poller = NEAPoller(max_attempts=4, timeout_sec=30)
    last_day = _now().normalize() - pd.Timedelta(days=2)
    span = (last_day - pd.Timestamp(f"{FIRST_YEAR}-01-01", tz=settings.tzinfo)).days
    for offset in sorted(rng.choice(span, size=4, replace=False)):
        day = pd.Timestamp(f"{FIRST_YEAR}-01-01", tz=settings.tzinfo) + pd.Timedelta(
            days=int(offset)
        )
        # Late evening: the API pages newest-first, so this needs one or two calls per day.
        start = (day + pd.Timedelta(hours=22)).to_pydatetime()
        end = (day + pd.Timedelta(hours=23, minutes=55)).to_pydatetime()
        _assert_same_readings(
            call_or_pending(load_snapshots, start, end),
            poller.fetch_range(start, end),
            str(day.date()),
        )


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

# Events need sources in every period the model is fitted or scored on.
MIN_SIGNED_OFF = 20
MIN_SIGNED_OFF_TEST_YEARS = 5


@pytest.fixture(scope="module")
def events_csv() -> pd.DataFrame:
    if not settings.flood_events_file.exists():
        pytest.xfail(f"{settings.flood_events_file.name} missing: {PENDING}")
    from floodsense.data.flood_events import read_events_csv

    return read_events_csv()


def test_events_have_the_agreed_columns(events_csv):
    from floodsense.data.flood_events import EVENT_COLUMNS

    assert list(events_csv.columns) == EVENT_COLUMNS
    assert len(events_csv) > 0


def test_every_event_is_sourced(events_csv):
    bad = events_csv.loc[~events_csv["source_url"].str.match(r"^https?://\S+$"), "event_id"]
    assert bad.empty, f"events without a usable source_url: {list(bad)}"
    assert (events_csv["source_name"].str.strip() != "").all()
    quoted = events_csv["evidence_quote"].str.strip().str.len() >= 20
    assert quoted.all(), (
        f"events without an evidence quote: {list(events_csv.loc[~quoted, 'event_id'])}"
    )


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
        assert start <= pd.Timestamp.now(tz="Asia/Singapore"), f"{row['event_id']}: in the future"
        if row["timestamp_end"]:
            assert pd.Timestamp(row["timestamp_end"]) >= start, f"{row['event_id']}: ends early"


def test_events_load_through_the_schema(events_csv):
    from floodsense.data.flood_events import load_flood_events

    events = call_or_pending(load_flood_events, None, True)
    assert len(events) == len(events_csv)
    for ev in events:
        assert ev.timestamp_start.utcoffset() == timedelta(hours=8)
        assert ev.source_url.startswith("http")
        assert ev.cause in {"rain", "rain_tide", "other"}


def test_enough_events_are_signed_off(events_csv):
    """A person has opened each training event's source and confirmed place and time."""
    signed = events_csv[events_csv["verified_by"].str.strip() != ""]
    years = pd.to_datetime(signed["timestamp_start"].str[:10]).dt.year
    assert len(signed) >= MIN_SIGNED_OFF, (
        f"{len(signed)} of {len(events_csv)} events signed off; need {MIN_SIGNED_OFF}"
    )
    in_test = int((years >= settings.test_start_year).sum())
    assert in_test >= MIN_SIGNED_OFF_TEST_YEARS, (
        f"{in_test} signed-off events from {settings.test_start_year} on; "
        f"need {MIN_SIGNED_OFF_TEST_YEARS}"
    )


@pytest.mark.network
def test_event_sources_resolve_to_the_article(events_csv):
    """Each source opens (HTTP 200), is not a redirect to a home or search page, and mentions
    the place. This is what caught the first event list: most links 404'd or bounced to /."""
    import requests

    headers = {"User-Agent": "Mozilla/5.0 (FloodSense source check)"}
    failures = []
    for _, row in events_csv.iterrows():
        try:
            resp = requests.get(row["source_url"], headers=headers, timeout=30)
        except requests.RequestException as exc:
            failures.append(f"{row['event_id']}: {exc.__class__.__name__}")
            continue
        path = requests.utils.urlparse(resp.url).path.strip("/")
        if resp.status_code != 200 or not path or path.startswith(("search", "tag")):
            failures.append(f"{row['event_id']}: {resp.status_code} -> {resp.url}")
            continue
        words = [w for w in re.findall(r"[A-Za-z]{4,}", row["location_raw"]) if w.lower() != "road"]
        if words and not any(w.lower() in resp.text.lower() for w in words):
            failures.append(f"{row['event_id']}: page never mentions {row['location_raw']!r}")
    assert not failures, "\n".join(failures)


def test_hand_typed_event_list_is_gone(events_csv):
    from floodsense.features import ground_truth_extractor

    assert not hasattr(ground_truth_extractor, "HISTORICAL_FLOOD_EVENTS_BENCHMARK"), (
        "labels must come from flood_events.csv via load_flood_events(), not a Python list"
    )
