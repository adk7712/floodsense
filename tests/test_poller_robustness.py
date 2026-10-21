from unittest.mock import MagicMock, patch

import pytest

from floodsense.ingestion.poller import LiveFeedUnavailable, NEAPoller, parse_rainfall_payload


def _v2(blocks):
    return {"code": 0, "data": {"stations": [], "readings": blocks}}


def _response(status=200, json_value=None, json_error=None):
    resp = MagicMock(status_code=status)
    if json_error:
        resp.json.side_effect = json_error
    else:
        resp.json.return_value = json_value
    return resp


@pytest.mark.parametrize("bad", [ValueError("Expecting value"), None])
def test_non_json_or_non_dict_200_raises_live_feed_unavailable(bad):
    resp = _response(json_error=bad) if bad else _response(json_value=[1, 2])
    poller = NEAPoller(api_key="x", max_attempts=1)
    with (
        patch("floodsense.ingestion.poller.requests.get", return_value=resp),
        pytest.raises(LiveFeedUnavailable),
    ):
        poller._get_json("http://example.test", {})


def test_block_without_timestamp_is_skipped_not_fatal():
    good = {"timestamp": "2025-05-22T17:05:00+08:00", "data": [{"stationId": "S1", "value": 1.0}]}
    snaps = parse_rainfall_payload(_v2([{"data": []}, good]))
    assert len(snaps) == 1 and snaps[0].readings == {"S1": 1.0}


def test_list_value_is_skipped_but_other_readings_kept():
    block = {
        "timestamp": "2025-05-22T17:05:00+08:00",
        "data": [{"stationId": "S1", "value": [1]}, {"stationId": "S2", "value": 2.0}],
    }
    (snap,) = parse_rainfall_payload(_v2([block]))
    assert snap.readings == {"S2": 2.0}


def test_repeating_pagination_token_raises_instead_of_looping():
    from datetime import date

    payload = {"code": 0, "data": {"stations": [], "readings": [], "paginationToken": "t"}}
    poller = NEAPoller(api_key="x")
    with (
        patch.object(poller, "_get_json", return_value=payload),
        patch("floodsense.ingestion.poller.time.sleep"),
        pytest.raises(LiveFeedUnavailable),
    ):
        list(poller._iter_day_pages(date(2026, 10, 22)))


def test_payload_with_every_block_unusable_raises_value_error():
    bad = {"time": "2025-05-22T17:05:00+08:00", "data": [{"stationId": "S1", "value": 1.0}]}
    with pytest.raises(ValueError):
        parse_rainfall_payload(_v2([bad]))
    assert parse_rainfall_payload(_v2([])) == []


def test_pagination_limit_is_a_live_feed_error_and_fetch_day_does_not_retry_it():
    from datetime import date

    from floodsense.data import build_rainfall_store as brs
    from floodsense.ingestion.poller import PaginationLimitExceeded

    assert issubclass(PaginationLimitExceeded, LiveFeedUnavailable)

    class Poller:
        calls = 0

        def _iter_day_pages(self, day):
            self.calls += 1
            raise PaginationLimitExceeded("repeated token")
            yield []

    poller = Poller()
    with pytest.raises(RuntimeError, match="not retrying"):
        brs._fetch_day(poller, date(2026, 10, 22))
    assert poller.calls == 1
