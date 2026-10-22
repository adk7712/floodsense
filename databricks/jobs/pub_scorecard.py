"""
FloodSense - Live scorecard against PUB (Databricks job task). NOT DEPLOYED.

For every PUB "flash flood" alert since ``--since``, writes what FloodSense showed for that planning
area in the 60 minutes before the alert, to the Delta table ``--out-table`` (default
``workspace.floodsense.pub_scorecard``, overwritten each run). LIVE MONITORING, not an accuracy
result: there are few alerts.

Why it rescores instead of reading gold: ``flood_risk_predictions_gold`` is rewritten each update and
keeps only the last 24 hours, so it can't say what was shown before an older alert. The rainfall
readings in ``rainfall_readings_silver`` are kept, so this job rescores them with the model behind
``--model-uri`` (the ``champion`` alias) for the hour before each alert. That is what the CURRENT
champion would have shown, which is not necessarily what the app displayed at the time if the
champion has changed since (version 3 became champion on 18 Oct 2026). The model version is stored
on every row.

Reads (all in the same schema): ``pub_flood_alerts_silver``, ``rainfall_readings_silver``,
``weather_stations_silver``. The scoring code is ``floodsense.monitoring.pub_scorecard`` from the
project wheel; the wheel's data files come from ``--root-dir`` (zone polygons for the alert areas).

    python pub_scorecard.py --since 2026-10-06

Serverless job tasks cannot set environment variables, so ``--root-dir`` is set before
``floodsense`` is imported (its settings are read at import).
"""

import argparse
import os
import sys
from datetime import date, timedelta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=date.fromisoformat, default=date(2026, 10, 6))
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--schema", default="workspace.floodsense")
    parser.add_argument("--out-table", default="workspace.floodsense.pub_scorecard")
    parser.add_argument("--root-dir", default="/Volumes/workspace/floodsense/artifacts/root")
    parser.add_argument("--model-uri", default="models:/workspace.floodsense.flood_model@champion")
    args = parser.parse_args(argv)
    os.environ["FLOODSENSE_ROOT_DIR"] = args.root_dir

    import pandas as pd
    from pyspark.sql import SparkSession
    from pyspark.sql import types as T

    from floodsense.ingestion.flood_alerts import FloodAlert, zone_for_point
    from floodsense.models.artifact import load_registered_model, registered_version
    from floodsense.monitoring.pub_scorecard import (
        CAVEAT,
        build_scorecard,
        summarise,
        unique_alerts,
    )
    from floodsense.serving.pipeline_core import WARMUP, score_window, spark_to_sgt

    spark = SparkSession.builder.getOrCreate()
    session_tz = spark.conf.get("spark.sql.session.timeZone", "UTC")

    raw = (
        spark.table(f"{args.schema}.pub_flood_alerts_silver").where("msg_type = 'Alert'").toPandas()
    )
    alerts = []
    if not raw.empty:
        raw = spark_to_sgt(raw, session_tz, "issued_at")
        for r in raw.itertuples(index=False):
            lat = None if pd.isna(r.latitude) else float(r.latitude)
            lon = None if pd.isna(r.longitude) else float(r.longitude)
            alerts.append(
                FloodAlert(
                    identifier=str(r.identifier),
                    msg_type="Alert",
                    references=str(r.references or ""),
                    issued_at=r.issued_at.to_pydatetime(),
                    headline=str(r.headline or ""),
                    description=str(r.description or ""),
                    area_desc=str(r.area_desc or ""),
                    latitude=lat,
                    longitude=lon,
                    radius_km=None if pd.isna(r.radius_km) else float(r.radius_km),
                    severity=str(r.severity or ""),
                    zone=zone_for_point(lat, lon) if lat is not None and lon is not None else None,
                )
            )

    model = load_registered_model(args.model_uri)
    version = registered_version(args.model_uri)
    history = pd.DataFrame(
        columns=["ura_planning_area", "timestamp", "flood_probability", "risk_tier"]
    )
    wanted = unique_alerts(alerts, args.since)
    if wanted:
        first = min(a.issued_at for a in wanted) - timedelta(minutes=args.window_minutes)
        last = max(a.issued_at for a in wanted)
        readings = (
            spark.table(f"{args.schema}.rainfall_readings_silver")
            .where(
                f"timestamp >= timestamp'{(first - WARMUP).isoformat()}' "
                f"AND timestamp <= timestamp'{last.isoformat()}'"
            )
            .select("station_id", "timestamp", "rainfall_mm")
            .toPandas()
        )
        stations = (
            spark.table(f"{args.schema}.weather_stations_silver")
            .select("station_id", "name", "latitude", "longitude")
            .toPandas()
        )
        if not readings.empty:
            readings = spark_to_sgt(readings, session_tz)
            history = score_window(readings, stations, model, emit_from=first, emit_to=last)

    card = build_scorecard(wanted, history, args.since, args.window_minutes)
    s = summarise(card)
    card["model_version"] = version
    card["n_alerts"] = s.n_alerts
    card["n_scored"] = s.n_scored
    card["n_moderate_or_higher"] = s.n_moderate_or_higher
    card["rate_ci90_low"] = s.ci_low
    card["rate_ci90_high"] = s.ci_high
    card["caveat"] = CAVEAT

    schema = T.StructType(
        [
            T.StructField("alert_id", T.StringType()),
            T.StructField("issued_at", T.TimestampType()),
            T.StructField("zone", T.StringType()),
            T.StructField("zones_touched", T.StringType()),
            T.StructField("area_desc", T.StringType()),
            T.StructField("window_start", T.TimestampType()),
            T.StructField("steps_scored", T.IntegerType()),
            T.StructField("status", T.StringType()),
            T.StructField("max_tier", T.StringType()),
            T.StructField("max_probability", T.DoubleType()),
            T.StructField("moderate_or_higher", T.BooleanType()),
            T.StructField("first_flag_at", T.TimestampType()),
            T.StructField("warning_minutes", T.DoubleType()),
            T.StructField("model_version", T.StringType()),
            T.StructField("n_alerts", T.IntegerType()),
            T.StructField("n_scored", T.IntegerType()),
            T.StructField("n_moderate_or_higher", T.IntegerType()),
            T.StructField("rate_ci90_low", T.DoubleType()),
            T.StructField("rate_ci90_high", T.DoubleType()),
            T.StructField("caveat", T.StringType()),
        ]
    )

    def clean(v):  # pandas missing values -> None, so Spark gets nulls (never zeros)
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return None
        return v.item() if hasattr(v, "item") else v  # numpy scalar -> python

    rows = [
        tuple(clean(v) for v in r) for r in card[[f.name for f in schema]].itertuples(index=False)
    ]
    (
        spark.createDataFrame(rows, schema)
        .write.mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(args.out_table)
    )
    print(s.text())
    print(f"Wrote {len(rows)} row(s) to {args.out_table} (model version {version}).")
    return 0


if __name__ == "__main__":
    # Databricks runs the file under IPython, which reports any SystemExit (even 0) as a failure,
    # so exit only on an error.
    if code := main():
        sys.exit(code)
