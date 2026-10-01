"""Acceptance tests for the Phase 2 correctness fixes.

Each test pins a bug found in the codebase review:
- the API parser understood only the legacy payload shape, and the primary endpoint 404'd
- live mode silently substituted random rain while labelled "LIVE"
- hand-typed station metadata dropped ~45% of the replay's rainfall
- app features were accumulated in a cache shared across reruns, so clicks inflated rainfall
- the replay window started after the storm had already peaked
"""

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import requests

from floodsense.common.config import settings
from floodsense.data.replay import load_replay, save_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.ingestion.poller import LiveFeedUnavailable, NEAPoller, parse_rainfall_payload
from floodsense.spatial.idw_matrix import IDWMatrixEngine

SGT = settings.timezone

V2_PAYLOAD = {
    "code": 0,
    "errorMsg": "",
    "data": {
        "stations": [
            {"id": "S1", "name": "One", "location": {"latitude": 1.30, "longitude": 103.80}},
            {"id": "S2", "name": "Two", "location": {"latitude": 1.35, "longitude": 103.90}},
        ],
        "readings": [
            {
                "timestamp": "2021-04-17T12:05:00+08:00",
                "data": [{"stationId": "S1", "value": 2.0}, {"stationId": "S2", "value": 0.4}],
            },
            {
                "timestamp": "2021-04-17T12:00:00+08:00",
                "data": [{"stationId": "S1", "value": 1.0}, {"stationId": "S2", "value": 0.2}],
            },
        ],
        "readingType": "TB1 Rainfall 5 Minute Total F",
        "readingUnit": "mm",
    },
}

LEGACY_PAYLOAD = {
    "metadata": {
        "stations": [
            {
                "id": "S1",
                "device_id": "S1",
                "name": "One",
                "location": {"latitude": 1.30, "longitude": 103.80},
            },
            {
                "id": "S2",
                "device_id": "S2",
                "name": "Two",
                "location": {"latitude": 1.35, "longitude": 103.90},
            },
        ]
    },
    "items": [
        {
            "timestamp": "2021-04-17T12:00:00+08:00",
            "readings": [{"station_id": "S1", "value": 1.0}, {"station_id": "S2", "value": 0.2}],
        },
        {
            "timestamp": "2021-04-17T12:05:00+08:00",
            "readings": [{"station_id": "S1", "value": 2.0}, {"station_id": "S2", "value": 0.4}],
        },
    ],
}


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


# --- ingestion -------------------------------------------------------------------------------


def test_v2_and_legacy_payloads_parse_identically():
    v2 = parse_rainfall_payload(V2_PAYLOAD)
    legacy = parse_rainfall_payload(LEGACY_PAYLOAD)

    assert [s.timestamp for s in v2] == [s.timestamp for s in legacy]
    assert [s.readings for s in v2] == [s.readings for s in legacy]
    # Snapshots come back in chronological order regardless of API ordering
    assert v2[0].timestamp < v2[1].timestamp
    assert v2[1].readings == {"S1": 2.0, "S2": 0.4}
    # Station metadata travels with the payload instead of a hand-typed table
    assert v2[0].stations["S2"].latitude == pytest.approx(1.35)


def test_parsed_timestamps_are_timezone_aware_sgt():
    snap = parse_rainfall_payload(V2_PAYLOAD)[0]
    assert snap.timestamp.tzinfo is not None
    assert snap.timestamp.utcoffset() == timedelta(hours=8)


def test_poller_raises_instead_of_fabricating_data(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    with pytest.raises(LiveFeedUnavailable):
        NEAPoller().fetch_latest()


def test_poller_surfaces_rate_limit_as_unavailable(monkeypatch):
    payload = {"code": 24, "name": "TOO_MANY_REQUESTS", "data": None, "errorMsg": "Rate limit"}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(payload, status_code=429))
    monkeypatch.setattr("time.sleep", lambda *_: None)

    with pytest.raises(LiveFeedUnavailable):
        NEAPoller().fetch_latest()


def test_poller_sends_api_key_when_configured(monkeypatch):
    seen_headers = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen_headers.update(headers or {})
        return _FakeResponse(V2_PAYLOAD)

    monkeypatch.setattr(requests, "get", fake_get)
    snap = NEAPoller(api_key="secret-key").fetch_latest()

    assert seen_headers.get("x-api-key") == "secret-key"
    assert snap.readings == {"S1": 2.0, "S2": 0.4}  # the latest block


# --- replay data -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def replay():
    return load_replay(settings.replay_file)


def test_replay_has_metadata_for_every_reporting_station(replay):
    reporting = {sid for snap in replay.snapshots for sid in snap.readings}
    missing = reporting - set(replay.stations)
    assert not missing, f"stations with readings but no coordinates: {sorted(missing)}"


def test_replay_window_starts_before_the_storm(replay):
    # Rain over western Singapore began ~11:30 SGT on 17 Apr 2021 (PUB: 161.4 mm 12:25-15:25).
    start = replay.display_start
    assert start.utcoffset() == timedelta(hours=8)
    assert (start.hour, start.minute) <= (11, 30)


def test_replay_includes_72h_warmup_for_wet_ground_feature(replay):
    first = replay.snapshots[0].timestamp
    assert replay.display_start - first >= timedelta(hours=72)


