"""Reading the Databricks gold table (floodsense.serving.gold), with a fake SDK client: no network."""

from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.serving import gold
from floodsense.serving.pipeline_core import PREDICTION_COLUMNS
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

EPOCH = 1759743300  # 2025-10-06 09:35 UTC = 17:35 SGT


def gold_rows(epoch: int = EPOCH, high: str = "BUKIT TIMAH") -> list[list[Any]]:
    """One row per zone, as the statement API returns them (JSON_ARRAY: every value a string)."""
    return [
        [zone, str(epoch), "0.0", "0.0", "1.5", "45.0", "50.0", "3.2", "62", "0.012", "High"]
        if zone == high
        else [zone, str(epoch), "0.0", "0.0", "0.0", "0.0", "0.0", "1.0", "62", "0.0", "Low"]
        for zone in sorted(URA_PLANNING_AREAS)
    ]


def _resp(state: str, columns=PREDICTION_COLUMNS, rows=None, error: str | None = None):
    return SimpleNamespace(
        statement_id="s1",
        status=SimpleNamespace(
            state=SimpleNamespace(value=state),
            error=SimpleNamespace(message=error) if error else None,
        ),
        manifest=SimpleNamespace(
            schema=SimpleNamespace(columns=[SimpleNamespace(name=c) for c in columns])
        ),
        result=SimpleNamespace(data_array=rows),
    )


class FakeStatements:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.statements: list[str] = []
        self.cancelled = False

    def execute_statement(self, statement: str, warehouse_id: str, wait_timeout: str):
        self.statements.append(statement)
        return self.responses.pop(0)

    def get_statement(self, statement_id: str):
        return self.responses.pop(0)

    def cancel_execution(self, statement_id: str) -> None:
        self.cancelled = True


def _client(*responses) -> SimpleNamespace:
    return SimpleNamespace(statement_execution=FakeStatements(*responses))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(gold.time, "sleep", lambda *_: None)


def test_latest_gold_is_typed_in_sgt_and_scored_as_stored():
    client = _client(_resp("SUCCEEDED", rows=gold_rows()))
    df = gold.fetch_latest_gold("wh", settings.gold_table, client=client)

    assert list(df.columns) == PREDICTION_COLUMNS
    assert len(df) == len(URA_PLANNING_AREAS)
    assert df["timestamp"].iloc[0] == pd.Timestamp("2025-10-06 17:35", tz="Asia/Singapore")
    assert str(df["timestamp"].dt.tz) == "Asia/Singapore"
    assert df["reporting_stations"].dtype.kind == "i"
    high = df[df["ura_planning_area"] == "BUKIT TIMAH"].iloc[0]
    assert high["risk_tier"] == "High" and high["flood_probability"] == pytest.approx(0.012)

    (sql,) = client.statement_execution.statements
    assert "unix_timestamp(timestamp)" in sql and "PARTITION BY ura_planning_area" in sql


def test_a_waking_warehouse_is_polled_until_the_query_finishes():
    client = _client(_resp("PENDING"), _resp("RUNNING"), _resp("SUCCEEDED", rows=gold_rows()))
    assert len(gold.fetch_latest_gold("wh", settings.gold_table, client=client)) == 55


def test_a_query_that_never_finishes_is_cancelled(monkeypatch):
    clock = iter([0.0, 1000.0])
    monkeypatch.setattr(gold.time, "monotonic", lambda: next(clock))
    client = _client(_resp("PENDING"))
    with pytest.raises(gold.GoldUnavailable, match="took over"):
        gold.fetch_latest_gold("wh", settings.gold_table, client=client)
    assert client.statement_execution.cancelled


@pytest.mark.parametrize(
    "response, message",
    [
        (_resp("FAILED", error="TABLE_OR_VIEW_NOT_FOUND"), "TABLE_OR_VIEW_NOT_FOUND"),
        (_resp("SUCCEEDED", rows=[]), "empty"),
        (
            _resp("SUCCEEDED", columns=PREDICTION_COLUMNS[:-1], rows=[["x"]]),
            "unexpected gold columns",
        ),
        (_resp("SUCCEEDED", rows=[["ATLANTIS", *gold_rows()[0][1:]]]), "unknown zones"),
        (_resp("SUCCEEDED", rows=[[*gold_rows()[0][:-1], "Severe"]]), "unknown risk tiers"),
        (_resp("SUCCEEDED", rows=[[*gold_rows()[0][:2], None, *gold_rows()[0][3:]]]), "missing"),
    ],
)
def test_bad_answers_are_refused(response, message):
    with pytest.raises(gold.GoldUnavailable, match=message):
        gold.fetch_latest_gold("wh", settings.gold_table, client=_client(response))


def test_sdk_errors_become_gold_unavailable():
    class Broken:
        def execute_statement(self, **kwargs):
            raise PermissionError("no access to the warehouse")

    with pytest.raises(gold.GoldUnavailable, match="no access"):
        gold.fetch_latest_gold(
            "wh", settings.gold_table, client=SimpleNamespace(statement_execution=Broken())
        )


def test_table_name_is_checked_before_it_reaches_sql():
    with pytest.raises(gold.GoldUnavailable, match="not a table name"):
        gold.latest_query("gold; DROP TABLE x")


def test_no_warehouse_configured(monkeypatch):
    assert gold.warehouse_id() is None  # conftest clears the developer's .env value
    with pytest.raises(gold.GoldUnavailable, match="no SQL warehouse"):
        gold.latest_zone_risk()
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "from-the-app")
    assert gold.warehouse_id() == "from-the-app"
