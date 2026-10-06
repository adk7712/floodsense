"""
FloodSense - Databricks Lakeflow Declarative Pipeline.

Architecture (DEPLOYMENT.md):
Landing volume -> Bronze (Auto Loader) -> Silver (payloads_to_readings) -> Gold (gold_from_silver)

Ground rules:
1. No feature or scoring logic in Spark. All transformations and ML inference call
   floodsense.serving.pipeline_core in pandas.
2. Never fabricate data. Invalid readings are dropped; malformed payloads are quarantined.
3. Missing is not dry: absent readings remain absent, not 0.0.

Pipeline configuration (all optional):
    floodsense.root_dir       folder holding copies of the repo's models/ and data/reference/
                              (sets FLOODSENSE_ROOT_DIR; needed on Databricks, not locally)
    floodsense.landing_path   UC volume Auto Loader watches (default /Volumes/floodsense/default/landing)
    floodsense.schema_path    Auto Loader schema location
    floodsense.gold_hours     hours of predictions gold keeps (default 24; it reads 72 h more)
"""

import json
import os
from typing import Any

import dlt
from pyspark.sql import Window
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    StringType,
    StructField,
    StructType,
    LongType,
    TimestampType,
)
import pandas as pd

# Before floodsense is imported: its settings read FLOODSENSE_ROOT_DIR once, at import. Only the
# driver needs it (gold loads the model); the parse UDF on the workers reads no files.
_root_dir = spark.conf.get("floodsense.root_dir", "")
if _root_dir:
    os.environ["FLOODSENSE_ROOT_DIR"] = _root_dir

from floodsense.models.artifact import FloodModel, load_model  # noqa: E402
from floodsense.serving.pipeline_core import (  # noqa: E402
    PREDICTION_COLUMNS,
    WARMUP,
    gold_from_silver,
    payloads_to_readings,
)

# -------------------------------------------------------------------------
# Schemas
# -------------------------------------------------------------------------
READINGS_SCHEMA = StructType(
    [
        StructField("station_id", StringType(), False),
        StructField("timestamp", TimestampType(), False),
        StructField("rainfall_mm", DoubleType(), False),
    ]
)

STATIONS_SCHEMA = StructType(
    [
        StructField("station_id", StringType(), False),
        StructField("name", StringType(), False),
        StructField("latitude", DoubleType(), False),
        StructField("longitude", DoubleType(), False),
    ]
)

GOLD_SCHEMA = StructType(
    [
        StructField("ura_planning_area", StringType(), False),
        StructField("timestamp", TimestampType(), False),
        StructField("rain_5m", DoubleType(), False),
        StructField("rain_15m", DoubleType(), False),
        StructField("rain_30m", DoubleType(), False),
        StructField("rain_60m", DoubleType(), False),
        StructField("rain_120m", DoubleType(), False),
        StructField("rain_decay_72h", DoubleType(), False),
        StructField("reporting_stations", LongType(), False),
        StructField("flood_probability", DoubleType(), False),
        StructField("risk_tier", StringType(), False),
    ]
)

PARSED_RESULT_SCHEMA = StructType(
    [
        StructField("is_valid", DoubleType(), False),  # 1.0 = valid, 0.0 = error
        StructField("error_message", StringType(), True),
        StructField("readings", ArrayType(READINGS_SCHEMA), True),
        StructField("stations", ArrayType(STATIONS_SCHEMA), True),
    ]
)


# -------------------------------------------------------------------------
# Helper UDFs
# -------------------------------------------------------------------------
@F.udf(returnType=PARSED_RESULT_SCHEMA)
def parse_single_payload_udf(payload_str: str) -> dict[str, Any]:
    """Parse one raw JSON payload string using floodsense.serving.pipeline_core."""
    if not payload_str or not payload_str.strip():
        return {
            "is_valid": 0.0,
            "error_message": "Empty payload string",
            "readings": [],
            "stations": [],
        }
    try:
        payload_dict = json.loads(payload_str)
        readings_df, stations_df = payloads_to_readings([payload_dict])

        readings_list = [
            {
                "station_id": str(r.station_id),
                "timestamp": r.timestamp.to_pydatetime(),
                "rainfall_mm": float(r.rainfall_mm),
            }
            for r in readings_df.itertuples(index=False)
        ]
        stations_list = [
            {
                "station_id": str(s.station_id),
                "name": str(s.name),
                "latitude": float(s.latitude),
                "longitude": float(s.longitude),
            }
            for s in stations_df.itertuples(index=False)
        ]
        return {
            "is_valid": 1.0,
            "error_message": None,
            "readings": readings_list,
            "stations": stations_list,
        }
    except Exception as exc:
        return {
            "is_valid": 0.0,
            "error_message": f"{type(exc).__name__}: {exc}",
            "readings": [],
            "stations": [],
        }


