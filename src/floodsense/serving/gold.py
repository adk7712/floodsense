"""
FloodSense - Read the latest risk per zone from the Databricks gold table.

``flood_risk_predictions_gold`` holds what the Lakeflow pipeline scored (``PREDICTION_COLUMNS``:
rain features, ``flood_probability`` and ``risk_tier`` per zone and 5-minute step). Live mode
shows it as is, without rescoring, and adk's Telegram bot can call ``latest_zone_risk()``.

The query runs on the SQL warehouse through the Databricks SDK's statement API, so there is no
extra driver. Auth is the SDK's own: locally a Databricks CLI profile (``databricks auth login``;
no token in the repo), and in a Databricks App the credentials the App injects.
"""

import os
import re
import time
from typing import Any

import pandas as pd

from floodsense.common.config import settings
from floodsense.serving.pipeline_core import PREDICTION_COLUMNS
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

TIERS = {"Low", "Moderate", "High"}
FLOAT_COLUMNS = [
    "rain_5m",
    "rain_15m",
    "rain_30m",
    "rain_60m",
    "rain_120m",
    "rain_decay_72h",
    "flood_probability",
]
# A waking serverless warehouse can take a while; the statement API waits at most 50 s per call.
QUERY_TIMEOUT_SEC = 120.0
POLL_SEC = 2.0


class GoldUnavailable(Exception):
    """The gold table couldn't be read (not configured, no SDK, auth, query or shape problem)."""


def latest_query(table: str) -> str:
    """The newest row per zone. ``unix_timestamp`` keeps the time zone out of the answer."""
    if not re.fullmatch(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){0,2}", table):
        raise GoldUnavailable(f"not a table name: {table!r}")
    cols = ", ".join(
        "unix_timestamp(timestamp) AS timestamp" if c == "timestamp" else c
        for c in PREDICTION_COLUMNS
    )
    return (
        f"SELECT {cols} FROM {table} "
        "QUALIFY row_number() OVER (PARTITION BY ura_planning_area ORDER BY timestamp DESC) = 1"
    )


def to_frame(columns: list[str], rows: list[list[Any]]) -> pd.DataFrame:
    """The statement result as the app's feature table: typed, SGT timestamps, checked."""
    if columns != PREDICTION_COLUMNS:
        raise GoldUnavailable(f"unexpected gold columns: {columns}")
    if not rows:
        raise GoldUnavailable("the gold table is empty")
    df = pd.DataFrame(rows, columns=columns)
    if df[FLOAT_COLUMNS + ["timestamp", "reporting_stations"]].isna().any().any():
        raise GoldUnavailable("gold has missing values")
    df[FLOAT_COLUMNS] = df[FLOAT_COLUMNS].astype(float)
    df["reporting_stations"] = df["reporting_stations"].astype(int)
    df["timestamp"] = (
        pd.to_datetime(df["timestamp"].astype("int64"), unit="s", utc=True)
        .dt.as_unit("ns")
        .dt.tz_convert(settings.tzinfo)
    )
    unknown = set(df["ura_planning_area"]) - set(URA_PLANNING_AREAS)
    if unknown:
        raise GoldUnavailable(f"unknown zones in gold: {sorted(unknown)}")
    if bad := set(df["risk_tier"]) - TIERS:
        raise GoldUnavailable(f"unknown risk tiers in gold: {sorted(bad)}")
    return df.sort_values("ura_planning_area").reset_index(drop=True)


def _client(profile: str | None) -> Any:
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        raise GoldUnavailable("the databricks-sdk package isn't installed") from exc
    try:
        return WorkspaceClient(profile=profile) if profile else WorkspaceClient()
    except Exception as exc:  # no host, no profile, expired login, ...
        raise GoldUnavailable(f"Databricks sign-in failed: {exc}") from exc


def fetch_latest_gold(
    warehouse_id: str, table: str, profile: str | None = None, client: Any | None = None
) -> pd.DataFrame:
    """The newest gold row for every zone (one SQL query on ``warehouse_id``)."""
    w = client or _client(profile)
    try:
        resp = w.statement_execution.execute_statement(
            statement=latest_query(table), warehouse_id=warehouse_id, wait_timeout="50s"
        )
        deadline = time.monotonic() + QUERY_TIMEOUT_SEC
        while resp.status.state.value in ("PENDING", "RUNNING"):
            if time.monotonic() > deadline:
                w.statement_execution.cancel_execution(resp.statement_id)
                raise GoldUnavailable(f"the gold query took over {QUERY_TIMEOUT_SEC:g} s")
            time.sleep(POLL_SEC)
            resp = w.statement_execution.get_statement(resp.statement_id)
    except GoldUnavailable:
        raise
    except Exception as exc:  # network, auth, permissions, unknown warehouse, ...
        raise GoldUnavailable(f"{type(exc).__name__}: {exc}") from exc
    if resp.status.state.value != "SUCCEEDED":
        error = resp.status.error.message if resp.status.error else resp.status.state.value
        raise GoldUnavailable(f"the gold query failed: {error}")
    columns = [c.name for c in resp.manifest.schema.columns]
    rows = (resp.result.data_array if resp.result else None) or []
    return to_frame(columns, rows)


def warehouse_id() -> str | None:
    """The SQL warehouse to query: ``settings`` first, then a Databricks App's resource env var."""
    return settings.databricks_warehouse_id or os.environ.get("DATABRICKS_WAREHOUSE_ID") or None


def latest_zone_risk() -> pd.DataFrame:
    """The latest gold row per zone, using ``settings`` (or ``DATABRICKS_WAREHOUSE_ID``).

    Columns are ``PREDICTION_COLUMNS``; ``timestamp`` is SGT. Raises ``GoldUnavailable``.
    """
    warehouse = warehouse_id()
    if not warehouse:
        raise GoldUnavailable(
            "no SQL warehouse configured (FLOODSENSE_DATABRICKS_WAREHOUSE_ID in .env)"
        )
    return fetch_latest_gold(warehouse, settings.gold_table, settings.databricks_profile)
