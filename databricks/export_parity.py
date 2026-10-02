"""Export the Phase 5 parity files from the workspace: gold rows for the replay window + run_info.

Reads the gold table and row counts through the SQL Statement Execution API, using the databricks
CLI (choose the profile with DATABRICKS_CONFIG_PROFILE). See DEPLOYMENT.md, section 4.

    python databricks/export_parity.py <pipeline_id> <update_id> <warehouse_id> data/reference/phase5
"""

import csv
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PIPELINE, UPDATE, WAREHOUSE, OUT = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
SCHEMA = "workspace.floodsense"
SGT = ZoneInfo("Asia/Singapore")
COLUMNS = [
    "ura_planning_area", "timestamp", "rain_5m", "rain_15m", "rain_30m", "rain_60m",
    "rain_120m", "rain_decay_72h", "reporting_stations", "flood_probability", "risk_tier",
]


def cli(*args, body=None):
    cmd = ["databricks", *args, "-o", "json"]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    return json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)


def sql(statement):
    r = cli("api", "post", "/api/2.0/sql/statements", body={
        "warehouse_id": WAREHOUSE, "statement": statement, "wait_timeout": "50s",
        "format": "JSON_ARRAY", "disposition": "INLINE", "on_wait_timeout": "CONTINUE",
    })
    while r["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(3)
        r = cli("api", "get", f"/api/2.0/sql/statements/{r['statement_id']}")
    if r["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(json.dumps(r["status"]))
    rows = list(r.get("result", {}).get("data_array", []))
    link = r.get("result", {}).get("next_chunk_internal_link")
    while link:
        chunk = cli("api", "get", link)
        rows += chunk.get("data_array", [])
        link = chunk.get("next_chunk_internal_link")
    return rows


def count(table):
    return int(sql(f"SELECT count(*) FROM {SCHEMA}.{table}")[0][0])


start = datetime(2021, 4, 17, 11, 0, tzinfo=SGT)
end = datetime(2021, 4, 17, 18, 0, tzinfo=SGT)
us = lambda t: int(t.timestamp() * 1_000_000)  # noqa: E731
cols = ", ".join(
    "unix_micros(timestamp)" if c == "timestamp" else f"cast({c} as string)" for c in COLUMNS
)
rows = sql(
    f"SELECT {cols} FROM {SCHEMA}.flood_risk_predictions_gold "
    f"WHERE unix_micros(timestamp) BETWEEN {us(start)} AND {us(end)} "
    "ORDER BY timestamp, ura_planning_area"
)
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / "databricks_replay_predictions.csv").open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(COLUMNS)
    for r in rows:
        ts = datetime.fromtimestamp(int(r[1]) / 1_000_000, tz=UTC).astimezone(SGT).isoformat()
        w.writerow([r[0], ts, *r[2:]])

HOST = cli("auth", "describe")["details"]["host"]
upd = cli("pipelines", "get-update", PIPELINE, UPDATE)["update"]
landing = cli("fs", "ls", "dbfs:/Volumes/workspace/floodsense/landing/rainfall")
info = {
    "workspace_host": HOST,
    "pipeline_id": PIPELINE,
    "update_id": UPDATE,
    "update_state": upd["state"],
    "update_started_at": datetime.fromtimestamp(upd["creation_time"] / 1000, tz=UTC).isoformat(),
    "landing_files": sum(1 for e in landing if e["name"].endswith(".json")),
    "bronze_rows": count("raw_rainfall_bronze"),
    "quarantine_rows": count("raw_payloads_quarantine"),
    "silver_rows": count("rainfall_readings_silver"),
    "stations_rows": count("weather_stations_silver"),
    "gold_rows": count("flood_risk_predictions_gold"),
    "exported_rows": len(rows),
    "exported_window_sgt": [start.isoformat(), end.isoformat()],
    "row_counts_source": "SELECT count(*) on each table after the update, via the SQL warehouse",
}
(OUT / "run_info.json").write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))