# -------------------------------------------------------------------------
# 01. BRONZE: raw payloads, one row per landed file (Auto Loader)
# -------------------------------------------------------------------------
@dlt.table(
    name="raw_rainfall_bronze",
    comment="Raw NEA rainfall API responses from the landing volume, one row per file, unparsed.",
    table_properties={"quality": "bronze"},
)
def raw_rainfall_bronze():
    landing_volume = spark.conf.get(
        "floodsense.landing_path", "/Volumes/floodsense/default/landing"
    )
    schema_volume = spark.conf.get(
        "floodsense.schema_path", "/Volumes/floodsense/default/schema/bronze"
    )
    # Whole-file text: the payload is kept verbatim, and a malformed file can't fail ingestion
    # (it is parsed below and quarantined if it doesn't parse).
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "text")
        .option("wholetext", "true")
        .option("cloudFiles.schemaLocation", schema_volume)
        .load(landing_volume)
        .select(
            F.col("value").alias("raw_payload_text"),
            F.col("_metadata.file_name").alias("source_file"),
            F.col("_metadata.file_modification_time").alias("ingested_at"),
        )
    )


# -------------------------------------------------------------------------
# 02. SILVER: parse once, quarantine failures, expectations on readings
# -------------------------------------------------------------------------
@dlt.table(
    name="parsed_payloads",
    comment="Each bronze payload parsed once by pipeline_core.payloads_to_readings.",
    table_properties={"quality": "silver"},
)
def parsed_payloads():
    return dlt.read_stream("raw_rainfall_bronze").withColumn(
        "parsed", parse_single_payload_udf(F.col("raw_payload_text"))
    )


@dlt.table(
    name="raw_payloads_quarantine",
    comment="Payloads that failed JSON decoding or schema validation, with the reason.",
    table_properties={"quality": "quarantine"},
)
def raw_payloads_quarantine():
    return (
        dlt.read_stream("parsed_payloads")
        .filter(F.col("parsed.is_valid") == 0.0)
        .select(
            "source_file",
            "ingested_at",
            "raw_payload_text",
            F.col("parsed.error_message").alias("quarantine_reason"),
        )
    )


@dlt.table(
    name="rainfall_readings_silver",
    comment="5-minute station rainfall readings (valid 0..100 mm). Missing readings stay missing.",
    table_properties={"quality": "silver"},
)
@dlt.expect_or_drop("valid_timestamp", "timestamp IS NOT NULL")
@dlt.expect_or_drop("valid_rainfall_range", "rainfall_mm >= 0.0 AND rainfall_mm <= 100.0")
def rainfall_readings_silver():
    return (
        dlt.read_stream("parsed_payloads")
        .filter(F.col("parsed.is_valid") == 1.0)
        .select(F.explode("parsed.readings").alias("reading"))
        .select(
            F.col("reading.station_id").alias("station_id"),
            F.col("reading.timestamp").alias("timestamp"),
            F.col("reading.rainfall_mm").alias("rainfall_mm"),
        )
        .withWatermark("timestamp", "7 days")
        .dropDuplicates(["station_id", "timestamp"])
    )


@dlt.table(
    name="weather_stations_silver",
    comment="Latest published location for each station (stations move, e.g. S119, S215).",
    table_properties={"quality": "silver"},
)
def weather_stations_silver():
    stations = (
        dlt.read("parsed_payloads")
        .filter(F.col("parsed.is_valid") == 1.0)
        .select("ingested_at", F.explode("parsed.stations").alias("s"))
        .select("ingested_at", "s.*")
    )
    latest = Window.partitionBy("station_id").orderBy(F.col("ingested_at").desc())
    return (
        stations.withColumn("rank", F.row_number().over(latest))
        .filter("rank = 1")
        .select("station_id", "name", "latitude", "longitude")
    )