def test_replay_snapshots_are_contiguous_5_minute_steps(replay):
    stamps = [s.timestamp for s in replay.snapshots]
    gaps = {b - a for a, b in zip(stamps, stamps[1:], strict=False)}
    assert gaps == {timedelta(minutes=5)}


def test_replay_round_trip(tmp_path):
    snaps = parse_rainfall_payload(V2_PAYLOAD)
    path = save_replay(
        tmp_path / "r.json",
        event_name="test",
        source="unit test",
        snapshots=snaps,
        display_start=snaps[0].timestamp,
        display_end=snaps[-1].timestamp,
    )
    loaded = load_replay(path)
    assert [s.readings for s in loaded.snapshots] == [s.readings for s in snaps]
    assert [s.timestamp for s in loaded.snapshots] == [s.timestamp for s in snaps]
    assert set(loaded.stations) == {"S1", "S2"}


# --- zone features ---------------------------------------------------------------------------


def test_vectorised_interpolation_matches_per_step_rebalancing():
    engine = IDWMatrixEngine()
    rng = np.random.default_rng(1)
    values = rng.uniform(0, 10, size=(5, len(engine.station_ids)))
    values[rng.uniform(size=values.shape) < 0.4] = np.nan  # ~40% of gauges offline per step
    matrix = engine.interpolate_matrix(values)
    for t in range(values.shape[0]):
        readings = {
            sid: values[t, i]
            for i, sid in enumerate(engine.station_ids)
            if not np.isnan(values[t, i])
        }
        per_step = [z.rainfall_mm for z in engine.interpolate_rainfall(readings)]
        np.testing.assert_allclose(matrix[t], per_step, atol=0.006)


def test_zone_features_use_every_station_with_metadata(replay):
    table = compute_zone_feature_table(replay.snapshots, replay.stations)
    peak = table[table["timestamp"] == pd.Timestamp("2021-04-17T12:15:00+08:00")]
    # Rain was island-wide at 12:15. With every reporting station placed (the hand-typed list
    # placed 37 of 67), every zone sees rain and all reporting gauges are counted.
    assert len(peak) == 55
    assert (peak["rain_5m"] > 0).all()
    assert peak["reporting_stations"].iloc[0] >= 60


def test_zone_features_are_causal(replay):
    """Features at step k must not depend on anything after k."""
    snaps = replay.snapshots
    full = compute_zone_feature_table(snaps, replay.stations)
    rng = np.random.default_rng(0)
    for k in rng.choice(np.arange(len(snaps) - 300, len(snaps)), size=3, replace=False):
        ts = snaps[k].timestamp
        partial = compute_zone_feature_table(snaps[: k + 1], replay.stations)
        a = full[full["timestamp"] == ts].set_index("ura_planning_area").sort_index()
        b = partial[partial["timestamp"] == ts].set_index("ura_planning_area").sort_index()
        pd.testing.assert_frame_equal(a, b)


def test_rolling_30m_is_sum_of_last_six_steps(replay):
    table = compute_zone_feature_table(replay.snapshots, replay.stations)
    zone = table[table["ura_planning_area"] == "BUKIT TIMAH"].sort_values("timestamp")
    expected = zone["rain_5m"].rolling(6, min_periods=1).sum()
    np.testing.assert_allclose(zone["rain_30m"].to_numpy(), expected.to_numpy(), atol=0.011)


# --- app -------------------------------------------------------------------------------------

APP_PATH = str(
    Path(__file__).resolve().parents[1] / "src" / "floodsense" / "app" / "streamlit_app.py"
)


def _kpis(at):
    return [m.value for m in at.metric]


@pytest.mark.integration
def test_app_kpis_do_not_change_when_only_the_zone_changes():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = "Replay Storm"  # the app opens in Live Feed
    at.run(timeout=60)
    assert not at.exception
    baseline = _kpis(at)

    for zone in ["BEDOK", "BISHAN", "BEDOK", "BUKIT TIMAH"]:
        at.selectbox[0].set_value(zone)
        at.run(timeout=60)
        assert not at.exception
        assert _kpis(at) == baseline, f"KPIs drifted after selecting {zone}"


@pytest.mark.integration
def test_app_live_mode_reports_unavailable_instead_of_fake_data(monkeypatch):
    from streamlit.testing.v1 import AppTest

    def boom(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    at = AppTest.from_file(APP_PATH)
    at.session_state["mode"] = "Replay Storm"  # the app opens in Live Feed
    at.run(timeout=60)
    at.sidebar.button_group[0].set_value("Live Feed")
    at.run(timeout=60)

    assert not at.exception
    page_text = " ".join(str(e.value) for e in [*at.markdown, *at.error, *at.warning, *at.info])
    assert "LIVE CONNECTED" not in page_text
    assert at.error or at.warning, "expected a visible 'live feed unavailable' message"


@pytest.mark.network
def test_live_api_contract():
    """The real data.gov.sg endpoint still returns a payload our parser understands."""
    snap = NEAPoller().fetch_latest()
    assert snap.readings
    assert set(snap.readings) <= set(snap.stations)
    assert snap.timestamp.utcoffset() == timedelta(hours=8)
