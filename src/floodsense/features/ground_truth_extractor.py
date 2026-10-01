"""
FloodSense - Ground Truth Flood Event Extractor.
Extracts, structures, and maps historical flood advisories and news alerts to URA Planning Areas.
Features:
1. Pydantic FloodEvent schema validation.
2. Dual pipeline: Databricks ai_query pattern + local keyword fallback parser.

Training labels do not come from here: sourced events live in data/reference/flood_events.csv
(``floodsense.data.flood_events.load_flood_events``) and are turned into labels by
``floodsense.labels.policy``.
"""

from datetime import datetime, timedelta
from typing import Literal

from floodsense.common.schemas import FloodEvent
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

# Known Singapore landmark / road to planning area lookup dictionary for fallback geocoder
ROAD_TO_ZONE_LOOKUP = {
    "dunearn": "BUKIT TIMAH",
    "bukit timah": "BUKIT TIMAH",
    "sixth avenue": "BUKIT TIMAH",
    "king albert park": "BUKIT TIMAH",
    "eng neo": "BUKIT TIMAH",
    "pasir ris": "PASIR RIS",
    "tampines": "TAMPINES",
    "bedok": "BEDOK",
    "changi": "CHANGI",
    "jurong town hall": "JURONG EAST",
    "boon lay": "BOON LAY",
    "jurong west": "JURONG WEST",
    "lakeside": "JURONG WEST",
    "clementi": "CLEMENTI",
    "bishan": "BISHAN",
    "braddell": "TOA PAYOH",
    "toa payoh": "TOA PAYOH",
    "kallang": "KALLANG",
    "geylang": "GEYLANG",
    "mountbatten": "MARINE PARADE",
    "tanjong katong": "MARINE PARADE",
    "queensway": "QUEENSTOWN",
    "commonwealth": "QUEENSTOWN",
    "hougang": "HOUGANG",
    "serangoon": "SERANGOON",
    "ang mo kio": "ANG MO KIO",
    "woodlands": "WOODLANDS",
    "yishun": "YISHUN",
    "choa chu kang": "CHOA CHU KANG",
    "bukit panjang": "BUKIT PANJANG",
}


class GroundTruthExtractor:
    """Extracts, standardizes and spatial-joins flood events into machine learning targets."""

    def parse_unstructured_alert(self, alert_text: str, timestamp_str: str) -> FloodEvent | None:
        """
        Local fallback extraction parsing PUB alert text and mapping to URA zone.
        In Databricks workspace, this logic is mirrored with ai_query('databricks-meta-llama-3-3-70b-instruct', ...).
        """
        text_lower = alert_text.lower()

        # Resolve planning area
        matched_zone = None
        for keyword, zone in ROAD_TO_ZONE_LOOKUP.items():
            if keyword in text_lower:
                matched_zone = zone
                break

        if not matched_zone:
            # Fallback scan directly on planning area names
            for z_name in URA_PLANNING_AREAS:
                if z_name.lower() in text_lower:
                    matched_zone = z_name
                    break

        if not matched_zone:
            return None

        # Severity detection
        severity: Literal["Minor", "Moderate", "Severe"]
        if any(w in text_lower for w in ["impassable", "submerged", "stranded", "severe"]):
            severity = "Severe"
        elif any(
            w in text_lower for w in ["moderate", "lanes affected", "slow traffic", "heavy water"]
        ):
            severity = "Moderate"
        else:
            severity = "Minor"

        try:
            ts = datetime.fromisoformat(timestamp_str)
        except Exception:
            ts = datetime.now()

        return FloodEvent(
            timestamp_start=ts,
            timestamp_end=ts + timedelta(hours=1, minutes=30),
            location_raw=alert_text.strip(),
            ura_planning_area=matched_zone,
            severity=severity,
            time_precision="exact",
            source_name="Parsed alert",
            geocoding_confidence=0.9,
        )

    def generate_databricks_ai_query_sql(self) -> str:
        """Returns the Databricks SQL query template using Serverless Llama 3.3 for Unity Catalog ingestion."""
        return """
        -- Databricks SQL ai_query Ground Truth Extraction
        CREATE OR REPLACE TABLE floodsense.silver.extracted_flood_events AS
        SELECT
          raw_id,
          alert_timestamp,
          raw_text,
          ai_query(
            'databricks-meta-llama-3-3-70b-instruct',
            CONCAT(
              'Extract flood location and severity as JSON with keys "location_raw", "ura_planning_area", "severity" (Minor/Moderate/Severe). Text: ',
              raw_text
            )
          ) AS extracted_json
        FROM floodsense.bronze.pub_raw_alerts;
        """