# -------------------------------------------------------------------------
# 03. GOLD: zone features and risk, computed by pipeline_core in pandas
# -------------------------------------------------------------------------
# No collect()/toPandas() here: Lakeflow evaluates a table function while it builds the graph, so
# reading silver into the driver would score whatever silver held at that moment (empty on the
# first run). Instead the whole window goes to one pandas call through applyInPandas, which Spark
# runs after silver is updated. The data is small (about 96 h x 70 gauges).
@dlt.table(
    name="flood_risk_predictions_gold",
    comment="Calibrated flood probability and Low/Moderate/High tier per zone and 5-minute step, "
    "for the last floodsense.gold_hours, computed by pipeline_core.gold_from_silver.",
    table_properties={"quality": "gold"},
)
def flood_risk_predictions_gold():
    emit_hours = float(spark.conf.get("floodsense.gold_hours", "24"))
    session_tz = spark.conf.get("spark.sql.session.timeZone", "UTC")
    # Loaded here (it reads floodsense.root_dir) and shipped to the worker inside score().
    # load_model() falls back to a heuristic when the file is missing: refuse that.
    model = load_model()
    if not isinstance(model, FloodModel):
        raise RuntimeError(f"trained model not found under {os.environ.get('FLOODSENSE_ROOT_DIR')}")

    # Readings from newest - (emit_hours + 72 h warm-up) on: pipeline_core.silver_window_start.
    span_minutes = int(emit_hours * 60 + WARMUP / pd.Timedelta(minutes=1))
    silver = dlt.read("rainfall_readings_silver")
    newest = silver.agg(F.max("timestamp").alias("newest"))
    window = (
        silver.crossJoin(newest)
        .filter(F.expr(f"timestamp >= newest - INTERVAL {span_minutes} MINUTES"))
        .join(dlt.read("weather_stations_silver"), "station_id")  # every reading's station is known
        .select("station_id", "timestamp", "rainfall_mm", "name", "latitude", "longitude")
    )

    def score(pdf):  # no type hints: Spark would try to infer a UDF type from them
        readings = pdf[["station_id", "timestamp", "rainfall_mm"]]
        stations = pdf[["station_id", "name", "latitude", "longitude"]].drop_duplicates(
            "station_id"
        )
        # Spark hands pandas naive session-timezone times; gold_from_silver converts them.
        out = gold_from_silver(readings, stations, session_tz, emit_hours, model)
        # ...and reads naive times back the same way.
        ts = pd.to_datetime(out["timestamp"])
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert(session_tz).dt.tz_localize(None)
        return out.assign(timestamp=ts)[PREDICTION_COLUMNS]

    return window.withColumn("batch", F.lit(0)).groupBy("batch").applyInPandas(score, GOLD_SCHEMA)


# -------------------------------------------------------------------------
# 04. PUB FLASH-FLOOD ALERTS: archived as they arrive (the API keeps no history)
# -------------------------------------------------------------------------
# Separate from the rainfall tables: gold doesn't read these. They grow a record of PUB's live
# alerts, the start of a label stream that doesn't depend on news reports.
ALERT_PAYLOAD_SCHEMA = (
    "struct<data: struct<records: array<struct<datetime: string, updatedTimestamp: string, "
    "item: struct<identifier: string, msgType: string, references: string, status: string, "
    "readings: array<struct<headline: string, description: string, event: string, "
    "severity: string, area: struct<areaDesc: string, circle: array<double>>>>>>>>>"
)


@dlt.table(
    name="pub_flood_alerts_bronze",
    comment="Raw PUB flood-alert API responses (data.gov.sg) from the landing volume, one row per file.",
    table_properties={"quality": "bronze"},
)
def pub_flood_alerts_bronze():
    landing = spark.conf.get(
        "floodsense.alerts_landing_path", "/Volumes/workspace/floodsense/landing/flood_alerts"
    )
    schema_path = spark.conf.get(
        "floodsense.alerts_schema_path", "/Volumes/workspace/floodsense/autoloader/flood_alerts"
    )
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "text")
        .option("wholetext", "true")
        .option("cloudFiles.schemaLocation", schema_path)
        .load(landing)
        .select(
            F.col("value").alias("raw_payload_text"),
            F.col("_metadata.file_name").alias("source_file"),
            F.col("_metadata.file_modification_time").alias("ingested_at"),
        )
    )


@dlt.table(
    name="pub_flood_alerts_silver",
    comment="One row per PUB flash-flood alert or cancellation, with its location circle. "
    "Duplicates across overlapping polls are removed by (identifier, msg_type).",
    table_properties={"quality": "silver"},
)
@dlt.expect_or_drop("has_identifier", "identifier IS NOT NULL AND identifier != ''")
def pub_flood_alerts_silver():
    parsed = dlt.read("pub_flood_alerts_bronze").select(
        F.from_json("raw_payload_text", ALERT_PAYLOAD_SCHEMA).alias("p")
    )
    records = parsed.select(F.explode_outer("p.data.records").alias("r"))
    readings = records.select("r.datetime", "r.item", F.explode("r.item.readings").alias("reading"))
    return readings.select(
        F.to_timestamp("datetime").alias("issued_at"),
        F.col("item.identifier").alias("identifier"),
        F.col("item.msgType").alias("msg_type"),
        F.col("item.references").alias("references"),
        F.col("reading.headline").alias("headline"),
        F.col("reading.description").alias("description"),
        F.col("reading.severity").alias("severity"),
        F.col("reading.area.areaDesc").alias("area_desc"),
        F.col("reading.area.circle")[0].alias("latitude"),
        F.col("reading.area.circle")[1].alias("longitude"),
        F.col("reading.area.circle")[2].alias("radius_km"),
    ).dropDuplicates(["identifier", "msg_type"])
