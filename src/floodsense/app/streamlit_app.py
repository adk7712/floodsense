"""
FloodSense - Streamlit dashboard.

Two modes:
- Replay: real NEA 5-minute station readings for any day in the rainfall store (2017 onward),
  scrubbed in time, with the major reported storms one click away. Without the store, the
  recorded 17 April 2021 storm only.
- Live: the latest readings from data.gov.sg, with a few hours of history for rolling features.

Zone features are recomputed from the readings for every view (and cached by input). Nothing is
accumulated across reruns, so the page depends only on the selected mode, time and zone.
"""

import html
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from shapely.geometry import mapping, shape

from floodsense.app import replay_days
from floodsense.app.resident_view import (
    RESIDENT_CSS,
    areas_at_risk,
    escape_markdown,
    pct,
    render_resident_view,
    reported_floods_line,
    show_pub_alerts,
)
from floodsense.common.config import settings
from floodsense.common.schemas import FloodEvent, StationMetadata
from floodsense.data.flood_events import load_flood_events
from floodsense.data.replay import load_replay
from floodsense.data.transport import SOURCE_NAME as MRT_SOURCE
from floodsense.data.transport import pin_zone_stations, stations_at_risk
from floodsense.features.zone_features import (
    compute_zone_feature_table,
    newest_complete_step,
    step_counts,
)
from floodsense.ingestion.flood_alerts import FloodAlert, active_alerts, fetch_flood_alerts
from floodsense.ingestion.poller import LiveFeedUnavailable, NEAPoller
from floodsense.models.artifact import load_model, rarity_scores
from floodsense.models.scoring import (
    NO_DATA_TIER,
    default_thresholds,
    score_zone_features,
)
from floodsense.radar.decode import COLOUR_SCALE
from floodsense.radar.live import RadarView, forecast_features, radar_view
from floodsense.serving import genie
from floodsense.serving.coverage import coverage, withhold_tier
from floodsense.serving.gold import GoldUnavailable, latest_zone_risk, warehouse_id
from floodsense.spatial.singapore_geo import (
    URA_PLANNING_AREAS,
    area_from_slug,
    load_station_snapshot,
)

log = logging.getLogger(__name__)

st.set_page_config(
    page_title="FloodSense | Urban Drainage Intelligence",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="auto",
)

if "theme" not in st.session_state:
    st.session_state["theme"] = "light"

# Synchronize dark mode switch and theme state
if "dark_mode_switch" in st.session_state and st.session_state["dark_mode_switch"] != (
    st.session_state["theme"] == "dark"
):
    st.session_state["theme"] = "dark" if st.session_state["dark_mode_switch"] else "light"
else:
    st.session_state["dark_mode_switch"] = st.session_state["theme"] == "dark"

IS_DARK = st.session_state["theme"] == "dark"

THEME_TOKENS = {
    "dark": {
        "bg_canvas": "#090D16",
        "bg_surface": "#111827",
        "bg_subtle": "#1A2234",
        "bg_hover": "#222D42",
        "border_main": "#2A3650",
        "border_subtle": "#162032",
        "text_main": "#F8FAFC",
        "text_muted": "#94A3B8",
        "text_dim": "#64748B",
        "accent": "#38BDF8",
        "primary": "#FB923C",
        "primary_hover": "#F97316",
        "accent_hover": "#0EA5E9",
        "accent_muted": "rgba(56, 189, 248, 0.12)",
        "card_shadow": "0 1px 3px 0 rgba(0, 0, 0, 0.4)",
        "map_style": "carto-darkmatter",
        "grid_color": "rgba(255, 255, 255, 0.08)",
        "gauge_tick": "#64748B",
        "gauge_num": "#F8FAFC",
        "badge_border": "#27272A",
        "badge_bg": "#1E293B",
        "badge_text": "#CBD5E1",
        "stat_tile_bg": "#151F30",
        "stat_tile_border": "#233047",
        "gauge_step_0_70": "rgba(56, 189, 248, 0.18)",
        "gauge_step_70_90": "rgba(245, 158, 11, 0.22)",
        "gauge_step_90_100": "rgba(239, 68, 68, 0.25)",
    },
    "light": {
        "bg_canvas": "#F8FAFC",
        "bg_surface": "#FFFFFF",
        "bg_subtle": "#F1F5F9",
        "bg_hover": "#F8FAFC",
        "border_main": "#E2E8F0",
        "border_subtle": "#F1F5F9",
        "text_main": "#0F172A",
        "text_muted": "#64748B",
        "text_dim": "#94A3B8",
        "accent": "#0284C7",
        "primary": "#EA580C",
        "primary_hover": "#C2410C",
        "accent_hover": "#0369A1",
        "accent_muted": "rgba(2, 132, 199, 0.08)",
        "card_shadow": "0 1px 3px 0 rgba(0, 0, 0, 0.04), 0 1px 2px -1px rgba(0, 0, 0, 0.04)",
        "map_style": "carto-positron",
        "grid_color": "rgba(0, 0, 0, 0.06)",
        "gauge_tick": "#94A3B8",
        "gauge_num": "#0F172A",
        "badge_border": "#E2E8F0",
        "badge_bg": "#EEF2F6",
        "badge_text": "#475569",
        "stat_tile_bg": "#F8FAFC",
        "stat_tile_border": "#E2E8F0",
        "gauge_step_0_70": "#E0F2FE",
        "gauge_step_70_90": "#FEF3C7",
        "gauge_step_90_100": "#FEE2E2",
    },
}

tokens = THEME_TOKENS["dark" if IS_DARK else "light"]
c_muted = tokens["text_muted"]
c_main = tokens["text_main"]
c_accent = tokens["accent"]
c_subtle = tokens["bg_subtle"]
c_surface = tokens["bg_surface"]
c_border = tokens["border_main"]
c_border_subtle = tokens["border_subtle"]
c_dim = tokens["text_dim"]
c_badge_bg = tokens["badge_bg"]
c_badge_border = tokens["badge_border"]
c_badge_text = tokens["badge_text"]
c_stat_bg = tokens["stat_tile_bg"]
c_stat_border = tokens["stat_tile_border"]
c_accent_muted = tokens["accent_muted"]

MODERN_TELEMETRY_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

:root {{
    --bg-canvas: {tokens["bg_canvas"]};
    --bg-surface: {tokens["bg_surface"]};
    --bg-subtle: {tokens["bg_subtle"]};
    --bg-hover: {tokens["bg_hover"]};
    --border-main: {tokens["border_main"]};
    --border-subtle: {tokens["border_subtle"]};
    --text-main: {tokens["text_main"]};
    --text-muted: {tokens["text_muted"]};
    --text-dim: {tokens["text_dim"]};
    --accent: {tokens["accent"]};
    --accent-hover: {tokens["accent_hover"]};
    --accent-muted: {tokens["accent_muted"]};
    --primary: {tokens["primary"]};
    --primary-hover: {tokens["primary_hover"]};
    --card-shadow: {tokens["card_shadow"]};
    --radius-card: 12px;
}}

/* --- 1. COMPLETELY HIDE STREAMLIT CHROME ON TOP --- */
header[data-testid="stHeader"] {{
    background: transparent !important;
    height: 0px !important;
    min-height: 0px !important;
    padding: 0px !important;
    border: none !important;
    pointer-events: none !important;
    overflow: visible !important;
    z-index: 999999 !important;
}}

#MainMenu,
footer,
.stDeployButton,
.stAppDeployButton,
[data-testid="stAppDeployButton"],
[data-testid="stMainMenu"],
[data-testid="stDecoration"],
[data-testid="stToolbarActions"],
div[data-testid="stSidebarNav"] {{
    display: none !important;
    height: 0px !important;
    width: 0px !important;
    opacity: 0 !important;
    visibility: hidden !important;
    pointer-events: none !important;
}}


div[data-testid="stToolbar"] {{
    background: transparent !important;
    height: 0px !important;
    overflow: visible !important;
    pointer-events: none !important;
}}

/* Ensure sidebar expand control floats cleanly when sidebar is collapsed */
div[data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"] {{
    pointer-events: auto !important;
    display: flex !important;
    position: fixed !important;
    top: 14px !important;
    left: 14px !important;
    z-index: 1000000 !important;
    background: var(--bg-surface) !important;
    border: 1px solid var(--border-main) !important;
    border-radius: 8px !important;
    box-shadow: var(--card-shadow) !important;
    color: var(--text-main) !important;
    width: 32px !important;
    height: 32px !important;
    align-items: center !important;
    justify-content: center !important;
    cursor: pointer !important;
    transition: all 0.15s ease !important;
}}

/* The "Settings" button is the last item in the sidebar, at the panel's bottom; it scrolls with
   the panel. Its toggles open to the right, as one piece with the button. */
[data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"]:has(> [data-testid="stLayoutWrapper"] > .st-key-advanced_menu) {{
    min-height: calc(100dvh - 2.25rem) !important;  /* the panel's height below its header */
}}
[data-testid="stSidebarUserContent"] {{
    padding-bottom: 0 !important;  /* Settings keeps its own space below */
}}
[data-testid="stLayoutWrapper"]:has(> .st-key-advanced_menu) {{
    order: 999 !important;  /* drawn early in the script, shown last in the sidebar */
    margin-top: auto !important;  /* at the panel's bottom when the sidebar is short */
    padding: 1rem 0 1.25rem 0 !important;
}}
.st-key-advanced_menu {{
    width: max-content !important;
    flex-wrap: nowrap !important;
    /* The button keeps its place when it opens: the taller toggle panel grows upwards beside it */
    align-items: flex-end !important;
    gap: 0 !important;
}}
.st-key-advanced_menu button {{
    height: 36px !important;
    min-height: 36px !important;
    padding: 0 12px !important;
    font-size: 0.8rem !important;
}}
.st-key-advanced_btn_open button {{
    border-top-right-radius: 0 !important;
    border-bottom-right-radius: 0 !important;
}}
.st-key-advanced_ext {{
    min-height: 36px !important;
    width: max-content !important;
    display: flex !important;
    justify-content: center !important;
    padding: 6px 12px !important;
    background-color: var(--bg-subtle) !important;
    border: 1px solid var(--border-main) !important;
    border-left: none !important;
    border-radius: 0 8px 8px 0 !important;
}}
.st-key-advanced_ext label, .st-key-advanced_ext p {{
    color: var(--text-main) !important;
    font-size: 0.8rem !important;
    white-space: nowrap !important;
}}

/* The close button mirrors the open button: same box, always visible, 14px from the corner */
[data-testid="stSidebarContent"] {{
    position: relative !important;  /* anchor for the close button: the panel's own edges */
}}
[data-testid="stSidebarHeader"] {{
    position: static !important;
}}
[data-testid="stSidebarCollapseButton"] {{
    visibility: visible !important;
    display: flex !important;
    position: absolute !important;
    top: 14px !important;
    right: 11px !important;  /* 14px from the panel edge, past its 3px resize strip */
    z-index: 1000 !important;
}}
[data-testid="stSidebarCollapseButton"] button {{
    background: var(--bg-surface) !important;
    border: 1px solid var(--border-main) !important;
    border-radius: 8px !important;
    box-shadow: var(--card-shadow) !important;
    width: 32px !important;
    height: 32px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    padding: 0 !important;
}}
[data-testid="stSidebarCollapseButton"] span {{
    color: var(--text-main) !important;
}}
[data-testid="stExpandSidebarButton"] svg,
[data-testid="stExpandSidebarButton"] span {{
    color: var(--text-main) !important;
}}

/* Phones: the open sidebar is a full-screen menu over the page (Streamlit keeps it in the page
   flow with a fixed width, which squeezes the page into a thin strip). Closing it returns to the
   page and its floating open button. Same breakpoint as the phone rules further down. */
@media (max-width: 640px) {{
    section[data-testid="stSidebar"][aria-expanded="true"] {{
        position: fixed !important;
        top: 0 !important;
        left: 0 !important;
        width: 100vw !important;
        min-width: 100vw !important;
        max-width: 100vw !important;
        height: 100vh !important;
        height: 100dvh !important;
        z-index: 999991 !important;
        border-right: none !important;
        overflow-x: hidden !important;
    }}
    section[data-testid="stSidebar"][aria-expanded="true"] [data-testid="stSidebarContent"] {{
        width: 100% !important;
        height: 100% !important;
        overflow-x: hidden !important;
    }}
    /* No dragging the panel narrower on a phone */
    section[data-testid="stSidebar"] div:has(> [data-testid="stSidebarResizeHandle"]) {{
        display: none !important;
    }}
    /* The close button sits at the screen's top right corner, clear of the edge */
    section[data-testid="stSidebar"][aria-expanded="true"] [data-testid="stSidebarCollapseButton"] {{
        right: 14px !important;
    }}
    /* Settings: the toggles wrap below the button instead of running off the screen */
    .st-key-advanced_menu {{
        width: auto !important;
        max-width: 100% !important;
        flex-wrap: wrap !important;
    }}
    .st-key-advanced_ext {{
        width: auto !important;
        max-width: 100% !important;
    }}
    .st-key-advanced_ext label, .st-key-advanced_ext p {{
        white-space: normal !important;
    }}
}}
/* Very narrow phones: the toggles no longer fit beside the button, so they stand alone below it */
@media (max-width: 360px) {{
    .st-key-advanced_menu {{ flex-direction: column !important; align-items: stretch !important; }}
    .st-key-advanced_btn_open button {{ border-radius: 8px !important; }}
    .st-key-advanced_ext {{
        border-left: 1px solid var(--border-main) !important;
        border-radius: 8px !important;
        margin-top: 4px !important;
    }}
}}

