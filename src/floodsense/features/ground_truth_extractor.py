"""
FloodSense - Ground Truth Flood Event Extractor.
Extracts, structures, and maps historical flood advisories and news alerts to URA Planning Areas.
Features:
1. Pydantic FloodEvent schema validation.
2. Dual pipeline: Databricks ai_query pattern + Local LLM/Regex fallback parser.
3. Shapely spatial mapping from point coordinate / road name to URA Planning Area.
4. Target label generation (flood_within_60min).
"""

from datetime import datetime, timedelta
from typing import Literal

import pandas as pd

from floodsense.common.config import settings
from floodsense.common.schemas import FloodEvent
from floodsense.common.timeutil import to_sgt
from floodsense.data.flood_events import load_flood_events
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

    def __init__(self):
        self.events: list[FloodEvent] = []
        self._load_curated_benchmark()

    def _load_curated_benchmark(self):
        if settings.flood_events_file.exists():
            self.events = load_flood_events(settings.flood_events_file)
        else:
            self.events = []

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

        event_id = f"{ts.strftime('%Y%m%d%H%M')}-{matched_zone.lower().replace(' ', '-')}"

        return FloodEvent(
            event_id=event_id,
            timestamp_start=ts,
            timestamp_end=ts + timedelta(hours=1, minutes=30),
            time_precision="approx_15min",
            location_raw=alert_text.strip(),
            ura_planning_area=matched_zone,
            severity=severity,
            cause="rain",
            source_url="https://t.me/pubfloodalerts",
            source_name="PUB Telegram",
            notes="Parsed from alert text",
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

    def attach_labels_to_feature_df(
        self,
        features_df: pd.DataFrame,
        lead_time_minutes: int = settings.prediction_lead_time_minutes,
    ) -> pd.DataFrame:
        """
        Attaches the binary target `flood_within_60min` to the feature DataFrame.
        For each row (zone j, time t), target = 1 if an active flood event starts or is active
        in zone j within [t, t + lead_time_minutes].
        """
        df = features_df.copy()
        df["flood_within_60min"] = 0

        # Build quick lookup per zone
        events_by_zone: dict[str, list[tuple[datetime, datetime]]] = {}
        for ev in self.events:
            start = ev.timestamp_start
            end = ev.timestamp_end or (start + timedelta(hours=1))
            events_by_zone.setdefault(ev.ura_planning_area, []).append((start, end))

        for idx, row in df.iterrows():
            zone = row["ura_planning_area"]
            raw_ts = pd.to_datetime(row["timestamp"])
            ts = to_sgt(raw_ts.to_pydatetime() if hasattr(raw_ts, "to_pydatetime") else raw_ts)

            if zone not in events_by_zone:
                continue

            # Check if any event falls in [ts, ts + lead_time_minutes]
            window_end = ts + timedelta(minutes=lead_time_minutes)
            for ev_start, ev_end in events_by_zone[zone]:
                # Overlap check
                if (ts <= ev_start <= window_end) or (ev_start <= ts <= ev_end):
                    df.at[idx, "flood_within_60min"] = 1
                    break

        return df
