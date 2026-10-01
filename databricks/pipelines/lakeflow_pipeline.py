"""
FloodSense - Databricks Lakeflow Declarative Pipeline.

Architecture (docs/phase5-handoff.md):
Landing volume -> Bronze (Auto Loader) -> Silver (payloads_to_readings) -> Gold (score_window)

Ground rules (from docs/phase5-handoff.md):
1. No feature or scoring logic in Spark. All transformations and ML inference call
   floodsense.serving.pipeline_core in pandas.
2. Never fabricate data. Invalid readings are dropped; malformed payloads are quarantined.
3. Missing is not dry: absent readings remain absent, not 0.0.
"""

import json
from typing import Any

import dlt
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    MapType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)
import pandas as pd

from floodsense.serving.pipeline_core import (
    PREDICTION_COLUMNS,
    READING_COLUMNS,
    STATION_COLUMNS,
    WARMUP,
    payloads_to_readings,
    score_window,
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
# 01. BRONZE LAYER: Raw Rainfall Ingestion (Auto Loader)
# -------------------------------------------------------------------------
@dlt.table(
    name="raw_rainfall_bronze",
    comment="Ingests raw NEA weather station rainfall JSON files from Unity Catalog landing volume.",
    table_properties={
        "quality": "bronze",
    },
)
def raw_rainfall_bronze():
    landing_volume = spark.conf.get(
        "floodsense.landing_path",
        "/Volumes/floodsense/default/landing",
    )
    schema_volume = spark.conf.get(
        "floodsense.schema_path",
        "/Volumes/floodsense/default/schema/bronze",
    )

    return (
        spark.readStream
        .format("cloudFiles")
        .option("cloudFiles.format", "text")
        .option("cloudFiles.wholetext", "true")
        .option("cloudFiles.schemaLocation", schema_volume)
        .option("cloudFiles.rescuedDataColumn", "_rescued_data")
        .load(landing_volume)
        .select(
            F.col("value").alias("raw_payload_text"),
            F.col("_metadata.file_name").alias("source_file"),
            F.col("_metadata.file_modification_time").alias("ingested_at"),
            F.col("_rescued_data"),
        )
    )


# -------------------------------------------------------------------------
# 02. SILVER LAYER: Parsing, Quarantine & Expectations
# -------------------------------------------------------------------------
@dlt.table(
    name="raw_payloads_quarantine",
    comment="Quarantine table for payloads that fail JSON decoding or schema validation.",
    table_properties={"quality": "quarantine"},
)
def raw_payloads_quarantine():
    bronze = dlt.read_stream("raw_rainfall_bronze")
    parsed = bronze.withColumn("parsed", parse_single_payload_udf(F.col("raw_payload_text")))
    return (
        parsed
        .filter(F.col("parsed.is_valid") == 0.0)
        .select(
            "source_file",
            "ingested_at",
            "raw_payload_text",
            F.col("parsed.error_message").alias("quarantine_reason"),
            "_rescued_data",
        )
    )


@dlt.table(
    name="weather_stations_silver",
    comment="Weather station metadata extracted from valid rainfall payloads.",
    table_properties={"quality": "silver"},
)
def weather_stations_silver():
    bronze = dlt.read_stream("raw_rainfall_bronze")
    parsed = bronze.withColumn("parsed", parse_single_payload_udf(F.col("raw_payload_text")))
    return (
        parsed
        .filter(F.col("parsed.is_valid") == 1.0)
        .select(F.explode("parsed.stations").alias("station"))
        .select(
            F.col("station.station_id").alias("station_id"),
            F.col("station.name").alias("name"),
            F.col("station.latitude").alias("latitude"),
            F.col("station.longitude").alias("longitude"),
        )
        .dropDuplicates(["station_id"])
    )


@dlt.table(
    name="rainfall_readings_silver",
    comment="Parsed 5-minute station rainfall readings in SGT (valid 0..100 mm).",
    table_properties={
        "quality": "silver",
        "pipelines.autoOptimize.zOrderCols": "timestamp,station_id",
    },
)
@dlt.expect_or_drop("valid_timestamp", "timestamp IS NOT NULL")
@dlt.expect_or_drop("valid_rainfall_range", "rainfall_mm >= 0.0 AND rainfall_mm <= 100.0")
def rainfall_readings_silver():
    bronze = dlt.read_stream("raw_rainfall_bronze")
    parsed = bronze.withColumn("parsed", parse_single_payload_udf(F.col("raw_payload_text")))
    return (
        parsed
        .filter(F.col("parsed.is_valid") == 1.0)
        .select(F.explode("parsed.readings").alias("reading"))
        .select(
            F.col("reading.station_id").alias("station_id"),
            F.col("reading.timestamp").alias("timestamp"),
            F.col("reading.rainfall_mm").alias("rainfall_mm"),
        )
        .dropDuplicates(["station_id", "timestamp"])
    )


# -------------------------------------------------------------------------
# 03. GOLD LAYER: Zone Features & Flood Risk Inference
# -------------------------------------------------------------------------
@dlt.table(
    name="flood_risk_predictions_gold",
    comment="Calibrated flood probabilities and Low/Moderate/High risk tiers computed by pipeline_core.",
    table_properties={
        "quality": "gold",
        "pipelines.autoOptimize.zOrderCols": "timestamp,ura_planning_area",
    },
)
def flood_risk_predictions_gold():
    """
    Gold scoring: reads silver readings and stations, takes the latest 72h window,
    and runs floodsense.serving.pipeline_core.score_window in pandas.
    """
    readings_df = dlt.read("rainfall_readings_silver").toPandas()
    stations_df = dlt.read("weather_stations_silver").toPandas()

    if readings_df.empty or stations_df.empty:
        # Return empty schema
        empty_pdf = pd.DataFrame(columns=PREDICTION_COLUMNS)
        return spark.createDataFrame(empty_pdf)

    # Convert timestamps to SGT for pipeline_core
    readings_df["timestamp"] = pd.to_datetime(readings_df["timestamp"])
    min_ts = readings_df["timestamp"].min()
    max_ts = readings_df["timestamp"].max()

    # If running the full replay window (warmup + display), emit from min_ts + WARMUP;
    # otherwise emit latest steps.
    emit_from = min_ts + WARMUP if (max_ts - min_ts) >= WARMUP else min_ts

    scored_pdf = score_window(
        readings=readings_df,
        stations=stations_df,
        emit_from=emit_from,
        emit_to=max_ts,
    )

    return spark.createDataFrame(scored_pdf[PREDICTION_COLUMNS])