/* Bordered containers (cards, panels): Streamlit draws their border from the light theme's
   text colour, which disappears on the dark canvas, so use the theme's own border colour. */
[data-testid="stMain"] [data-testid="stVerticalBlock"] {{
    border-color: var(--border-main) !important;
}}

/* Expanders: Streamlit paints the open header from the light theme; use the page's colours. */
[data-testid="stExpander"] details,
[data-testid="stExpander"] summary {{
    background-color: var(--bg-surface) !important;
    border-color: var(--border-main) !important;
}}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary * {{
    color: var(--text-main) !important;
}}
[data-testid="stExpander"] summary:hover {{
    background-color: var(--bg-subtle) !important;
}}

/* Date-picker calendar popup: Streamlit paints it from the light theme while the page's text
   colour follows the toggle, which left the day numbers invisible in dark mode. */
[data-testid="stDateInputCalendar"] {{
    background-color: var(--bg-surface) !important;
    border: 1px solid var(--border-main) !important;
    color: var(--text-main) !important;
}}
[data-testid="stDateInputCalendar"] *:not(svg):not(path) {{
    background-color: transparent;
    color: var(--text-main);
}}
[data-testid="stDateInputCalendar"] [data-outside-month],
[data-testid="stDateInputCalendar"] [data-disabled],
[data-testid="stDateInputCalendar"] [aria-disabled="true"] {{
    color: var(--text-dim) !important;
}}
[data-testid="stDateInputCalendar"] svg {{
    color: var(--text-main) !important;
    fill: currentColor;
}}

/* Widget labels in the main area (e.g. "Radar") follow the theme, like the sidebar's. */
[data-testid="stMain"] [data-testid="stWidgetLabel"] p {{
    color: var(--text-main) !important;
}}

/* Toggle switches: the off-state track came from the light theme and vanished in dark mode. */
[data-testid="stCheckbox"] label:not([data-selected]) > span:has(input[role="switch"]) + div {{
    background-color: var(--text-dim) !important;
}}

/* Map view switch and its timing switch: same width, equal centred buttons, joined as one unit. */
div.st-key-map_view,
div.st-key-map_subview div[data-testid="stElementContainer"] {{
    width: 250px !important;
}}
div.st-key-map_view div[data-testid="stButtonGroup"],
div.st-key-map_subview div[data-testid="stButtonGroup"] {{
    width: 250px !important;
    box-sizing: border-box !important;
}}
div.st-key-map_view div[role="radiogroup"],
div.st-key-map_subview div[role="radiogroup"] {{
    display: flex !important;
    width: 100% !important;
    max-width: none !important;
}}
div.st-key-map_view div[role="radiogroup"] > button,
div.st-key-map_subview div[role="radiogroup"] > button {{
    flex: 1 1 0 !important;
    justify-content: center !important;
    text-align: center !important;
}}
div.st-key-map_view div[data-testid="stButtonGroup"] {{
    border-bottom-left-radius: 0 !important;
    border-bottom-right-radius: 0 !important;
}}
div.st-key-map_subview div[data-testid="stButtonGroup"] {{
    border-top: none !important;
    border-top-left-radius: 0 !important;
    border-top-right-radius: 0 !important;
}}
div.st-key-map_subview {{
    margin-top: -1rem !important;  /* sit directly under the main switch (no block gap) */
}}

/* Map attribution: just the (i) button, the text shows on hover. */
.maplibregl-ctrl-attrib .maplibregl-ctrl-attrib-inner,
.mapboxgl-ctrl-attrib .mapboxgl-ctrl-attrib-inner {{
    display: none !important;
}}
.maplibregl-ctrl-attrib:hover .maplibregl-ctrl-attrib-inner,
.mapboxgl-ctrl-attrib:hover .mapboxgl-ctrl-attrib-inner {{
    display: block !important;
}}

/* Slim legend bars under the map (radar colours and risk levels share the same dimensions). */
.fs-legend {{ margin: 10px 0 2px 0; font-family: 'Inter', sans-serif; }}
.fs-legend-title {{
    font-size: 0.66rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
    color: var(--text-muted); margin-bottom: 4px;
}}
.fs-legend-bar {{ height: 10px; width: 100%; border-radius: 5px; display: flex; overflow: hidden; }}
.fs-legend-ticks {{ position: relative; height: 16px; font-size: 0.68rem; color: var(--text-muted); }}
.fs-legend-ticks span {{ position: absolute; top: 2px; transform: translateX(-50%); white-space: nowrap; }}

/* The map's secondary switch (Now / In 1 hour, or Now / +30 / +60) is smaller than the main one. */
div.st-key-map_subview div[data-testid="stButtonGroup"] button {{
    padding: 2px 10px !important;
    min-height: 0 !important;
}}
div.st-key-map_subview div[data-testid="stButtonGroup"] button p {{
    font-size: 0.74rem !important;
}}
.st-key-map_subview {{
    margin-top: -6px !important;
}}

/* Map card and zone card: same height (the shorter stretches to the taller). */
div[data-testid="stHorizontalBlock"]:has(.st-key-map_card) {{
    align-items: stretch !important;
}}
div[data-testid="stHorizontalBlock"]:has(.st-key-map_card) > div[data-testid="stColumn"] > div,
div[data-testid="stLayoutWrapper"]:has(> .st-key-map_card),
div[data-testid="stLayoutWrapper"]:has(> .st-key-zone_card),
.st-key-map_card,
.st-key-zone_card {{
    height: 100% !important;
}}

/* Page container */
.block-container {{
    padding-top: 0.4rem !important;
    padding-bottom: 2.75rem !important;
    padding-left: 2.25rem !important;
    padding-right: 2.25rem !important;
    max-width: 1420px !important;
}}

/* Responsive metric cards grid on smaller viewports */
@media (max-width: 900px) {{
    div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) {{
        flex-wrap: wrap !important;
        gap: 0.5rem !important;
    }}
    div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > div {{
        min-width: 140px !important;
        flex: 1 1 calc(33.333% - 0.5rem) !important;
    }}
}}
@media (max-width: 600px) {{
    div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > div {{
        min-width: 130px !important;
        flex: 1 1 calc(50% - 0.5rem) !important;
    }}
}}

/* --- 2. GLOBAL APP CANVAS & TYPOGRAPHY --- */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stApp"], .main, section[data-testid="stMain"] {{
    background-color: var(--bg-canvas) !important;
    color: var(--text-main) !important;
    font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
}}

/* Sidebar */
section[data-testid="stSidebar"] {{
    background-color: var(--bg-surface) !important;
    border-right: 1px solid var(--border-main) !important;
}}

section[data-testid="stSidebar"] * {{
    color: var(--text-main);
}}

section[data-testid="stSidebar"] .stMarkdown h3 {{
    font-family: 'Inter', sans-serif !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em !important;
    color: var(--text-main) !important;
    font-size: 1.05rem !important;
}}

/* Headings */
h1, h2, h3, h4, h5, h6 {{
    font-family: 'Inter', system-ui, sans-serif !important;
    color: var(--text-main) !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em !important;
}}

h1 {{
    font-size: 1.6rem !important;
    margin-bottom: 0.2rem !important;
    padding-bottom: 0px !important;
    border-bottom: none !important;
    letter-spacing: -0.02em !important;
}}

h2, h3 {{
    font-size: 1.1rem !important;
    color: var(--text-main) !important;
}}

/* --- 3. ENTERPRISE CARDS --- */
div[data-testid="stVerticalBlockBorderWrapper"] > div {{
    background-color: var(--bg-surface) !important;
    border: 1px solid var(--border-main) !important;
    border-radius: var(--radius-card) !important;
    box-shadow: var(--card-shadow) !important;
    padding: 1.15rem 1.25rem !important;
}}

/* Top border accent on Diagnostic Card matches active telemetry theme */
div[data-testid="stColumn"]:nth-of-type(2) div[data-testid="stVerticalBlockBorderWrapper"] > div {{
    border-top: 3px solid var(--accent) !important;
}}

/* Rarity gauge: no element toolbar */
.st-key-rarity_gauge [data-testid="stElementToolbar"] {{
    display: none !important;
}}

/* --- 4. TOP KPI METRIC CARDS --- */
[data-testid="stMetric"] {{
    background-color: transparent !important;
    padding: 0px !important;
}}

[data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] p,
[data-testid="stMetricLabel"] div {{
    font-family: 'Inter', sans-serif !important;
    font-size: clamp(0.65rem, 0.75vw, 0.72rem) !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
    color: var(--text-muted) !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    white-space: nowrap !important;
}}

[data-testid="stMetricValue"] {{
    font-family: 'Inter', system-ui, sans-serif !important;
    font-size: clamp(1.25rem, 1.8vw, 2rem) !important;
    font-weight: 800 !important;
    color: var(--text-main) !important;
    font-variant-numeric: tabular-nums !important;
    letter-spacing: -0.02em !important;
    line-height: 1.15 !important;
}}

[data-testid="stMetricDelta"] {{
    font-family: 'Inter', sans-serif !important;
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    font-variant-numeric: tabular-nums !important;
}}

/* --- 5. BUTTONS --- */
button[kind="primary"],
[data-testid="baseButton-primary"] {{
    background-color: var(--primary) !important;
    border: 1px solid var(--primary) !important;
    color: #FFFFFF !important;
    border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
    box-shadow: 0 1px 2px rgba(234, 88, 12, 0.2) !important;
    transition: all 0.15s ease !important;
}}

button[kind="primary"]:hover,
[data-testid="baseButton-primary"]:hover {{
    background-color: var(--primary-hover) !important;
    border-color: var(--primary-hover) !important;
    color: #FFFFFF !important;
}}

button[kind="secondary"],
[data-testid="baseButton-secondary"] {{
    background-color: var(--bg-subtle) !important;
    border: 1px solid var(--border-main) !important;
    color: var(--text-main) !important;
    border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.85rem !important;
    box-shadow: none !important;
    transition: all 0.15s ease !important;
}}

button[kind="secondary"]:hover,
[data-testid="baseButton-secondary"]:hover {{
    background-color: var(--bg-hover) !important;
    border-color: var(--border-main) !important;
    color: var(--text-main) !important;
}}

