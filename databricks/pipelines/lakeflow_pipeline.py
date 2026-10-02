"""
FloodSense - Databricks Lakeflow Declarative Pipeline.

Architecture (docs/phase5-handoff.md):
Landing volume -> Bronze (Auto Loader) -> Silver (payloads_to_readings) -> Gold (score_window)

Ground rules (from docs/phase5-handoff.md):
1. No feature or scoring logic in Spark. All transformations and ML inference call
   floodsense.serving.pipeline_core in pandas.
2. Never fabricate data. Invalid readings are dropped; malformed payloads are quarantined.
3. Missing is not dry: absent readings remain absent, not 0.0.

Pipeline configuration (all optional):
    floodsense.landing_path   UC volume Auto Loader watches (default /Volumes/floodsense/default/landing)
    floodsense.schema_path    Auto Loader schema location
    floodsense.gold_hours     hours of predictions gold keeps (default 24; it reads 72 h more)
"""

import json
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

from floodsense.serving.pipeline_core import (
    PREDICTION_COLUMNS,
    gold_from_silver,
    payloads_to_readings,
    silver_window_start,
)

# -------------------------------------------------------------------------
# Schemas
# -------------------------------------------------------------------------
READINGS_SCHEMA = StructType([
    StructField("station_id", StringType(), False),
    StructField("timestamp", TimestampType(), False),
    StructField("rainfall_mm", DoubleType(), False),
])

STATIONS_SCHEMA = StructType([
    StructField("station_id", StringType(), False),
    StructField("name", StringType(), False),
    StructField("latitude", DoubleType(), False),
    StructField("longitude", DoubleType(), False),
])

GOLD_SCHEMA = StructType([
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
])

PARSED_RESULT_SCHEMA = StructType([
    StructField("is_valid", DoubleType(), False),  # 1.0 = valid, 0.0 = error
    StructField("error_message", StringType(), True),
    StructField("readings", ArrayType(READINGS_SCHEMA), True),
    StructField("stations", ArrayType(STATIONS_SCHEMA), True),
])


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
    landing_volume = spark.conf.get("floodsense.landing_path", "/Volumes/floodsense/default/landing")
    schema_volume = spark.conf.get(
        "floodsense.schema_path", "/Volumes/floodsense/default/schema/bronze"
    )
    # Whole-file text: the payload is kept verbatim, and a malformed file can't fail ingestion
    # (it is parsed below and quarantined if it doesn't parse).
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "text")
        .option("cloudFiles.wholetext", "true")
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
@dlt.table(
    name="flood_risk_predictions_gold",
    comment="Calibrated flood probability and Low/Moderate/High tier per zone and 5-minute step, "
    "for the last floodsense.gold_hours, computed by pipeline_core.gold_from_silver.",
    table_properties={"quality": "gold"},
)
def flood_risk_predictions_gold():
    emit_hours = float(spark.conf.get("floodsense.gold_hours", "24"))
    silver = dlt.read("rainfall_readings_silver")
    newest = silver.agg(F.max("timestamp")).first()[0]
    if newest is None:
        return spark.createDataFrame(pd.DataFrame(columns=PREDICTION_COLUMNS), GOLD_SCHEMA)
    # Only the readings gold needs: emit window + 72 h warm-up. Bounded however long it runs.
    start = silver_window_start(pd.Timestamp(newest), emit_hours).to_pydatetime()
    readings = silver.filter(F.col("timestamp") >= F.lit(start)).toPandas()
    stations = dlt.read("weather_stations_silver").toPandas()
    # toPandas() returns naive times in the session timezone; gold_from_silver converts them.
    session_tz = spark.conf.get("spark.sql.session.timeZone", "UTC")
    scored = gold_from_silver(readings, stations, session_tz, emit_hours)
    return spark.createDataFrame(scored[PREDICTION_COLUMNS], GOLD_SCHEMA)
