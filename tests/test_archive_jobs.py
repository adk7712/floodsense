"""The daily rain-gauge and the 15-minute flood-alert archive jobs (databricks/jobs), on fake API
pages: no network."""

import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

JOBS = Path(__file__).resolve().parents[1] / "databricks" / "jobs"
SGT = timezone(timedelta(hours=8))


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, JOBS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rainfall = _load("archive_rainfall")
alerts = _load("archive_flood_alerts")


class FakeResponse:
    def __init__(self, status: int, body: dict[str, Any] | None = None):
        self.status_code = status
        self._body = body or {}

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Answers each (date, paginationToken) with a canned response and records the calls."""

    def __init__(self, pages: dict[tuple[str, str | None], FakeResponse]):
        self.pages = pages
        self.calls: list[dict[str, str]] = []

    def get(self, url: str, params: dict[str, str], timeout: int) -> FakeResponse:
        self.calls.append(dict(params))
        return self.pages.get((params["date"], params.get("paginationToken")), FakeResponse(404))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(rainfall.time, "sleep", lambda *_: None)
    monkeypatch.setattr(alerts.time, "sleep", lambda *_: None)


def _rain_page(day: date, hours: range, token: str | None) -> FakeResponse:
    readings = [
        {"timestamp": f"{day.isoformat()}T{h:02d}:{m:02d}:00+08:00", "data": []}
        for h in hours
        for m in range(0, 60, 5)
    ]
    return FakeResponse(200, {"code": 0, "data": {"readings": readings, "paginationToken": token}})


# --- rain gauges ---------------------------------------------------------------------------------


def test_rainfall_day_is_archived_unchanged_and_marked(tmp_path):
    day = date(2026, 10, 14)
    session = FakeSession(
        {
            (day.isoformat(), None): _rain_page(day, range(12, 24), "t1"),
            (day.isoformat(), "t1"): _rain_page(day, range(0, 12), None),
        }
    )
    out, state = tmp_path / "daily", tmp_path / "state"
    assert rainfall.archive_day(session, day, out, state) == 288

    # A plain YYYY-MM-DD folder: under the Auto Loader path "date=..." could become a column.
    files = sorted((out / "2026-10-14").iterdir())
    assert [f.name for f in files] == ["p00.json", "p01.json"]  # only pages under the landing path
    assert json.loads(files[0].read_text()) == session.pages[(day.isoformat(), None)].json()
    marker = json.loads((state / "2026-10-14.complete").read_text())
    assert marker["pages"] == 2 and marker["steps"] == 288
    assert not list(state.glob("*.tmp"))

    # Marked days are skipped without calling the API.
    calls = len(session.calls)
    assert rainfall.archive_day(session, day, out, state) == 0
    assert len(session.calls) == calls


def test_rainfall_404_part_way_keeps_pages_and_empty_day_is_retried(tmp_path):
    day = date(2026, 10, 14)
    session = FakeSession({(day.isoformat(), None): _rain_page(day, range(12, 24), "t1")})
    out, state = tmp_path / "daily", tmp_path / "state"
    assert rainfall.archive_day(session, day, out, state) == 144  # NEA had only half the day
    assert (state / "2026-10-14.complete").exists()

    missing = date(2026, 10, 13)
    assert rainfall.archive_day(session, missing, out, state) == 0
    assert not (out / "2026-10-13").exists()
    assert not (state / "2026-10-13.complete").exists()  # tried again on the next run


def test_rainfall_in_band_api_error_fails_loudly(tmp_path):
    day = date(2026, 10, 14)
    session = FakeSession(
        {(day.isoformat(), None): FakeResponse(200, {"code": 7, "errorMsg": "bad key"})}
    )
    with pytest.raises(RuntimeError, match="bad key"):
        rainfall.archive_day(session, day, tmp_path / "daily", tmp_path / "state")


# --- PUB flood alerts ----------------------------------------------------------------------------


def _alert_page(
    times: list[datetime], with_alert: datetime | None, token: str | None
) -> FakeResponse:
    records = []
    for t in times:
        readings = (
            [{"headline": "Flash flood", "area": {"areaDesc": "Dunearn Rd"}}]
            if t == with_alert
            else []
        )
        records.append({"datetime": t.isoformat(), "item": {"readings": readings}})
    return FakeResponse(200, {"code": 0, "data": {"records": records, "paginationToken": token}})


def test_alerts_only_pages_with_an_alert_are_landed(tmp_path):
    now = datetime(2026, 10, 16, 14, 0, tzinfo=SGT)
    newest = [now - timedelta(minutes=2 * i) for i in range(25)]  # 14:00 back to 13:12
    session = FakeSession({("2026-10-16", None): _alert_page(newest, newest[3], "t1")})
    pages = alerts.recent_pages(session, now, now - timedelta(minutes=20))
    assert len(session.calls) == 1  # the first page already reaches back past the lookback

    out, state = tmp_path / "alerts", tmp_path / "state"
    assert alerts.land(pages, now, out, state) == 1
    (landed,) = out.iterdir()
    assert landed.name == "flood_alerts_20261016_p00_fetched_20261016_140000.json"
    assert json.loads(landed.read_text()) == pages[0]
    assert not list(state.glob("*.tmp"))

    quiet = [_alert_page(newest, None, None).json()]
    assert alerts.land(quiet, now, tmp_path / "quiet", state) == 0
    assert not (tmp_path / "quiet").exists()


def test_alerts_just_after_midnight_also_read_yesterday(tmp_path):
    now = datetime(2026, 10, 16, 0, 5, tzinfo=SGT)
    today = [now - timedelta(minutes=2 * i) for i in range(3)]  # 00:05 back to 00:01
    yesterday = [
        datetime(2026, 10, 15, 23, 59, tzinfo=SGT) - timedelta(minutes=2 * i) for i in range(25)
    ]
    session = FakeSession(
        {
            ("2026-10-16", None): _alert_page(today, None, None),
            ("2026-10-15", None): _alert_page(yesterday, yesterday[5], "t1"),
        }
    )
    pages = alerts.recent_pages(session, now, now - timedelta(minutes=20))
    assert [c["date"] for c in session.calls] == ["2026-10-16", "2026-10-15"]
    assert alerts.land(pages, now, tmp_path / "alerts", tmp_path / "state") == 1