/* --- 5.1 TOP-RIGHT THEME TOGGLE SWITCH --- */
/* Anchored to the top of the page (not the screen), so it scrolls away with the content */
[data-testid="stMain"] {{
    position: relative !important;
}}
.st-key-top_controls {{
    position: absolute !important;
    top: 0.7rem !important;
    right: 1.25rem !important;
    width: auto !important;
    z-index: 999990 !important;
    flex-wrap: nowrap !important;
}}
/* Sidebar switches (Analyst / Resident, Live / Replay): equal halves, same size, stacked close */
.st-key-sidebar_switches [data-testid="stElementContainer"],
.st-key-sidebar_switches div[data-testid="stButtonGroup"] {{
    width: 100% !important;
}}
.st-key-sidebar_switches div[role="radiogroup"] {{
    display: flex !important;
    width: 100% !important;
    max-width: none !important;
}}
.st-key-sidebar_switches div[role="radiogroup"] > button {{
    flex: 1 1 0 !important;
    justify-content: center !important;
    min-width: 0 !important;
    padding-left: 6px !important;
    padding-right: 6px !important;
    font-size: 0.82rem !important;
}}
.st-key-ask_genie button {{
    box-shadow: var(--card-shadow) !important;
}}
.st-key-telegram_join a.fs-telegram {{
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.42rem 0.95rem;
    border-radius: 0.5rem;
    background: #229ED9;
    color: #ffffff !important;
    font-size: 0.9rem;
    font-weight: 600;
    line-height: 1.4;
    white-space: nowrap;
    text-decoration: none !important;
    box-shadow: var(--card-shadow);
    transition: background 0.15s ease;
}}
.st-key-telegram_join a.fs-telegram:hover {{ background: #1B86B8; }}
.st-key-telegram_join a.fs-telegram svg {{ width: 1.25rem; height: 1.25rem; flex: none; }}

.st-key-app_header h1 {{
    padding-top: 0 !important;  /* level with the sidebar's "FloodSense" */
}}

/* Less empty space above the sidebar's brand (keeps the collapse button) */
[data-testid="stSidebarHeader"] {{
    height: 2.25rem !important;
    min-height: 2.25rem !important;
    margin-bottom: 0 !important;
    padding-top: 0.5rem !important;
    padding-bottom: 0 !important;
}}

div[data-testid="stToggle"] {{
    display: inline-flex !important;
    justify-content: flex-end !important;
    align-items: center !important;
    margin-bottom: 0px !important;
}}

div[data-testid="stToggle"] label {{
    font-family: 'Inter', sans-serif !important;
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    color: var(--text-main) !important;
    cursor: pointer !important;
}}

div[data-testid="stToggle"] label p {{
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    color: var(--text-main) !important;
    margin: 0 !important;
}}

/* --- 6. SEGMENTED CONTROL --- */
/* Line the "Mode" label up with the first option's text (button padding + border). */
section[data-testid="stSidebar"] [data-testid="stButtonGroup"] [data-testid="stWidgetLabel"] {{
    padding-left: 15px !important;
}}

div[data-testid="stSegmentedControl"],
.stButtonGroup {{
    background-color: var(--bg-subtle) !important;
    border: 1px solid var(--border-main) !important;
    border-radius: 10px !important;
    padding: 3px !important;
}}

button[data-variant="segmented_control"],
button[data-testid="stButtonGroupButton"] {{
    border-radius: 7px !important;
    border: 1px solid transparent !important;
    background-color: transparent !important;
    color: var(--text-muted) !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    padding: 5px 14px !important;
    transition: all 0.15s ease !important;
}}

button[data-testid="stButtonGroupButton"][aria-pressed="true"] {{
    background-color: var(--primary) !important;
    color: {"#111827" if IS_DARK else "#FFFFFF"} !important;
    border: 1px solid var(--primary) !important;
}}

button[data-variant="segmented_control"]:hover,
button[data-testid="stButtonGroupButton"]:hover {{
    color: var(--text-main) !important;
}}

/* --- 7. INPUTS & SELECTBOXES (BaseWeb & React-Aria) --- */
/* Selectbox / ComboBox */
div[data-baseweb="select"] > div,
div[data-testid="stSelectbox"] div[data-rac][role="group"],
div.react-aria-ComboBox div[data-rac][role="group"] {{
    background-color: var(--bg-surface) !important;
    border: 1px solid var(--border-main) !important;
    border-radius: 8px !important;
    color: var(--text-main) !important;
    font-size: 0.875rem !important;
    font-family: 'Inter', sans-serif !important;
    transition: border-color 0.15s ease !important;
}}

div[data-testid="stSelectbox"] div[data-rac][role="group"]:focus-within,
div.react-aria-ComboBox div[data-rac][role="group"]:focus-within {{
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 1px var(--accent) !important;
}}

div[data-testid="stSelectbox"] input,
div.react-aria-ComboBox input {{
    background-color: transparent !important;
    color: var(--text-main) !important;
    font-size: 0.875rem !important;
    font-family: 'Inter', sans-serif !important;
}}

div[data-testid="stSelectbox"] button svg,
div.react-aria-ComboBox button svg {{
    fill: var(--text-muted) !important;
}}

/* DateInput & DateField */
div[data-baseweb="input"],
div[data-testid="stDateInput"] div[data-testid="stDateInputField"],
div.react-aria-DateField,
div.react-aria-DateField [role="group"] {{
    background-color: var(--bg-surface) !important;
    border: 1px solid var(--border-main) !important;
    border-radius: 8px !important;
    color: var(--text-main) !important;
}}

div[data-baseweb="input"] input,
div[data-testid="stDateInput"] input {{
    background-color: transparent !important;
    color: var(--text-main) !important;
    font-size: 0.875rem !important;
    font-family: 'JetBrains Mono', monospace !important;
}}

div[data-testid="stDateInput"] span[role="spinbutton"],
div[data-testid="stDateInput"] span[data-rac] {{
    color: var(--text-main) !important;
    background-color: transparent !important;
    font-family: 'JetBrains Mono', monospace !important;
}}

/* Dropdown popover & Listbox */
div[data-baseweb="popover"],
div.react-aria-Popover,
div[data-rac][role="listbox"],
ul[role="listbox"] {{
    background-color: var(--bg-surface) !important;
    border: 1px solid var(--border-main) !important;
    border-radius: 8px !important;
    box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.25), 0 8px 10px -6px rgba(0, 0, 0, 0.2) !important;
    color: var(--text-main) !important;
}}

li[role="option"],
div[data-rac][role="option"] {{
    color: var(--text-main) !important;
    font-size: 0.875rem !important;
    border-radius: 6px !important;
    font-family: 'Inter', sans-serif !important;
    padding: 6px 12px !important;
    background-color: transparent !important;
}}

li[role="option"]:hover,
div[data-rac][role="option"]:hover,
div[data-rac][role="option"][data-focused="true"] {{
    background-color: var(--bg-subtle) !important;
    color: var(--accent) !important;
}}

li[role="option"][aria-selected="true"],
div[data-rac][role="option"][aria-selected="true"] {{
    background-color: var(--bg-hover) !important;
    color: var(--accent) !important;
    font-weight: 600 !important;
}}

/* Tooltips */
[data-testid="stTooltipIcon"] button svg {{
    stroke: var(--text-muted) !important;
}}

/* --- 8. SLIDERS: TELEMETRY ACCENT --- */
div[data-testid="stSlider"] div[role="slider"],
div[data-testid="stSelectSlider"] div[role="slider"],
div[data-testid="stSlider"] div[role="group"] > div > div,
div[data-testid="stSelectSlider"] div[role="group"] > div > div {{
    border-radius: 9999px !important;
    background-color: var(--primary) !important;
}}

div[data-testid="stSlider"] div[data-baseweb="slider"] div,
div[data-testid="stSelectSlider"] div[data-baseweb="slider"] div {{
    background-color: var(--primary) !important;
}}

/* The min/max labels shown while the slider has focus are text, not track. */
div[data-testid="stSlider"] div[role="group"] > div > div[data-testid="stSliderTickBar"],
div[data-testid="stSlider"] div[role="group"] > div > div[data-testid="stSliderTickBar"] * {{
    background-color: transparent !important;
    color: var(--text-muted) !important;
}}

/* --- 9. CODE & TELEMETRY --- */
code {{
    font-family: 'JetBrains Mono', monospace !important;
    background-color: var(--bg-subtle) !important;
    border: 1px solid var(--border-main) !important;
    color: var(--accent) !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
    font-size: 0.85em !important;
}}

.stMarkdown code {{
    white-space: nowrap !important;
    display: inline-block !important;
}}

.stMarkdown ul li {{
    white-space: normal !important;
    line-height: 1.5 !important;
    margin-bottom: 0.35rem !important;
}}

/* --- 10. ALERTS & DIVIDERS --- */
div[data-testid="stAlert"] {{
    border-radius: 8px !important;
    border: 1px solid var(--border-main) !important;
    background-color: var(--bg-surface) !important;
    color: var(--text-main) !important;
}}

hr {{
    border: none !important;
    border-top: 1px solid var(--border-main) !important;
    margin: 1.25rem 0 !important;
}}
/* Tighter divider under the sidebar switches, closer to "Zone Diagnostic" */
[data-testid="stSidebar"] hr {{
    margin: 0.5rem 0 !important;
}}

.stCaption, [data-testid="stCaptionContainer"] {{
    color: var(--text-muted) !important;
    font-size: 0.78rem !important;
}}

/* Every switch (Live / Replay, Analyst / Resident, the map's layer and timing): the chosen option
   is the orange button, like the other buttons. "Settings" is orange while its toggle is open. */
div[data-testid="stButtonGroup"] button[aria-pressed="true"],
div[data-testid="stButtonGroup"] button[aria-checked="true"],
button[data-variant="segmented_control"][aria-checked="true"],
button[data-variant="segmented_control"][aria-pressed="true"],
.st-key-advanced_btn_open button {{
    background-color: var(--primary) !important;
    border-color: var(--primary) !important;
    box-shadow: none !important;
}}
.st-key-advanced_btn_open button:hover {{
    background-color: var(--primary-hover) !important;
    border-color: var(--primary-hover) !important;
}}
div[data-testid="stButtonGroup"] button[aria-pressed="true"] *,
div[data-testid="stButtonGroup"] button[aria-checked="true"] *,
button[data-variant="segmented_control"][aria-checked="true"] *,
button[data-variant="segmented_control"][aria-pressed="true"] *,
.st-key-advanced_btn_open button * {{
    color: {"#111827" if IS_DARK else "#FFFFFF"} !important;
}}

/* KPI labels: one line with an ellipsis on wide screens (wrapping on phones, below). */
.fs-kpi-label {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
/* The map legend's "no data" key. */
.fs-legend-nodata {{ margin-top: 2px; font-size: 0.68rem; color: var(--text-muted); }}
.fs-legend-nodata span {{
    display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px;
}}

/* Phones: the top controls stack full width in the page flow, clear of the sidebar toggle at the
   top left; labels wrap rather than being cut off. */
@media (max-width: 640px) {{
    .block-container {{
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }}
    .st-key-top_controls {{
        position: static !important;
        flex-direction: column !important;
        align-items: stretch !important;
        width: 100% !important;
        padding-left: 48px !important;
        margin-bottom: 0.5rem !important;
        box-sizing: border-box !important;
    }}
    .st-key-top_controls > div, .st-key-ask_genie, .st-key-ask_genie button {{
        width: 100% !important;
        max-width: 100% !important;
    }}
    .st-key-telegram_join a.fs-telegram {{
        display: flex;
        justify-content: center;
        white-space: normal;
    }}
    .fs-kpi-label {{ white-space: normal; overflow: visible; text-overflow: clip; }}
    div[data-testid="stMain"] {{ overflow-x: hidden; }}
    div.st-key-map_view, div.st-key-map_subview div[data-testid="stElementContainer"],
    div.st-key-map_view div[data-testid="stButtonGroup"],
    div.st-key-map_subview div[data-testid="stButtonGroup"] {{
        width: 100% !important;
        max-width: 100% !important;
    }}
}}

/* Resident view */
{RESIDENT_CSS}
.st-key-resident_page [data-testid="stCaptionContainer"] {{ font-size: 1rem !important; }}
</style>
"""
st.markdown(MODERN_TELEMETRY_CSS, unsafe_allow_html=True)


def tier_style(tier: str, is_dark: bool) -> tuple[str, str, str]:
    """Return (bg_color, border_color, text_color) for the given risk tier."""
    if tier == "High":
        if is_dark:
            return ("rgba(239, 68, 68, 0.16)", "rgba(239, 68, 68, 0.4)", "#FCA5A5")
        return ("#FEF2F2", "#FECACA", "#DC2626")
    elif tier == "Moderate":
        if is_dark:
            return ("rgba(245, 158, 11, 0.16)", "rgba(245, 158, 11, 0.4)", "#FCD34D")
        return ("#FFFBEB", "#FDE68A", "#D97706")
    elif tier == NO_DATA_TIER:  # grey: the risk is unknown, not low
        if is_dark:
            return ("rgba(148, 163, 184, 0.14)", "rgba(148, 163, 184, 0.4)", "#CBD5E1")
        return ("#F1F5F9", "#CBD5E1", "#475569")
    else:  # Low
        if is_dark:
            return ("rgba(56, 189, 248, 0.16)", "rgba(56, 189, 248, 0.4)", "#7DD3FC")
        return ("#EFF6FF", "#BFDBFE", "#0284C7")


@st.cache_data
def load_ura_boundaries() -> dict[str, Any] | None:
    path = settings.zone_polygons_file
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                raw_geojson = json.load(f)
            simplified_features: list[dict[str, Any]] = []
            for feat in raw_geojson.get("features", []):
                s = shape(feat["geometry"]).simplify(0.001, preserve_topology=True)
                simplified_features.append(
                    {
                        "type": "Feature",
                        "properties": feat.get("properties", {}),
                        "geometry": mapping(s),
                    }
                )
            return {"type": "FeatureCollection", "features": simplified_features}
        except Exception:
            return None
    return None


def forecast_scored(features: pd.DataFrame, radar: RadarView) -> pd.DataFrame:
    """Zone risk in 1 hour: the same scoring as now, on the radar nowcast's rain."""
    return page_table(score_zone_features(forecast_features(features, radar.zones), model))


def withhold_no_data(scored: pd.DataFrame) -> pd.DataFrame:
    """Zones with too few reporting gauges get tier "No data" and no probability, not Low.

    Live and replay tables carry the ``coverage`` column (judged over the whole hour, in
    ``live_features`` / ``replay_days``). A gold table has only its own step's gauge count, so each
    row is judged alone: only "none" can be told there, never "partial"."""
    if "coverage" not in scored.columns:
        out = withhold_tier(scored.assign(timestamp=scored["timestamp"].max()))
        out["timestamp"] = scored["timestamp"]
        return out
    out = scored.copy()
    none = out["coverage"] == "none"
    out.loc[none, "risk_tier"] = NO_DATA_TIER
    out.loc[none, "flood_probability"] = float("nan")
    return out


def page_table(scored: pd.DataFrame) -> pd.DataFrame:
    """The scored table as the page shows it: one row for each of the 55 zones, with zone
    coordinates. A zone missing from ``scored`` is "No data", so a lookup never comes back empty."""
    out = ZONE_META.merge(withhold_no_data(scored), on="ura_planning_area", how="left")
    out["risk_tier"] = out["risk_tier"].fillna(NO_DATA_TIER)
    out["coverage"] = out["coverage"].fillna("none")
    out["zone"] = out["ura_planning_area"]
    return out


TIER_COLORS = {
    "High": "#EF4444",  # Signal Crimson
    "Moderate": "#F59E0B",  # Industrial Amber
    "Low": "#0EA5E9",  # Sky Blue
    NO_DATA_TIER: "#94A3B8",  # Slate grey: unknown, not low
}
NUM_ZONES = len(URA_PLANNING_AREAS)
RADAR_COLOURS = COLOUR_SCALE  # light → heavy, as on MSS's legend
# Where 1, 5, 20, 50 and 100 mm/h fall on the 33-colour bar (from the gauge calibration).
RADAR_TICKS = [(16.7, "1"), (40.9, "5"), (68.2, "20"), (83.3, "50"), (98.5, "100+")]
REPLAY_DEFAULT_TIME = "12:15"  # where the 17 Apr 2021 demo opens; other days open at their peak
ZONE_META = (
    pd.DataFrame.from_dict(URA_PLANNING_AREAS, orient="index")[["lat", "lon", "region"]]
    .rename_axis("ura_planning_area")
    .reset_index()
)


@st.cache_resource
def get_model() -> tuple[Any | None, str]:
    """Return (model or None, caption describing where risk scores come from)."""
    try:
        model = load_model()
    except Exception as exc:  # unpickling can fail in many ways, e.g. a missing libomp
        return None, (
            f"Model could not be loaded ({type(exc).__name__}): "
            "risk tiers use a 30-minute rainfall heuristic."
        )
    if model is None:
        return None, "No trained model found: risk tiers use a 30-minute rainfall heuristic."
    start, end = model.provenance.get("trained_from"), model.provenance.get("trained_to")
    trained = f"NEA rainfall {start[:4]}–{end[:4]}" if start and end else "real NEA rainfall"
    tiers = (
        "thresholds set from the team's false-alarm budgets"
        if model.thresholds
        else "default thresholds (budget not set)"
    )
    return model, f"Risk from a model trained on {trained}, with {tiers}."


@st.cache_data(show_spinner="Computing replay features…")
def replay_features(path: str) -> tuple[pd.DataFrame, str, int]:
    """Features for every zone and step in the replay's display window (warm-up included in calc)."""
    replay = load_replay(path)
    table = compute_zone_feature_table(replay.snapshots, replay.stations)
    table["coverage"] = coverage(table)
    in_window = (table["timestamp"] >= replay.display_start) & (
        table["timestamp"] <= replay.display_end
    )
    return table[in_window].reset_index(drop=True), replay.event_name, len(replay.stations)


@st.cache_data(show_spinner="Loading rainfall for that day…")
def day_features(day_iso: str) -> replay_days.DayView:
    return replay_days.day_view(date.fromisoformat(day_iso))


@st.cache_data
def store_range() -> tuple[date, date]:
    return replay_days.store_date_range()


@st.cache_data
def flood_events() -> list[FloodEvent]:
    try:
        return load_flood_events()
    except (FileNotFoundError, ValueError):
        return []


def _pick_day(day: date) -> None:
    st.session_state["replay_date"] = day


def _set_replay_stage(stage: str) -> None:
    st.session_state["replay_stage"] = stage


def _replay_pinned_storm() -> None:
    """Jump to the 17 Apr 2021 replay. A callback, so it runs before the widgets are drawn."""
    st.session_state["mode"] = "Replay Storm"
    st.session_state["replay_date"] = replay_days.PINNED_STORM
    st.session_state[f"replay-time-{replay_days.PINNED_STORM}"] = REPLAY_DEFAULT_TIME
    st.session_state["replay_stage"] = "simulate"


ANALYST_VIEW, RESIDENT_VIEW = "Analyst view", "Resident view"


def resident_view_on() -> bool:
    # The page opens on the Analyst view. Set here, not only in the header: the sidebar asks
    # before the header is drawn.
    return st.session_state.setdefault("view", ANALYST_VIEW) == RESIDENT_VIEW


def zone_picker() -> str:
    """The planning-area selector for the Zone Diagnostic (sidebar), in both views."""
    st.sidebar.subheader(":material/tune: Zone Diagnostic")
    return str(
        st.sidebar.selectbox("Select Planning Area", options=sorted(URA_PLANNING_AREAS), key="zone")
    )


def storm_buttons(selected: date) -> None:
    """One-click buttons for the major reported storms (sidebar)."""
    st.sidebar.caption("Major storms (days with the most reported floods):")
    for storm_day, label in replay_days.featured_storms(flood_events()):
        st.sidebar.button(
            label,
            key=f"storm-{storm_day}",
            on_click=_pick_day,
            args=(storm_day,),
            width="stretch",
            type="primary" if storm_day == selected else "secondary",
        )


@st.cache_data(ttl=300, show_spinner="Fetching live rainfall from data.gov.sg…")
def live_features() -> tuple[pd.DataFrame | None, int, str | None]:
    """Latest zone features from live data, or (None, 0, error message)."""
    try:
        snapshots = NEAPoller().fetch_history(settings.live_history_hours)
    except LiveFeedUnavailable as exc:
        return None, 0, str(exc)
    stations: dict[str, StationMetadata] = {}
    for snap in snapshots:
        stations.update(snap.stations)
    table = compute_zone_feature_table(snapshots, stations)
    table["coverage"] = coverage(table)  # over the whole history, so a gap in the hour shows
    # The newest step NEA has finished filling in, not one with only a few gauges so far.
    latest = table[table["timestamp"] == newest_complete_step(step_counts(table))]
    latest = latest.reset_index(drop=True)
    return latest, len(stations), None


# Live mode's two sources: the gold table the Databricks pipeline scores (default), or the
# data.gov.sg API scored here (fallback).
DATABRICKS, DATA_GOV = "Databricks", "data.gov.sg"


# 15 min: half the poller's 30-min interval, so gold stays under the stale warning while each
# viewer queries the SQL warehouse (daily compute cap) at most 4 times an hour.
@st.cache_data(ttl=900, show_spinner="Reading risk from Databricks…")
def _gold_cached() -> pd.DataFrame:
    # Raises when gold is unavailable: Streamlit doesn't cache a call that raised, so one cold
    # warehouse can't keep every viewer on the fallback for 15 minutes.
    return latest_zone_risk()


GOLD_RETRY_AFTER = 60.0  # seconds to leave a failed gold read alone


@st.cache_resource
def _gold_failure() -> dict[str, Any]:
    """The last gold failure (shared by all sessions): when it happened and its message."""
    return {}


def clear_gold() -> None:
    """Forget the cached gold table and any recent failure (Retry)."""
    _gold_cached.clear()
    _gold_failure().clear()


def gold_latest() -> tuple[pd.DataFrame | None, str | None]:
    """The latest gold row per zone, or (None, error message). Successes are cached for 15 min;
    a failure is remembered for GOLD_RETRY_AFTER seconds, so a down warehouse isn't queried on
    every rerun."""
    failure = _gold_failure()
    failed_at = failure.get("at")  # .get: another session may clear it at any moment
    if failed_at is not None and time.monotonic() - failed_at < GOLD_RETRY_AFTER:
        return None, str(failure.get("error", "unavailable"))
    try:
        return _gold_cached(), None
    except GoldUnavailable as exc:
        failure.update(at=time.monotonic(), error=str(exc))
        return None, str(exc)


def _toggle_advanced() -> None:
    st.session_state["advanced_open"] = not st.session_state.get("advanced_open", False)


def age_text(age: timedelta) -> str:
    """'4 min', '3 h 5 min' or '9 days'."""
    minutes = max(int(age.total_seconds() // 60), 0)
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h {minutes % 60} min"
    return f"{minutes // (24 * 60)} days"


@st.cache_data(ttl=120, show_spinner=False)
def live_flood_alerts() -> tuple[list[FloodAlert] | None, str | None]:
    """PUB flash-flood alerts still active from the last 3 hours, or (None, error message)."""
    since = datetime.now(settings.tzinfo) - timedelta(hours=3)
    try:
        return active_alerts(fetch_flood_alerts(since=since)), None
    except LiveFeedUnavailable as exc:
        return None, str(exc)
    except (ValueError, KeyError, TypeError, OSError) as exc:  # a malformed or failed reply
        log.warning("PUB flood alerts could not be read", exc_info=True)
        return None, f"{type(exc).__name__}: {exc}"


try:
    import cv2

    _RADAR_ERRORS: tuple[type[BaseException], ...] = (ValueError, KeyError, OSError, cv2.error)
except ImportError:  # the optional 'radar' extra isn't installed (radar_view then says so)
    _RADAR_ERRORS = (ValueError, KeyError, OSError)


def _radar(when_iso: str) -> RadarView | None:
    try:
        return radar_view(datetime.fromisoformat(when_iso))
    except ImportError:
        return None  # the optional 'radar' extra (OpenCV) isn't installed
    except _RADAR_ERRORS:  # a corrupt frame or a changed colour scale: "no radar", not a crash
        log.warning("Radar for %s could not be read", when_iso, exc_info=True)
        return None


class _NoRadar(Exception):
    """No radar came back; raised so that the miss isn't cached for good."""


RADAR_MISS_TTL = 300.0  # seconds a missing radar frame isn't looked for again


@st.cache_resource
def _radar_misses() -> dict[str, float]:
    return {}


# Frames more than an hour old never change, so a replayed time is computed once and kept (about
# 0.2 MB each; 500 is plenty for a session of scrubbing). Recent times are refreshed every 5 min,
# since the newest frames may still be arriving.
@st.cache_data(max_entries=500, show_spinner="Loading radar…")
def _radar_past(when_iso: str) -> RadarView:
    view = _radar(when_iso)
    if view is None:
        raise _NoRadar  # not cached: a network blip must not hide the radar for the whole session
    return view


@st.cache_data(ttl=300, show_spinner="Loading radar…")
def _radar_recent(when_iso: str) -> RadarView | None:
    return _radar(when_iso)


def radar_for(when_iso: str) -> RadarView | None:
    """Radar now and the nowcast at a time (None: no radar exists for it). Cached per frame time."""
    age = datetime.now(settings.tzinfo) - datetime.fromisoformat(when_iso)
    if age <= timedelta(hours=1):
        return _radar_recent(when_iso)
    misses = _radar_misses()
    missed_at = misses.get(when_iso)
    if missed_at is not None and time.monotonic() - missed_at < RADAR_MISS_TTL:
        return None  # looked a moment ago: don't repeat the frame reads and API calls
    try:
        return _radar_past(when_iso)
    except _NoRadar:
        misses[when_iso] = time.monotonic()
        return None


LIVE_REFRESH = timedelta(minutes=5)


@st.fragment(run_every=LIVE_REFRESH)
def live_auto_refresh() -> None:
    """Re-run the whole app with fresh readings every LIVE_REFRESH while Live Feed is open."""
    loaded_at = st.session_state.setdefault("live_loaded_at", time.monotonic())
    if time.monotonic() - loaded_at >= LIVE_REFRESH.total_seconds() - 10:
        st.session_state["live_loaded_at"] = time.monotonic()
        live_features.clear()
        live_flood_alerts.clear()  # not gold: its 15-min cache keeps the warehouse load down
        st.rerun(scope="app")


model, model_caption = get_model()

# --- SIDEBAR -------------------------------------------------------------------------------
st.sidebar.markdown(
    '<div style="margin-bottom: 14px;">'
    '  <div style="font-size: 1.25rem; font-weight: 800; letter-spacing: -0.02em; display: flex; align-items: center; gap: 8px;">'
    "<span>FloodSense</span>"
    "</div>"
    f'  <div style="font-size: 0.72rem; color: {tokens["text_muted"]}; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; margin-top: 2px;">'
    "    Urban Drainage Intelligence"
    "  </div>"
    "</div>",
    unsafe_allow_html=True,
)

# The Analyst / Resident switch heads the sidebar, right above Live / Replay; both are the same
# full-width size (CSS on "sidebar_switches").
st.session_state.setdefault("view", ANALYST_VIEW)
MODES = ["Live Feed", "Replay Storm"]
# Clicking the chosen segment again sends None; keep Live Feed rather than falling into Replay.
if st.session_state.get("mode") not in MODES:
    st.session_state["mode"] = "Live Feed"
with st.sidebar.container(key="sidebar_switches", gap="xsmall"):
    st.segmented_control(
        "View",
        options=[ANALYST_VIEW, RESIDENT_VIEW],
        key="view",
        required=True,
        label_visibility="collapsed",
    )
    mode = (
        st.segmented_control(
            "Mode",
            options=MODES,
            key="mode",
            required=True,
            label_visibility="collapsed",
        )
        or "Live Feed"
    )

st.sidebar.markdown("---")

# Replay has two steps in the sidebar: choose a date ("choose"), then simulate it ("simulate",
# with the area and time controls). Streamlit drops a widget's value while the widget is hidden,
# so re-store the values of the widgets the other step hides.
# A shared link (?area=bukit-merah) picks the area once per session; later choices win.
DEFAULT_ZONE = "BUKIT TIMAH"
if not st.session_state.get("_area_link_applied"):
    st.session_state["_area_link_applied"] = True
    _link = st.query_params.get("area")
    _linked_area = area_from_slug(_link)
    if _linked_area:
        st.session_state["zone"] = _linked_area
    elif _link:
        st.session_state["_area_notice"] = str(_link)[:60]
st.session_state.setdefault("zone", DEFAULT_ZONE)
st.session_state.setdefault("_area_synced", st.session_state["zone"])
st.session_state.setdefault("replay_stage", "choose")
for _key in list(st.session_state):
    if _key in ("zone", "replay_date", "use_data_gov") or str(_key).startswith("replay-time-"):
        st.session_state[_key] = st.session_state[_key]
selected_zone = str(st.session_state["zone"])
live_source = DATA_GOV
if mode == "Live Feed":
    # Databricks by default; a copy with no warehouse set up (e.g. CI) starts on data.gov.sg.
    st.session_state.setdefault("use_data_gov", not warehouse_id())
# Settings stay out of the way: a "Settings" button at the bottom of the sidebar slides its
# toggles out to the right (CSS): the theme, and in Live Feed the data.gov.sg fallback. It is drawn
# at the same place in the script in both modes (before the area picker), so a rerun keeps it as
# one element instead of drawing a second one and moving it; CSS shows it last.
st.session_state.setdefault("advanced_open", False)
advanced_open = st.session_state["advanced_open"]
with st.sidebar.container(key="advanced_menu", horizontal=True, wrap=False, gap=None):
    st.button(
        "Settings",
        key="advanced_btn_open" if advanced_open else "advanced_btn",
        icon=":material/chevron_left:" if advanced_open else ":material/chevron_right:",
        on_click=_toggle_advanced,
    )
    if advanced_open:
        with st.container(key="advanced_ext", gap="small"):
            is_dark_active = st.toggle("Light/Dark Mode", key="dark_mode_switch")
            if mode == "Live Feed":
                st.toggle("data.gov.sg", key="use_data_gov")
        if is_dark_active != IS_DARK:
            st.session_state["theme"] = "dark" if is_dark_active else "light"
            st.rerun()
if mode == "Live Feed":
    selected_zone = zone_picker()
    live_source = DATA_GOV if st.session_state["use_data_gov"] else DATABRICKS


# --- TOP HEADER & APPEARANCE SWITCH --------------------------------------------------------
# --- ASK FLOODSENSE (DATABRICKS GENIE) ------------------------------------------------------
def genie_examples(resident: bool, zone: str) -> list[str]:
    """Starter questions: plain ones for residents, ranked tables for analysts."""
    area = zone.title()
    if resident:
        return [
            f"Is {area} at risk of a flash flood right now?",
            "Which areas are at risk of a flash flood right now?",
        ]
    return [
        "Which areas are at Moderate or High risk right now?",
        "Top 5 areas by flood probability right now",
        f"How has the flood chance in {area} changed over the last 6 hours?",
    ]


def _ask_genie(question: str) -> None:
    question = question.strip()
    if not question:
        return
    try:
        answer = genie.ask(question)
        st.session_state["genie_qa"] = {"question": question, "answer": answer, "error": None}
    except genie.GenieUnavailable as exc:
        log.warning("Genie unavailable: %s", exc)
        st.session_state["genie_qa"] = {"question": question, "answer": None, "error": str(exc)}


def render_genie_answer(resident: bool) -> None:
    qa = st.session_state.get("genie_qa")
    if not qa:
        return
    st.markdown(f"**{html.escape(qa['question'])}**")
    answer: genie.GenieAnswer | None = qa["answer"]
    if answer is None:
        st.info(
            "Genie couldn't answer just now. Please try again in a minute.",
            icon=":material/cloud_off:",
        )
        return
    if answer.summary:
        st.markdown(answer.summary)
    if answer.rows and not resident:
        st.dataframe(
            pd.DataFrame(answer.rows, columns=answer.columns), hide_index=True, height="content"
        )
    if answer.sql and not resident:
        with st.expander("SQL that Genie ran on the gold table"):
            st.code(answer.sql, language="sql")
    st.caption("FloodSense is not an official warning. Follow PUB's alerts.")


def render_ask_panel() -> None:
    resident = resident_view_on()
    with st.popover("Ask FloodSense", icon=":material/forum:", type="primary", key="ask_genie"):
        st.caption("Ask about live flood risk in plain English. Powered by Databricks Genie.")
        if not genie.space_id():
            st.info("Ask FloodSense needs the Databricks connection.", icon=":material/cloud_off:")
            return
        if mode != "Live Feed":
            st.caption("Answers use the live data, not the day being replayed.")
        examples = genie_examples(resident, str(st.session_state.get("zone", DEFAULT_ZONE)))
        for i, example in enumerate(examples):
            if st.button(example, key=f"genie_example_{i}", width="stretch"):
                with st.spinner("Asking Genie..."):
                    _ask_genie(example)
        with st.form("genie_form", clear_on_submit=True, border=False):
            question = st.text_input(
                "Your question",
                placeholder="Type a question about flood risk",
                label_visibility="collapsed",
            )
            if st.form_submit_button("Ask", type="primary"):
                with st.spinner("Asking Genie..."):
                    _ask_genie(question)
        render_genie_answer(resident)


TELEGRAM_URL = "https://t.me/fs_tele_alert_bot"
TELEGRAM_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" fill="#ffffff">'
    '<path d="M9.78 18.65l.28-4.23 7.68-6.92c.34-.31-.07-.46-.52-.19L7.74 13.3 3.64 12c-.88'
    "-.25-.89-.86.2-1.3l15.97-6.16c.73-.33 1.43.18 1.15 1.3l-2.72 12.81c-.19.91-.74 1.13-1.5"
    '.71L12.6 16.3l-1.99 1.93c-.23.23-.42.42-.83.42z"/></svg>'
)


def render_telegram_join() -> None:
    with st.container(key="telegram_join"):
        st.markdown(
            f'<a class="fs-telegram" href="{TELEGRAM_URL}" target="_blank" rel="noopener noreferrer" '
            f'title="Join the FloodSense flood alerts channel on Telegram">'
            f"{TELEGRAM_ICON}<span>Join flood alerts on Telegram</span></a>",
            unsafe_allow_html=True,
        )


def render_app_header() -> None:
    # Top-right corner (CSS): Telegram alerts channel, and "Ask FloodSense" (Genie answering
    # questions about live risk).
    with st.container(
        key="top_controls", horizontal=True, vertical_alignment="center", gap="small"
    ):
        render_telegram_join()
        render_ask_panel()
    with st.container(key="app_header"):
        st.title("FloodSense")
        st.caption(
            "The chance of a flash flood in the next hour, for each of Singapore's 55 planning "
            "areas."
        )


render_app_header()


def sync_area_param(zone: str) -> None:
    """Keep ?area= in step with the chosen area, so the address bar is the shareable link."""
    if st.session_state.get("_area_synced") != zone:
        st.session_state["_area_synced"] = zone
        st.query_params["area"] = zone.lower().replace(" ", "-")


# A shared link with an area we don't have: say so (while the default is still showing).
_area_notice = st.session_state.get("_area_notice")
if _area_notice is not None:
    if st.session_state["zone"] == DEFAULT_ZONE:
        st.info(
            f"Area '{escape_markdown(_area_notice)}' not found; showing {DEFAULT_ZONE.title()}."
        )
    else:
        del st.session_state["_area_notice"]

# --- DATA FOR THE SELECTED VIEW ------------------------------------------------------------
replay_warning: str | None = None
stale = False  # live data older than settings.gold_stale_minutes
older_zones = 0  # gold zones whose row is older than the newest
from_gold = False  # Live risk read from the Databricks gold table (scored there, not here)
day_events: list[FloodEvent] | None = None  # reported floods for the replayed day
second_look: str | None = None  # the "second look" label for a replayed 2024–2026 test-set day
if mode == "Live Feed":
    features: pd.DataFrame | None = None
    if live_source == DATABRICKS:
        features, gold_error = gold_latest()
        if features is None:
            log.warning("Gold table unavailable: %s", gold_error)
            if not resident_view_on():  # residents get the data.gov.sg readings without the jargon
                st.warning(
                    "Couldn't read the Databricks gold table. Showing data.gov.sg readings instead.",
                    icon=":material/cloud_off:",
                )
                with st.expander("Technical detail"):
                    st.code(str(gold_error), language=None)
        else:
            from_gold, total_stations = True, 0
    if features is None:
        features, total_stations, live_error = live_features()
    if features is None:
        log.warning("Live feed unavailable: %s", live_error)
        st.error("We can't reach NEA's rain gauges right now.", icon=":material/cloud_off:")
        if resident_view_on():
            st.info("No risk is shown rather than a guess. Please try again in a few minutes.")
        else:
            st.info(
                "No data is shown rather than substituting simulated rain. Switch to "
                "**Replay Storm**, or retry."
            )
            with st.expander("Technical detail"):
                st.code(str(live_error), language=None)
                if any(code in str(live_error) for code in ("401", "403")):
                    st.caption(
                        "The data.gov.sg key may be missing or wrong. Set "
                        "`FLOODSENSE_DATA_GOV_API_KEY` to a valid key."
                    )
        col_fb1, col_fb2 = st.columns(2)
        with col_fb1:
            if st.button("Retry Live Feed", use_container_width=True):
                live_features.clear()
                clear_gold()
                st.rerun()
        with col_fb2:
            if not resident_view_on():
                st.button(
                    "Switch to Replay Storm (17 Apr 2021)",
                    type="primary",
                    on_click=_replay_pinned_storm,
                    use_container_width=True,
                )
        st.stop()
    view_time = features["timestamp"].max()
    # Gold rows can differ in time: the age is the oldest row's, so a stale zone isn't hidden.
    age = datetime.now(settings.tzinfo) - features["timestamp"].min()
    older_zones = int((features["timestamp"] < view_time).sum())
    live_bg = "rgba(16, 185, 129, 0.15)" if IS_DARK else "#ECFDF5"
    live_border = "rgba(16, 185, 129, 0.35)" if IS_DARK else "#A7F3D0"
    live_color = "#34D399" if IS_DARK else "#059669"
    live_badge = (
        f'<span style="background: {live_bg}; border: 1px solid {live_border}; '
        f"color: {live_color}; padding: 4px 12px; border-radius: 9999px; font-size: 0.78rem; "
        f"font-family: 'Inter', sans-serif; font-weight: 600;\">"
        + (
            "● Live: scored on Databricks</span>"
            if from_gold
            else f"● Live: {int(features['reporting_stations'].iloc[0])} of {total_stations} gauges "
            "reporting (data.gov.sg)</span>"
        )
    )
    # Older than settings.gold_stale_minutes (either source): the age turns amber and the page warns.
    stale = age > timedelta(minutes=settings.gold_stale_minutes)
    age_color = ("#FBBF24" if IS_DARK else "#D97706") if stale else c_muted
    status_line = (
        f"{live_badge} &nbsp;·&nbsp; <span style='color: {c_muted}; font-size: 0.8rem; font-family: \"Inter\", sans-serif; font-weight: 500;'>Latest Reading:</span> "
        f"`{view_time:%d %b %Y %H:%M} SGT` "
        f"<span style='color: {age_color}; font-size: 0.8rem; font-family: \"Inter\", sans-serif; font-weight: {600 if stale else 400}; white-space: nowrap;'>· {age_text(age)} old</span>"
    )
    if not from_gold:
        history_note = (
            f"Rolling features use the last {settings.live_history_hours:g} h of live readings, so "
            "the 72-hour wet-ground index is understated."
        )
elif replay_days.store_available():
    first_day, last_day = store_range()
    st.session_state.setdefault("replay_date", replay_days.PINNED_STORM)
    choosing = st.session_state["replay_stage"] == "choose"
    if choosing:
        replay_day = st.sidebar.date_input(
            "Replay date (SGT)",
            key="replay_date",
            min_value=first_day,
            max_value=last_day,
            format="DD/MM/YYYY",
        )
    else:
        replay_day = st.session_state["replay_date"]
    view = day_features(replay_day.isoformat())
    if view.features.empty:
        st.session_state["replay_stage"] = "choose"
        if not choosing:
            st.rerun()
        storm_buttons(replay_day)
        st.sidebar.button(
            "Simulate this date",
            type="primary",
            width="stretch",
            disabled=True,
        )
        st.warning(
            f"NEA has no rain-gauge readings for {replay_day:%d %b %Y}. It is one of the gaps in "
            "NEA's record, so there is nothing to replay. Missing data is not shown as dry. "
            "Pick another date.",
            icon=":material/cloud_off:",
        )
        st.stop()
    table, total_stations = view.features, view.total_stations
    times = {f"{t:%H:%M}": t for t in sorted(table["timestamp"].unique())}
    default = (
        REPLAY_DEFAULT_TIME
        if replay_day == replay_days.PINNED_STORM and REPLAY_DEFAULT_TIME in times
        else f"{replay_days.default_time(table):%H:%M}"
    )
    time_key = f"replay-time-{replay_day}"
    if st.session_state.get(time_key) not in times:
        st.session_state[time_key] = default
    if choosing:
        storm_buttons(replay_day)
        st.sidebar.button(
            "Simulate this date",
            type="primary",
            width="stretch",
            on_click=_set_replay_stage,
            args=("simulate",),
        )
        chosen = st.session_state[time_key]
    else:
        st.sidebar.markdown(f"**Simulating {replay_day:%d %b %Y}**")
        selected_zone = zone_picker()
        chosen = st.sidebar.select_slider(
            f"Replay time (SGT, {replay_day:%d %b %Y})",
            options=list(times),
            key=time_key,
        )
        st.sidebar.markdown("---")
        st.sidebar.button(
            "Choose another date",
            type="primary",
            width="stretch",
            on_click=_set_replay_stage,
            args=("choose",),
        )
    view_time = times[chosen]
    features = table[table["timestamp"] == view_time].reset_index(drop=True)
    replay_bg = "rgba(245, 158, 11, 0.15)" if IS_DARK else "#FEF3C7"
    replay_border = "rgba(245, 158, 11, 0.35)" if IS_DARK else "#FDE68A"
    replay_color = "#FBBF24" if IS_DARK else "#D97706"
    replay_badge = (
        f'<span style="background: {replay_bg}; border: 1px solid {replay_border}; '
        f"color: {replay_color}; padding: 4px 12px; border-radius: 9999px; font-size: 0.78rem; "
        f"font-family: 'Inter', sans-serif; font-weight: 600;\">"
        "⟳ Replay: NEA gauge readings</span>"
    )
    status_line = (
        f"{replay_badge} &nbsp;·&nbsp; <span style='color: {c_muted}; font-size: 0.8rem; font-family: \"Inter\", sans-serif; font-weight: 500;'>Timestamp:</span> "
        f"`{view_time:%d %b %Y %H:%M} SGT`"
    )
    second_look = replay_days.second_look_note(replay_day)
    if view.step_share < replay_days.SPARSE_SHARE:
        replay_warning = (
            f"NEA's record for this day is patchy: only {view.step_share:.0%} of 5-minute steps "
            "have any reading. Gaps are left as gaps, so rainfall may be understated."
        )
    day_events = replay_days.events_on(replay_day, flood_events())
else:
    table, event_name, total_stations = replay_features(str(settings.replay_file))
    times = {f"{t:%H:%M}": t for t in sorted(table["timestamp"].unique())}
    selected_zone = zone_picker()
    chosen = st.sidebar.select_slider(
        "Replay time (SGT, 17 Apr 2021)",
        options=list(times),
        value=REPLAY_DEFAULT_TIME if REPLAY_DEFAULT_TIME in times else next(iter(times)),
    )
    view_time = times[chosen]
    features = table[table["timestamp"] == view_time].reset_index(drop=True)
    replay_bg = "rgba(245, 158, 11, 0.15)" if IS_DARK else "#FEF3C7"
    replay_border = "rgba(245, 158, 11, 0.35)" if IS_DARK else "#FDE68A"
    replay_color = "#FBBF24" if IS_DARK else "#D97706"
    replay_badge = (
        f'<span style="background: {replay_bg}; border: 1px solid {replay_border}; '
        f"color: {replay_color}; padding: 4px 12px; border-radius: 9999px; font-size: 0.78rem; "
        f"font-family: 'Inter', sans-serif; font-weight: 600;\">"
        f"⟳ Historical Replay: {event_name}</span>"
    )
    status_line = (
        f"{replay_badge} &nbsp;·&nbsp; <span style='color: {c_muted}; font-size: 0.8rem; font-family: \"Inter\", sans-serif; font-weight: 500;'>Timestamp:</span> "
        f"`{view_time:%d %b %Y %H:%M} SGT`"
    )
    day_events = replay_days.events_on(replay_days.PINNED_STORM, flood_events())

# Gold arrives scored by the pipeline: show its probabilities and tiers as they are.
df_results = page_table(features if from_gold else score_zone_features(features, model))
reporting_stations = int(features["reporting_stations"].iloc[0])
no_data_count = int((df_results["risk_tier"] == NO_DATA_TIER).sum())
all_no_data = no_data_count == NUM_ZONES

if all_no_data:  # an outage or a gap: no risk can be given, and none is guessed
    gauges = (
        f"{reporting_stations} of {total_stations}" if total_stations else f"{reporting_stations}"
    )
    st.warning(
        f"Too few rain gauges are reporting ({gauges}) — risk can't be estimated "
        f"{'right now' if mode == 'Live Feed' else 'at this time'}.",
        icon=":material/cloud_off:",
    )


# --- PUBLIC TRANSPORT AT RISK (BOTH VIEWS) ----------------------------------------------------
def public_transport(
    zone: str, alerts: list[FloodAlert], chance_thresholds: dict[str, float] | None = None
) -> None:
    """MRT/LRT stations with an exit in a Moderate or High area (or near an active PUB alert),
    plus the chosen area's stations. With ``chance_thresholds`` (the Analyst view) the table adds
    each station's chance; the Resident view shows tiers only, no percentages."""
    show_chance = chance_thresholds is not None
    with st.container(border=True):
        st.subheader(":material/train: Public transport at risk")
        try:
            at_risk = stations_at_risk(df_results, alerts)
        except FileNotFoundError as exc:
            st.warning(str(exc))
            at_risk = None
        if at_risk is not None and at_risk.empty:
            st.markdown(
                "No MRT/LRT station has an exit in a Moderate or High area"
                + (" or near an active PUB flood alert" if mode == "Live Feed" else "")
                + " right now."
            )
        elif at_risk is not None:
            counts = at_risk["tier"].value_counts()
            parts = [
                f"{n} in {tier} areas"
                for tier in ("High", "Moderate")
                if (n := int(counts.get(tier, 0)))
            ]
            if near := int(at_risk["near_alert"].sum()):
                parts.append(f"{near} near an active PUB alert")
            st.markdown(f"**Stations with an exit at risk:** {', '.join(parts)}.")
        if at_risk is not None:
            shown = pin_zone_stations(at_risk, zone, df_results)

            # An HTML table rather than st.dataframe: the dataframe is drawn on a canvas that the
            # page's light/dark styling can't reach.
            def _tier_chip(tier: str) -> str:
                bg, border, text = (
                    tier_style(tier, IS_DARK)
                    if tier != "PUB alert"
                    else tier_style("High", IS_DARK)
                )
                return (
                    f'<span style="background:{bg}; border:1px solid {border}; color:{text}; '
                    f'padding:1px 8px; border-radius:9999px; font-weight:600;">{html.escape(tier)}</span>'
                )

            cell = (
                f"padding:7px 10px; border-bottom:1px solid {c_border_subtle}; white-space:nowrap;"
            )
            head = (
                f"position:sticky; top:0; background:{c_subtle}; color:{c_muted}; "
                f"text-align:left; font-weight:600; {cell}"
            )
            # The selected area's stations are pinned to the top and tinted (also when it is Low).
            pick = f"background:{c_accent_muted}; font-weight:600;"
            rows_html = "".join(
                "<tr>"
                f'<td style="{cell} {pick if r.selected else ""}">{html.escape(str(r.station))}</td>'
                f'<td style="{cell} {pick if r.selected else ""}">{_tier_chip(str(r.tier))}</td>'
                + (
                    f'<td style="{cell} {pick if r.selected else ""}">'
                    f"{'' if pd.isna(r.probability) else pct(r.probability, chance_thresholds)}</td>"
                    if show_chance
                    else ""
                )
                + f'<td style="{cell} {pick if r.selected else ""}">{html.escape(str(r.zone).title())}</td>'
                "</tr>"
                for r in shown.itertuples(index=False)
            )
            # Risk (and chance) come right after the station, so they stay on screen on a phone.
            headers = [
                "Station",
                "Risk",
                *(["Chance (next hour)"] if show_chance else []),
                "Planning area",
            ]
            table_box = (
                st.container() if not at_risk.empty else st.expander(f"Stations in {zone.title()}")
            )
            table_box.markdown(
                f'<div style="max-height:380px; overflow:auto; border:1px solid {c_border}; '
                f'border-radius:10px; background:{c_surface};">'
                f'<table style="width:100%; border-collapse:collapse; font-size:0.85rem; '
                f"font-family:'Inter', sans-serif; color:{c_main}; margin:0;\">"
                "<thead><tr>"
                + "".join(f'<th style="{head}">{h}</th>' for h in headers)
                + f"</tr></thead><tbody>{rows_html}</tbody></table></div>",
                unsafe_allow_html=True,
            )
        st.caption(
            "Stations with an exit in a Moderate or High area or near an active PUB alert, plus the "
            f"selected area's stations. This shows exposure, not service disruptions. Exit locations: "
            f"{MRT_SOURCE}."
            + (
                " Today's station list is used for every replay, so stations that opened after the "
                "replayed day can appear."
                if mode != "Live Feed"
                else ""
            )
        )


# --- RESIDENT VIEW -------------------------------------------------------------------------
if resident_view_on():
    if mode == "Live Feed":
        live_auto_refresh()
    # radar_for returns None when there's no radar for this time (or no OpenCV); anything else
    # is a bug and should show, not silently drop the "In 1 hour" line.
    resident_radar = radar_for(view_time.isoformat())
    resident_forecast = (
        forecast_scored(features, resident_radar) if resident_radar is not None else None
    )
    resident_alerts, resident_alerts_error = (
        live_flood_alerts() if mode == "Live Feed" else ([], None)
    )
    with st.container(key="resident_page"):
        resident_zone = render_resident_view(
            zone=selected_zone,
            risk_now=df_results,
            risk_in_1h=resident_forecast,
            when=view_time,
            is_replay=mode != "Live Feed",
            tier_style=tier_style,
            is_dark=IS_DARK,
            tokens=tokens,
            # Live only; the list is cached (no new fetch).
            pub_alerts=resident_alerts or [],
            pub_alerts_unavailable=resident_alerts_error is not None,
            stale_age=age_text(age) if stale else None,
            day_events=day_events,
        )
        public_transport(resident_zone, resident_alerts or [])
    sync_area_param(resident_zone)
    st.stop()
sync_area_param(selected_zone)

# --- TOP BAR -------------------------------------------------------------------------------
# Only what changes how the page should be read stays in view (warnings, the "second look"
# label); where the numbers come from goes under "More numbers and data sources".
st.markdown(status_line, unsafe_allow_html=True)
if replay_warning:
    st.warning(replay_warning, icon=":material/warning:")
if stale:
    st.warning(
        f"Data is {age_text(age)} old. The risk shown may not reflect current conditions.",
        icon=":material/schedule:",
    )
if older_zones:
    st.caption(
        f"{older_zones} of {NUM_ZONES} areas have older readings than the rest; the age above is "
        "for the oldest."
    )
if second_look:
    st.caption(second_look)
if model is None and not from_gold:
    st.warning(model_caption, icon=":material/model_training:")
elif model is None:  # says why the alert levels and the storm rarity are missing
    st.caption(
        "Risk is scored on Databricks. The local model isn't loaded, so alert levels and the "
        "storm-rarity figure are not shown."
    )
# The data.gov.sg fallback has only a few hours of history, which understates the risk.
if mode == "Live Feed" and not from_gold:
    st.caption(history_note)

active_pub_alerts: list[FloodAlert] = []  # live PUB alerts (none in Replay: no history)
if mode == "Live Feed":
    live_auto_refresh()
    pub_alerts, pub_alerts_error = live_flood_alerts()
    active_pub_alerts = pub_alerts or []
    if pub_alerts_error:
        st.caption("PUB flood alerts can't be checked right now.")
        with st.expander("Technical detail"):
            st.code(str(pub_alerts_error), language=None)
    elif pub_alerts:
        tiers = dict(zip(df_results["ura_planning_area"], df_results["risk_tier"], strict=True))
        lines = [
            f"- **{escape_markdown(a.zone or a.area_desc)}** · {a.issued_at:%H:%M} · "
            f"{escape_markdown(' '.join(a.description.split()))}"
            + (f" *(FloodSense: {tiers[a.zone]})*" if a.zone in tiers else "")
            for a in pub_alerts
        ]
        st.error(
            f"**{len(pub_alerts)} active PUB flash-flood alert(s)**\n\n" + "\n".join(lines),
            icon=":material/flood:",
        )

high_risk_count = int((df_results["risk_tier"] == "High").sum())
mod_risk_count = int((df_results["risk_tier"] == "Moderate").sum())
# A "partial" zone's missing steps are 0-filled, so a zero there isn't known to be dry.
any_partial = bool((df_results["coverage"] == "partial").any())
# Rain is only read where gauges reported: a zone with no data isn't "the driest".
with_rain = df_results[
    (df_results["coverage"] != "none")
    & df_results["rain_30m"].notna()
    & df_results["rain_decay_72h"].notna()
]
peak = with_rain.sort_values("rain_30m", ascending=False).iloc[0] if len(with_rain) else None
wettest = (
    with_rain.sort_values("rain_decay_72h", ascending=False).iloc[0] if len(with_rain) else None
)

# --- HEADLINE: HOW MANY AREAS ARE AT RISK ---------------------------------------------------
headline = areas_at_risk(df_results)
worst_tier = (
    NO_DATA_TIER
    if all_no_data
    else "High"
    if high_risk_count
    else "Moderate"
    if mod_risk_count
    else "Low"
)
if peak is None:
    rain_line = "Rainfall unknown: no gauge readings."  # missing, never shown as dry
elif peak["rain_30m"] > 0:
    rain_line = (
        f"Heaviest rain: {peak['zone'].title()}, {peak['rain_30m']:.1f} mm in the last 30 min."
    )
else:
    rain_line = "Some rain readings are missing." if any_partial else "No rain in the last 30 min."
sub_lines = [rain_line]
if day_events:  # a replay: what was actually reported that day, next to what the model says
    sub_lines.append(reported_floods_line(day_events))
when_label = "Right now" if mode == "Live Feed" else f"At {view_time:%H:%M}, {view_time:%d %b %Y}"
head_bg, head_border, head_text = tier_style(worst_tier, IS_DARK)
st.markdown(
    f'<div style="background:{head_bg}; border:1px solid {head_border}; border-radius:12px; '
    f"padding:14px 18px; margin:10px 0 12px 0; font-family:'Inter', sans-serif;\">"
    f'<div style="font-size:0.72rem; font-weight:700; color:{tokens["text_muted"]}; '
    f'text-transform:uppercase; letter-spacing:0.05em;">{when_label}</div>'
    f'<div style="font-size:1.5rem; font-weight:700; color:{head_text}; line-height:1.25; '
    f'margin:4px 0 6px 0;">{html.escape(headline)}</div>'
    + "".join(
        f'<div style="font-size:0.92rem; color:{tokens["text_main"]};">{html.escape(line)}</div>'
        for line in sub_lines
    )
    + "</div>",
    unsafe_allow_html=True,
)
if no_data_count:
    st.caption(
        f"{no_data_count} {'area has' if no_data_count == 1 else 'areas have'} no data "
        "(too few gauges reporting), so they are left out of the risk counts."
    )

# --- 5 METRIC CARDS ------------------------------------------------------------------------
# Every card has the same three parts, each one line: label, value, footer (label | value).
_LABEL = (
    f"font-size:0.68rem; font-family:'Inter', sans-serif; font-weight:700; color:{tokens['text_muted']}; "
    "text-transform:uppercase; letter-spacing:0.05em; display:block;"
)  # one line with an ellipsis on wide screens, wrapping on phones (.fs-kpi-label)


def card_header(label: str) -> None:
    st.markdown(
        f'<div style="margin-bottom:4px;"><span class="fs-kpi-label" style="{_LABEL}">{label}</span></div>',
        unsafe_allow_html=True,
    )


def card_footer(left: str, right: str) -> None:
    st.markdown(
        f'<div style="display:flex; justify-content:space-between; font-size:0.72rem; '
        f"font-family:'Inter', sans-serif; color:{tokens['text_muted']}; border-top:1px solid {tokens['border_subtle']}; "
        f'padding-top:6px; margin-top:2px; gap:8px; white-space:nowrap;">'
        f"<span>{left}</span>"
        f'<span style="font-weight:700; color:{tokens["text_main"]}; overflow:hidden; text-overflow:ellipsis;" '
        f'title="{right}">{right}</span></div>',
        unsafe_allow_html=True,
    )


def kpi_card(label: str, metric_label: str, value: str, footer: tuple[str, str], **kw) -> None:
    with st.container(border=True):
        card_header(label)
        st.metric(label=metric_label, value=value, label_visibility="collapsed", **kw)
        card_footer(*footer)


# Gold is scored on Databricks. Without a local model there are no thresholds to show (the 25% /
# 65% heuristic ones would be wrong for gold), so the alert levels and the rarity are left out.
tier_thresholds: dict[str, float] | None = getattr(model, "thresholds", None) or (
    None if (from_gold and model is None) else default_thresholds()
)
share = reporting_stations / total_stations if total_stations else 0.0
kpi_cols = st.columns(5)
with kpi_cols[0]:
    kpi_card(
        "High risk zones",
        "High Risk",
        f"{high_risk_count} / {NUM_ZONES}",
        ("High from", pct(tier_thresholds["high"]))
        if tier_thresholds
        else ("Scored on", "Databricks"),
    )
with kpi_cols[1]:
    kpi_card(
        "Moderate risk zones",
        "Moderate Risk",
        f"{mod_risk_count} / {NUM_ZONES}",
        ("Moderate from", pct(tier_thresholds["moderate"]))
        if tier_thresholds
        else ("Scored on", "Databricks"),
    )
with kpi_cols[2]:
    kpi_card(
        "Max 30-min rain",
        "Max 30-min Rain",
        f"{peak['rain_30m']:.1f} mm" if peak is not None else "—",
        ("Heaviest", "No data")
        if peak is None
        else ("Heaviest", peak["zone"].title())
        if peak["rain_30m"] > 0
        else ("Heaviest", "Readings missing" if any_partial else "No rain"),
    )
with kpi_cols[3]:
    kpi_card(
        "Wet-ground index",
        "Wet-Ground Index",
        f"{wettest['rain_decay_72h']:.0f}" if wettest is not None else "—",
        ("Wettest", "No data")
        if wettest is None
        else ("Wettest", wettest["zone"].title())
        if wettest["rain_decay_72h"] > 0
        else ("Wettest", "Readings missing" if any_partial else "All dry"),
    )
with kpi_cols[4]:
    kpi_card(
        "Gauges reporting",
        "Gauges Live",
        f"{reporting_stations} / {total_stations}" if total_stations else f"{reporting_stations}",
        ("Reporting now", f"{share:.0%}") if total_stations else ("Source", "Databricks"),
    )

# --- MAP & ZONE DETAILS --------------------------------------------------------------------
detail_col, map_col = st.columns([1.3, 1.7])

with map_col, st.container(border=True, key="map_card"):
    st.markdown(
        f'<div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 2px;">'
        f'  <div style="display: flex; align-items: center; gap: 8px;">'
        f"    <span style=\"font-size: 1.15rem; font-weight: 700; color: {tokens['text_main']}; font-family: 'Inter', sans-serif;\">Risk map</span>"
        f"  </div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    map_view = st.segmented_control(
        "Map view",
        ["Risk of flood", "Rain radar"],
        default="Risk of flood",
        key="map_view",
        label_visibility="collapsed",
    )
    radar_mode = map_view == "Rain radar"
    with st.container(key="map_subview"):
        if radar_mode:
            radar_layer = st.segmented_control(
                "Radar time",
                ["Now", "+30 min", "+60 min"],
                default="Now",
                key="radar_when",
                label_visibility="collapsed",
            )
            forecast_on = False
        else:
            radar_layer = None
            forecast_on = (
                st.segmented_control(
                    "Risk time",
                    ["Now", "In 1 hour"],
                    default="Now",
                    key="risk_when",
                    label_visibility="collapsed",
                )
                == "In 1 hour"
            )
    need_radar = radar_mode or forecast_on
    radar = radar_for(view_time.isoformat()) if need_radar else None
    if need_radar and radar is None:
        st.caption(
            "No radar for this time. data.gov.sg keeps about 3 months of radar and our archive "
            "starts 16 Jul 2026. Try replaying **30 Sep 2026** (Woodlands flood)."
        )
    map_df = df_results
    if forecast_on and radar is not None:
        map_df = forecast_scored(features, radar)
    map_func = getattr(px, "scatter_map", getattr(px, "scatter_mapbox", None))
    style_key = "map_style" if hasattr(px, "scatter_map") else "mapbox_style"
    map_kwargs = {
        "lat": "lat",
        "lon": "lon",
        "size": "marker_size",
        "color": "risk_tier",
        "color_discrete_map": TIER_COLORS,
        "category_orders": {"risk_tier": list(TIER_COLORS)},
        "hover_name": "zone",
        "hover_data": {
            "risk_tier": True,
            "prob_txt": True,
            "rain_30m": ":.1f",
            "rain_decay_72h": False,
            "marker_size": False,
            "lat": False,
            "lon": False,
        },
        "size_max": 28,
        "zoom": 10.5,
        "center": {"lat": 1.3521, "lon": 103.8198},
        style_key: tokens["map_style"],
    }
    # Plotly's map hides markers of size exactly 0 (before sizemin applies), so floor at 0.01.
    map_df = map_df.assign(
        marker_size=map_df["rain_30m"].clip(lower=0.01).fillna(0.01),
        prob_txt=[pct(p, tier_thresholds) for p in map_df["flood_probability"]],
    )
    fig_map = map_func(map_df, **map_kwargs)  # type: ignore[misc]

    # Clean tooltip formatting
    for trace in fig_map.data:
        if trace.name == NO_DATA_TIER:  # no probability and no rain figure: neither is known
            trace.update(
                hovertemplate=(
                    "<b>ZONE: %{hovertext}</b><br>"
                    f"<span style='color:{tokens['text_muted']};'>No gauge data: risk unknown</span>"
                    "<extra></extra>"
                ),
            )
        elif trace.name in TIER_COLORS:
            trace.update(
                hovertemplate=(
                    "<b>ZONE: %{hovertext}</b><br>"
                    f"<span style='color:{tokens['text_muted']};'>Risk Classification:</span> %{{customdata[0]}}<br>"
                    f"<span style='color:{tokens['text_muted']};'>Flood Probability:</span> %{{customdata[1]}}<br>"
                    f"<span style='color:{tokens['text_muted']};'>Max 30m Rain:</span> %{{customdata[2]:.1f}} mm<extra></extra>"
                ),
            )

    # Fixed marker scale (40 mm in 30 min = largest), so sizes compare across times; dry zones
    # keep a small visible marker. Plotly's default scales to the wettest zone in view.
    for trace in fig_map.data:
        if trace.name in TIER_COLORS:
            trace.update(marker=dict(sizemode="area", sizeref=2 * 40 / 28**2, sizemin=6))

    # Radar view shows only the radar; risk view shows zones, gauges and outlines (never both).
    if radar_mode:
        for trace in fig_map.data:
            trace.visible = False

    # Weather station / sensor dots layer: cool cyan dots
    stns = load_station_snapshot()
    if stns and not radar_mode:
        stn_lats = [s["lat"] for s in stns.values()]
        stn_lons = [s["lon"] for s in stns.values()]
        stn_names = [
            f"<b>NEA rain gauge {sid}</b><br>"
            f"<span style='color:{tokens['text_muted']};'>{s.get('name', sid)}</span>"
            for sid, s in stns.items()
        ]
        trace_cls = getattr(go, "Scattermap", getattr(go, "Scattermapbox", None))
        if trace_cls:
            fig_map.add_trace(
                trace_cls(
                    lat=stn_lats,
                    lon=stn_lons,
                    mode="markers",
                    marker=dict(size=4, color="#94A3B8" if IS_DARK else "#64748B", opacity=0.7),
                    name="NEA rain gauges (locations)",
                    hoverinfo="text",
                    hovertext=stn_names,
                )
            )

    # 55 URA planning area polygon boundaries: thin outlines
    ura_geojson = load_ura_boundaries()
    map_layers: list[dict[str, Any]] = []
    if ura_geojson and not radar_mode:
        map_layers.append(
            {
                "sourcetype": "geojson",
                "source": ura_geojson,
                "type": "line",
                "color": "rgba(100, 116, 139, 0.45)" if IS_DARK else "rgba(148, 163, 184, 0.45)",
                "line": {"width": 1.0},
            }
        )

    if radar is not None and radar_layer in radar.images:
        map_layers.insert(
            0,
            {
                "sourcetype": "image",
                "source": radar.data_uri(radar_layer),
                "coordinates": radar.corners(),
                "opacity": 0.75,
                "below": "traces",
            },
        )

    map_layout_key = "map" if hasattr(px, "scatter_map") else "mapbox"
    fig_map.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=500,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(
            bgcolor=tokens["bg_surface"],
            bordercolor=tokens["border_main"],
            font_family="Inter, sans-serif",
            font_size=12,
            font_color=tokens["text_main"],
        ),
        legend=dict(
            itemsizing="constant",
            yanchor="top",
            y=0.98,
            xanchor="left",
            x=0.02,
            bgcolor=f"rgba({17 if IS_DARK else 255}, {24 if IS_DARK else 255}, {39 if IS_DARK else 255}, 0.92)",
            bordercolor=tokens["border_main"],
            borderwidth=1,
            font=dict(family="Inter, sans-serif", size=11, color=tokens["text_main"]),
            title=dict(
                text="RISK IN 1 HOUR"
                if forecast_on and radar is not None
                else "RISK CLASSIFICATION",
                font=dict(family="Inter, sans-serif", size=10, color=tokens["text_muted"]),
            ),
        ),
        showlegend=False,  # the slim legend bar under the map replaces it
        **{map_layout_key: {"style": tokens["map_style"], "layers": map_layers}},
    )
    # No "Pan" button: dragging the map already pans it.
    st.plotly_chart(fig_map, config={"modeBarButtonsToRemove": ["pan2d"]})
    if radar_mode:
        stops = ", ".join(f"rgb{c}" for c in RADAR_COLOURS)
        ticks = "".join(f'<span style="left:{pos}%;">{label}</span>' for pos, label in RADAR_TICKS)
        st.markdown(
            '<div class="fs-legend"><div class="fs-legend-title">Rain rate (mm/h), radar'
            + (f" · {radar_layer}" if radar_layer and radar_layer != "Now" else "")
            + f'</div><div class="fs-legend-bar" style="background: linear-gradient(90deg, {stops});">'
            f'</div><div class="fs-legend-ticks">{ticks}</div></div>',
            unsafe_allow_html=True,
        )
    else:
        segs = "".join(
            f'<div style="flex:1; background:{TIER_COLORS[t]};"></div>'
            for t in ("Low", "Moderate", "High")
        )
        title = "Risk in 1 hour" if forecast_on and radar is not None else "Risk of flood"
        ticks = '<span style="left:16.7%;">Low</span>'
        if tier_thresholds:
            ticks += f'<span style="left:33.3%;">{pct(tier_thresholds["moderate"])}</span>'
        ticks += '<span style="left:50%;">Moderate</span>'
        if tier_thresholds:
            ticks += f'<span style="left:66.7%;">{pct(tier_thresholds["high"])}</span>'
        ticks += '<span style="left:83.3%;">High</span>'
        no_data_key = (
            '<div class="fs-legend-nodata"><span style="background:'
            f'{TIER_COLORS[NO_DATA_TIER]};"></span>No data: too few gauges reporting</div>'
            if (map_df["risk_tier"] == NO_DATA_TIER).any()
            else ""
        )
        st.markdown(
            f'<div class="fs-legend"><div class="fs-legend-title">{title} · chance of a reported '
            f'flood in the next hour</div><div class="fs-legend-bar">{segs}</div>'
            f'<div class="fs-legend-ticks">{ticks}</div>{no_data_key}</div>',
            unsafe_allow_html=True,
        )
        st.caption("If the map background doesn't load, the coloured areas still show risk.")


with detail_col, st.container(border=True, key="zone_card"):
    st.markdown(
        f'<div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">'
        f'  <div style="display: flex; align-items: center; gap: 8px;">'
        f"    <span style=\"font-size: 1.15rem; font-weight: 700; color: {tokens['text_main']}; font-family: 'Inter', sans-serif;\">Zone Diagnostic</span>"
        f"  </div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    # df_results has a row for every zone (an absent one reads "No data"), so this can't be empty.
    zone_data = df_results[df_results["zone"] == selected_zone].iloc[0]
    zone_no_data = zone_data["risk_tier"] == NO_DATA_TIER

    tier_label = {
        "High": "HIGH RISK",
        "Moderate": "MODERATE RISK",
        "Low": "LOW RISK",
        NO_DATA_TIER: "No gauge data — risk unknown",
    }[zone_data["risk_tier"]]
    tier_bg, tier_border, tier_text_color = tier_style(zone_data["risk_tier"], IS_DARK)

    st.markdown(
        f'<div style="margin: 8px 0 12px 0;">'
        f'  <div style="font-weight: 600; color: {tokens["text_main"]}; font-size: 0.95rem;">'
        f'📍 {selected_zone.title()} <span style="color: {tokens["text_muted"]}; font-weight: 500;">'
        f"· {zone_data['region']} Region · change it in the sidebar</span></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div style="background: {tier_bg}; border: 1px solid {tier_border}; border-radius: 8px; padding: 10px 14px; margin: 10px 0 14px 0; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">'
        f'  <div style="display: flex; align-items: center; gap: 10px;">'
        f'    <span style="color: {tier_text_color}; font-size: 1.2rem; font-weight: 700;">{"?" if zone_no_data else "✓" if zone_data["risk_tier"] == "Low" else "!"}</span>'
        f"    <div>"
        f"      <div style=\"font-size: 0.65rem; font-weight: 700; color: {tokens['text_muted']}; text-transform: uppercase; letter-spacing: 0.05em; font-family: 'Inter', sans-serif;\">STATUS</div>"
        f"      <div style=\"font-size: 0.95rem; font-weight: 700; color: {tier_text_color}; font-family: 'Inter', sans-serif;\">{tier_label}</div>"
        f"    </div>"
        f"  </div>"
        + (
            ""
            if zone_no_data
            else f"  <span style=\"background: {tokens['bg_surface']}; color: {tier_text_color}; border: 1px solid {tier_border}; border-radius: 9999px; padding: 3px 10px; font-size: 0.75rem; font-weight: 600; font-family: 'JetBrains Mono', monospace; white-space: nowrap;\">{pct(zone_data['flood_probability'], tier_thresholds)} chance</span>"
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    if zone_data["coverage"] == "partial" and not zone_no_data:
        st.caption(
            "Some readings in the last hour are missing, so rain totals and risk may read too low."
        )
    show_pub_alerts(active_pub_alerts, selected_zone)

    if radar_mode and radar is not None:
        zr = radar.zones.set_index("ura_planning_area").loc[selected_zone]
        st.caption(
            f"**Radar:** {zr['radar_now_mm_h']:.1f} mm/h falling here now.  \n"
            f"Forecast: **{zr['nowcast_30_mm']:.1f} mm** in the next 30 min, "
            f"**{zr['nowcast_60_mm']:.1f} mm** in the next hour."
        )
    elif not zone_no_data:
        levels = (
            f"are low: Moderate from {pct(tier_thresholds['moderate'])}, "
            f"High from {pct(tier_thresholds['high'])}."
            if tier_thresholds
            else "are low."
        )
        st.caption(
            f"Chance a flood is reported here in the next 60 min: "
            f"**{pct(zone_data['flood_probability'], tier_thresholds)}**. Flood reports are rare, "
            f"so the alert levels {levels}"
        )

    def rain_txt(col: str, spec: str) -> str:
        """A rain figure, or a dash where there is no gauge data (0 would read as dry)."""
        value = zone_data[col]
        return "—" if zone_no_data or pd.isna(value) else format(value, spec)

    # Rolling rainfall and wet-ground tiles
    st.markdown(
        f'<div style="display: flex; justify-content: space-between; align-items: center; margin: 14px 0 8px 0;">'
        f"  <span style=\"font-size: 0.7rem; font-weight: 700; color: {tokens['text_muted']}; text-transform: uppercase; letter-spacing: 0.05em; font-family: 'Inter', sans-serif;\">ROLLING RAINFALL & WET-GROUND</span>"
        f"  <span style=\"font-size: 0.72rem; font-weight: 600; color: {tokens['accent']}; font-family: 'Inter', sans-serif;\">{'Live' if mode == 'Live Feed' else 'Replay'} · {view_time:%H:%M} SGT</span>"
        f"</div>"
        f'<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px;">'
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">5-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{rain_txt("rain_5m", ".2f")} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">15-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{rain_txt("rain_15m", ".2f")} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">30-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{rain_txt("rain_30m", ".2f")} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">60-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{rain_txt("rain_60m", ".2f")} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">120-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{rain_txt("rain_120m", ".2f")} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">Wet-Ground</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{rain_txt("rain_decay_72h", ".1f")} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">idx</span></div>'
        f"  </div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    fitted = getattr(model, "rarity_quantiles", None)
    # Not shown without gauge data (nothing to rank), nor for gold without a local model: the
    # uncalibrated 0.0 fallback would read as "not rare" next to a tier scored elsewhere.
    if zone_no_data:
        st.caption("No gauge data, so there is no storm to rank.")
    elif fitted or not from_gold:
        if fitted:
            rarity = float(
                rarity_scores(df_results[df_results["zone"] == selected_zone], fitted)[0]
            )
            rarity_title = "Storm Rarity Percentile"
            rarity_basis = "of rainy half-hours in this zone, 2017–2023"
        else:
            rarity = float(zone_data.get("storm_rarity_score", 0.0))
            rarity_title = "Storm Rarity Percentile (uncalibrated)"
            rarity_basis = "against hand-set cut-offs: no trained model loaded"
        zone_partial = zone_data["coverage"] == "partial"
        if zone_data["rain_30m"] <= 0 and zone_partial:
            rarity_status = "Rain readings missing"
        elif zone_data["rain_30m"] <= 0:
            rarity_status = "No rain in the last 30 min"
        elif rarity < 0.7:
            rarity_status = "Typical for rain here"
        elif rarity < 0.9:
            rarity_status = "Heavier than usual"
        else:
            rarity_status = "Rare for this zone"

        # The title lives in the page, not the chart: a chart title stays small and lands inside the
        # arc when the chart is opened full screen.
        st.markdown(
            '<div style="display: flex; justify-content: space-between; align-items: center; margin: 4px 0 0 0;">'
            f"  <span style=\"font-size: 0.7rem; font-weight: 700; color: {tokens['text_muted']}; text-transform: uppercase; letter-spacing: 0.05em; font-family: 'Inter', sans-serif;\">{rarity_title}</span>"
            f"  <span style=\"font-size: 0.72rem; font-weight: 600; color: {tokens['accent']}; font-family: 'Inter', sans-serif;\">{rarity_status}</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        fig_gauge = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=rarity * 100,
                number={
                    "font": {
                        "family": "Inter, sans-serif",
                        "size": 34,
                        "color": tokens["gauge_num"],
                    },
                    "valueformat": ".1f",
                    "suffix": "%",
                },
                gauge={
                    "axis": {
                        "range": [0, 100],
                        "tickcolor": tokens["gauge_tick"],
                        "tickfont": {
                            "family": "Inter, sans-serif",
                            "size": 11,
                            "color": tokens["text_muted"],
                        },
                    },
                    "bar": {
                        "color": "#0EA5E9"
                        if rarity < 0.7
                        else ("#F59E0B" if rarity < 0.9 else "#EF4444"),
                        "thickness": 0.26,
                    },
                    "steps": [
                        {"range": [0, 70], "color": tokens["gauge_step_0_70"]},
                        {"range": [70, 90], "color": tokens["gauge_step_70_90"]},
                        {"range": [90, 100], "color": tokens["gauge_step_90_100"]},
                    ],
                },
            )
        )
        fig_gauge.update_layout(
            height=210,
            margin={"t": 20, "b": 10, "l": 30, "r": 30},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        # No full-screen or download toolbar: a half-dial gains nothing from full screen, where its
        # text stays small while the arc grows.
        with st.container(key="rarity_gauge"):
            st.plotly_chart(fig_gauge, config={"displayModeBar": False})
        st.caption(
            "Some readings are missing, so there is no reliable rain total to rank."
            if zone_data["rain_30m"] <= 0 and zone_partial
            else "No rain here in the last 30 minutes, so there is no storm to rank."
            if zone_data["rain_30m"] <= 0
            else f"The last 30 minutes here were heavier than **{rarity * 100:.1f}%** {rarity_basis}."
        )
        st.caption("Risk uses rain in the last 60 minutes only.")


# --- PUBLIC TRANSPORT AT RISK -------------------------------------------------------------
# With each station's chance; {} when the alert levels aren't known (gold without a local model).
public_transport(selected_zone, active_pub_alerts, tier_thresholds or {})

# --- HOW IT WAS TESTED (COLLAPSED) -----------------------------------------------------------
# The live model's (version 4) validation results in three plain points, and, on a replayed day,
# the sources for the floods the headline lists. Figures: models/candidates/
# cutoff_comparison_2026-10-22.json (92 labels, 30 floods in the 2020–2023 folds; the set version 4
# was signed off on); version 4 has no score on the 2024–2026 test.
_n_events = len(flood_events())
event_count_text = f"{_n_events} sourced events" if _n_events else "sourced events"
with st.expander("How it was tested"):
    st.markdown(
        "- **Checked on floods it hadn't seen.** In cross-validation on 2020–2023 (each year "
        "scored by a model fitted only on the years before it), the live model's method and "
        "thresholds caught 20 of 30 reported floods at Moderate (90% CI 53–80%) and 15 of 30 at "
        "High (37–67%).\n"
        "- **Warnings come before the report.** The median warning was 26 min ahead at Moderate "
        "and 45 min at High. At Moderate, every flood the previous cut-off also caught is warned "
        "at the same time or earlier; one flood caught only now came late, which lowers the "
        "median.\n"
        "- **False alarms per area per year:** about 7.2 at Moderate (budget 11) and 2.4 at High "
        "(budget 2.5). A false "
        "alarm here is one with no *reported* flood after it, and many floods go unreported, so "
        "this overstates them."
    )
    st.caption(
        "The 2024–2026 test set was scored once, with version 1: 21 of 30 floods at Moderate "
        "(90% CI 57–83%), 12 of 30 at High (27–53%), median warnings 15 and 10 min. The live "
        "model (version 4) has not been scored on it, and the two sets of figures aren't directly "
        f"comparable. Flood labels: {event_count_text}, each with a source link, a quoted "
        "sentence and a human sign-off."
    )
    if day_events is not None:
        if day_events:
            lines = []
            for e in day_events:
                when = (
                    "time not reported"
                    if e.time_precision == "day_only"
                    else f"{e.timestamp_start:%H:%M}"
                    + (
                        ""
                        if e.time_precision == "exact"
                        else " (PUB alert time)"
                        if e.time_precision == "pub_issued"
                        else " (approx.)"
                    )
                )
                lines.append(
                    f"- **{when}** · {e.ura_planning_area.title()}: {e.location_raw} "
                    f"([{e.source_name}]({e.source_url}))"
                )
            st.markdown("**Reported floods this day:**\n" + "\n".join(lines))
        else:
            st.markdown(
                f"**Reported floods this day:** none in our {event_count_text}. Many floods are "
                "never reported, so this doesn't mean none happened."
            )

# --- FOOTER --------------------------------------------------------------------------------
st.markdown(
    f'<div style="padding: 24px 0 16px 0; border-top: 1px solid {tokens["border_main"]}; margin-top: 32px; '
    f"color: {tokens['text_muted']}; font-size: 0.78rem; font-family: 'Inter', sans-serif;\">"
    "FloodSense · flash-flood risk for Singapore's 55 planning areas · Rainfall: NEA via "
    "data.gov.sg · Boundaries: URA Master Plan 2019"
    "</div>",
    unsafe_allow_html=True,
)
