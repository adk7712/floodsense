"""Tests for the rainfall store builder and loaders on a tiny CSV in the data.gov.sg bulk format."""

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.common.schemas import RainfallSnapshot, StationMetadata
from floodsense.data import build_rainfall_store as brs
from floodsense.data import rainfall_store

HEADER = (
    "date,timestamp,update_timestamp,station_id,station_name,station_device_id,"
    "location_longitude,location_latitude,reading_update_timestamp,reading_value,"
    "reading_type,reading_unit"
)
TYPE = "TB1 Rainfall 5 Minute Total F"


def row(ts: str, sid: str, value: float, lat: float = 1.30, lon: float = 103.80, kind=TYPE) -> str:
    return f"{ts[:10]},{ts},{ts},{sid},Name {sid},{sid},{lon},{lat},{ts},{value},{kind},mm"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "root_dir", tmp_path)
    lines = [
        HEADER,
        # Early records sit one second before the grid; they snap to :00/:05.
        row("2021-03-01T09:59:59+08:00", "S1", 0.0),
        row("2021-03-01T09:59:59+08:00", "S2", 1.2),
        row("2021-03-01T10:04:59+08:00", "S1", 0.4),
        row("2021-03-01T10:04:59+08:00", "S1", 0.4),  # exact duplicate
        row("2021-03-01T10:05:00+08:00", "S2", 2.6),
        row("2021-03-01T10:10:00+08:00", "S2", 150.0),  # impossible: dropped
        row("2021-03-01T10:10:00+08:00", "S1", 9.9, kind="Other Type"),  # other series: dropped
        # S2 moves on 2 March.
        row("2021-03-02T10:00:00+08:00", "S2", 0.0, lat=1.31, lon=103.81),
        row("2022-01-01T00:00:00+08:00", "S1", 0.0),  # belongs to the next year's file
    ]
    csv = brs.bulk_dir() / "rainfall_2021.csv"
    csv.parent.mkdir(parents=True)
    csv.write_text("\n".join(lines) + "\n")
    counts = brs.convert_year(2021)
    brs.build_station_table()
    return counts


def test_convert_counts(store):
    assert store["rows"] == 9
    assert store["other_reading_type"] == 1
    assert store["out_of_range"] == 1
    assert store["duplicates"] == 1
    assert store["other_year"] == 1
    assert store["written"] == 5


def test_read_rainfall_snaps_to_grid_and_keeps_zeros(store):
    df = rainfall_store.read_rainfall(datetime(2021, 3, 1), datetime(2021, 3, 3))
    assert list(df.columns) == ["station_id", "timestamp", "rainfall_mm"]
    assert str(df["timestamp"].dt.tz) == "Asia/Singapore"
    first = df[df["timestamp"] == pd.Timestamp("2021-03-01T10:00+08:00")]
    assert dict(zip(first["station_id"], first["rainfall_mm"], strict=True)) == {
        "S1": 0.0,
        "S2": 1.2,
    }
    assert not df.duplicated(["station_id", "timestamp"]).any()
    assert df["timestamp"].is_monotonic_increasing


def test_read_rainfall_bounds_are_inclusive(store):
    t = datetime.fromisoformat("2021-03-01T10:05:00+08:00")
    df = rainfall_store.read_rainfall(t, t)
    assert set(df["station_id"]) == {"S1", "S2"}


def test_missing_is_not_dry(store):
    snaps = rainfall_store.load_snapshots(datetime(2021, 3, 2), datetime(2021, 3, 3))
    (snap,) = snaps
    assert snap.readings == {"S2": 0.0}  # S1 did not report, so it is absent rather than 0.0


def test_station_moves_are_tracked(store):
    table = rainfall_store.load_station_table()
    s2 = table[table["station_id"] == "S2"].reset_index(drop=True)
    assert len(s2) == 2
    assert pd.isna(s2.loc[0, "valid_from"]) and pd.isna(s2.loc[1, "valid_to"])
    assert s2.loc[0, "valid_to"] == s2.loc[1, "valid_from"]
    before = rainfall_store.stations_at(datetime(2021, 3, 1, 12))
    after = rainfall_store.stations_at(datetime(2021, 3, 2, 12))
    assert before["S2"].latitude == 1.30 and after["S2"].latitude == 1.31
    assert set(before) == set(after) == {"S1", "S2"}  # outer periods are open-ended

    snaps = rainfall_store.load_snapshots(datetime(2021, 3, 1), datetime(2021, 3, 3))
    assert snaps[0].stations["S2"].latitude == 1.30
    assert snaps[-1].stations["S2"].latitude == 1.31


def test_unbuilt_store_raises_file_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "root_dir", tmp_path)
    with pytest.raises(FileNotFoundError):
        rainfall_store.read_rainfall(datetime(2021, 1, 1), datetime(2021, 1, 2))


class FakePoller:
    """Two readings a day, one station, so the backfill's month bookkeeping is easy to check."""

    def __init__(self):
        self.days: list[date] = []

    def _iter_day_pages(self, day):
        self.days.append(day)
        meta = {"S9": StationMetadata(station_id="S9", name="Nine", latitude=1.35, longitude=103.9)}
        base = datetime(day.year, day.month, day.day, 8, tzinfo=settings.tzinfo)
        yield [
            RainfallSnapshot(
                timestamp=base + timedelta(minutes=m), readings={"S9": 0.2}, stations=meta
            )
            for m in (0, 5)
        ]


def test_backfill_writes_months_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "root_dir", tmp_path)
    fake = FakePoller()
    monkeypatch.setattr("floodsense.ingestion.poller.NEAPoller", lambda: fake)
    brs.backfill(date(2025, 1, 30), date(2025, 2, 2), day_delay_sec=0)
    assert fake.days == [date(2025, 1, 30), date(2025, 1, 31), date(2025, 2, 1), date(2025, 2, 2)]
    df = rainfall_store.read_rainfall(datetime(2025, 1, 1), datetime(2025, 3, 1))
    assert len(df) == 8 and set(df["station_id"]) == {"S9"}

    # January is complete and skipped on a re-run; February is still open and is refetched.
    fake.days.clear()
    brs.backfill(date(2025, 1, 30), date(2025, 2, 3), day_delay_sec=0)
    assert fake.days == [date(2025, 2, 1), date(2025, 2, 2), date(2025, 2, 3)]
    brs.build_station_table()
    assert set(rainfall_store.stations_at(datetime(2025, 2, 1))) == {"S9"}


def test_gapfill_adds_only_missing_steps(store, monkeypatch):
    """Sparse bulk days are topped up from the API; bulk readings win where both exist."""
    fake = FakePoller()
    monkeypatch.setattr("floodsense.ingestion.poller.NEAPoller", lambda: fake)
    monkeypatch.setattr(brs, "sparse_days", lambda year: [date(2021, 3, 1), date(2021, 3, 5)])
    added = brs.gapfill(2021, day_delay_sec=0)
    assert fake.days == [date(2021, 3, 1), date(2021, 3, 5)] and added == 4
    df = rainfall_store.read_rainfall(datetime(2021, 3, 1), datetime(2021, 3, 6))
    assert set(df["station_id"]) == {"S1", "S2", "S9"}

    fake.days.clear()
    brs.gapfill(2021, day_delay_sec=0)  # resumable: filled days are not refetched
    assert fake.days == []


def test_sparse_days_counts_grid_steps(store):
    days = brs.sparse_days(2021)
    assert date(2021, 3, 1) in days and date(2021, 1, 1) in days and len(days) == 365
