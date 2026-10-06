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
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from shapely.geometry import mapping, shape

from floodsense.app import replay_days
from floodsense.common.config import settings
from floodsense.common.schemas import FloodEvent, StationMetadata
from floodsense.data.flood_events import load_flood_events
from floodsense.data.replay import load_replay
from floodsense.data.transport import SOURCE_NAME as MRT_SOURCE
from floodsense.data.transport import stations_at_risk
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.ingestion.flood_alerts import FloodAlert, active_alerts, fetch_flood_alerts
from floodsense.ingestion.poller import LiveFeedUnavailable, NEAPoller
from floodsense.models.artifact import load_model, rarity_scores
from floodsense.models.scoring import default_thresholds, score_zone_features
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS, load_station_snapshot

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
.st-key-theme_toggle {{
    position: absolute !important;
    top: 0.7rem !important;
    right: 1.25rem !important;
    width: auto !important;
    z-index: 999990 !important;
    background: var(--bg-surface) !important;
    border: 1px solid var(--border-main) !important;
    border-radius: 9999px !important;
    padding: 4px 12px !important;
    box-shadow: var(--card-shadow) !important;
}}

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

button[data-variant="segmented_control"][aria-checked="true"],
button[data-variant="segmented_control"][aria-pressed="true"],
button[data-testid="stButtonGroupButton"][aria-pressed="true"] {{
    background-color: var(--bg-surface) !important;
    color: var(--text-main) !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2) !important;
    border: 1px solid var(--border-subtle) !important;
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

.stCaption, [data-testid="stCaptionContainer"] {{
    color: var(--text-muted) !important;
    font-size: 0.78rem !important;
}}
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


TIER_COLORS = {
    "High": "#EF4444",  # Signal Crimson
    "Moderate": "#F59E0B",  # Industrial Amber
    "Low": "#0EA5E9",  # Sky Blue
}
NUM_ZONES = len(URA_PLANNING_AREAS)
WET_GROUND_HELP = (
    "Recent rain, with each millimetre counting half as much after every "
    f"{settings.decay_half_life_hours:g} hours. An index of how wet the ground is, not a rain total."
)
LIVE_WET_GROUND_NOTE = (
    f" In Live mode it is built from only the last {settings.live_history_hours:g} hours of "
    "readings, so it reads low."
)
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


def zone_picker() -> str:
    """The planning-area selector for the Zone Diagnostic (sidebar)."""
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
    latest = table[table["timestamp"] == table["timestamp"].max()].reset_index(drop=True)
    return latest, len(stations), None


@st.cache_data(ttl=120, show_spinner=False)
def live_flood_alerts() -> tuple[list[FloodAlert] | None, str | None]:
    """PUB flash-flood alerts still active from the last 3 hours, or (None, error message)."""
    since = datetime.now(settings.tzinfo) - timedelta(hours=3)
    try:
        return active_alerts(fetch_flood_alerts(since=since)), None
    except LiveFeedUnavailable as exc:
        return None, str(exc)


LIVE_REFRESH = timedelta(minutes=5)


@st.fragment(run_every=LIVE_REFRESH)
def live_auto_refresh() -> None:
    """Re-run the whole app with fresh readings every LIVE_REFRESH while Live Feed is open."""
    loaded_at = st.session_state.setdefault("live_loaded_at", time.monotonic())
    if time.monotonic() - loaded_at >= LIVE_REFRESH.total_seconds() - 10:
        st.session_state["live_loaded_at"] = time.monotonic()
        live_features.clear()
        live_flood_alerts.clear()
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

st.session_state.setdefault("mode", "Live Feed")
mode = st.sidebar.segmented_control(
    "Mode",
    options=["Live Feed", "Replay Storm"],
    key="mode",
    help="Live readings from data.gov.sg, or a replay of any past day's real readings.",
)

st.sidebar.markdown("---")

# Replay has two steps in the sidebar: choose a date ("choose"), then simulate it ("simulate",
# with the area and time controls). Streamlit drops a widget's value while the widget is hidden,
# so re-store the values of the widgets the other step hides.
st.session_state.setdefault("zone", "BUKIT TIMAH")
st.session_state.setdefault("replay_stage", "choose")
for _key in list(st.session_state):
    if _key in ("zone", "replay_date") or str(_key).startswith("replay-time-"):
        st.session_state[_key] = st.session_state[_key]
selected_zone = str(st.session_state["zone"])
if mode == "Live Feed":
    selected_zone = zone_picker()


# --- TOP HEADER & APPEARANCE SWITCH --------------------------------------------------------
def render_app_header() -> None:
    with st.container(key="theme_toggle"):  # pinned to the top-right corner (CSS)
        is_dark_active = st.toggle("Light/Dark Mode", key="dark_mode_switch")
    with st.container(key="app_header"):
        st.title("FloodSense Intelligence Center")
    if is_dark_active != IS_DARK:
        st.session_state["theme"] = "dark" if is_dark_active else "light"
        st.rerun()


render_app_header()

# --- DATA FOR THE SELECTED VIEW ------------------------------------------------------------
replay_warning: str | None = None
day_events: list[FloodEvent] | None = None  # reported floods for the replayed day
if mode == "Live Feed":
    features, total_stations, live_error = live_features()
    if features is None:
        st.error(f"Live feed unavailable: {live_error}", icon=":material/cloud_off:")
        st.info(
            "No data is shown rather than substituting simulated rain. Switch to **Replay Storm**, "
            "or retry. Setting `FLOODSENSE_DATA_GOV_API_KEY` avoids anonymous rate limits."
        )
        col_fb1, col_fb2 = st.columns(2)
        with col_fb1:
            st.button(
                "⚡ Switch to Replay Storm (17 Apr 2021)",
                type="primary",
                on_click=_replay_pinned_storm,
                use_container_width=True,
            )
        with col_fb2:
            if st.button("🔄 Retry Live Feed", use_container_width=True):
                live_features.clear()
                st.rerun()
        st.stop()
    view_time = features["timestamp"].iloc[0]
    live_bg = "rgba(16, 185, 129, 0.15)" if IS_DARK else "#ECFDF5"
    live_border = "rgba(16, 185, 129, 0.35)" if IS_DARK else "#A7F3D0"
    live_color = "#34D399" if IS_DARK else "#059669"
    live_badge = (
        f'<span style="background: {live_bg}; border: 1px solid {live_border}; '
        f"color: {live_color}; padding: 4px 12px; border-radius: 9999px; font-size: 0.78rem; "
        f"font-family: 'Inter', sans-serif; font-weight: 600;\">"
        f"● Live: {int(features['reporting_stations'].iloc[0])} of {total_stations} gauges "
        "reporting (data.gov.sg)</span>"
    )
    status_line = (
        f"{live_badge} &nbsp;·&nbsp; <span style='color: {c_muted}; font-size: 0.8rem; font-family: \"Inter\", sans-serif; font-weight: 500;'>Latest Reading:</span> "
        f"`{view_time:%d %b %Y %H:%M} SGT`"
    )
    history_note = (
        f"Rolling features use the last {settings.live_history_hours:g} h of live readings, so the "
        "72-hour wet-ground index is understated."
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
            help=f"Any day with NEA gauge readings, {first_day:%d %b %Y} to {last_day:%d %b %Y}.",
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
            help="NEA has no readings for this day.",
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
            help="Step through the day's real readings, zone by zone.",
        )
        chosen = st.session_state[time_key]
    else:
        st.sidebar.markdown(f"**Simulating {replay_day:%d %b %Y}**")
        selected_zone = zone_picker()
        chosen = st.sidebar.select_slider(
            f"Replay time (SGT, {replay_day:%d %b %Y})",
            options=list(times),
            key=time_key,
            help="Opens at the day's heaviest island-wide 30-minute rain.",
        )
        st.sidebar.markdown("---")
        st.sidebar.button(
            "Choose another date",
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
    history_note = "Features include the 72 hours of real readings before the day."
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
        help="Real NEA readings. The rainfall store is not available here, so only this storm "
        "can be replayed.",
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
    history_note = "Features include the 72 hours of real readings before the replay window."
    day_events = replay_days.events_on(replay_days.PINNED_STORM, flood_events())

df_results = score_zone_features(features, model).merge(ZONE_META, on="ura_planning_area")
df_results["zone"] = df_results["ura_planning_area"]
reporting_stations = int(features["reporting_stations"].iloc[0])

# --- TOP BAR & SCOPE INDICATORS -----------------------------------------------------------
st.markdown(status_line, unsafe_allow_html=True)
st.markdown(
    f'<div style="background: {tokens["badge_bg"]}; border: 1px solid {tokens["badge_border"]}; border-radius: 9999px; '
    f"padding: 5px 16px; margin: 8px 0 18px 0; display: inline-flex; align-items: center; "
    f"gap: 8px; font-size: 0.78rem; font-family: 'Inter', sans-serif; color: {tokens['badge_text']};\">"
    "  <span>📍 Singapore Urban Flash-Flood Risk</span>"
    f'  <span style="color: {tokens["text_dim"]};">•</span>'
    "  <span>55 URA Planning Areas</span>"
    "</div>",
    unsafe_allow_html=True,
)
if replay_warning:
    st.warning(replay_warning, icon=":material/warning:")

active_pub_alerts: list[FloodAlert] = []  # live PUB alerts (none in Replay: no history)
if mode == "Live Feed":
    live_auto_refresh()
    pub_alerts, pub_alerts_error = live_flood_alerts()
    active_pub_alerts = pub_alerts or []
    if pub_alerts_error:
        st.caption(f"PUB flood alerts unavailable right now: {pub_alerts_error}")
    elif pub_alerts:
        tiers = dict(zip(df_results["ura_planning_area"], df_results["risk_tier"], strict=True))
        lines = [
            f"- **{a.zone or a.area_desc}** · {a.issued_at:%H:%M} · {a.description}"
            + (f" *(FloodSense: {tiers[a.zone]})*" if a.zone in tiers else "")
            for a in pub_alerts
        ]
        st.error(
            f"**{len(pub_alerts)} active PUB flash-flood alert(s)**\n\n" + "\n".join(lines),
            icon=":material/flood:",
        )
    else:
        st.caption(
            ":material/check_circle: No active PUB flash-flood alerts in the last 3 hours "
            "(PUB via data.gov.sg; refreshes every 2 min). The page reloads live readings every "
            f"{LIVE_REFRESH.total_seconds() / 60:g} min."
        )

high_risk_count = int((df_results["risk_tier"] == "High").sum())
mod_risk_count = int((df_results["risk_tier"] == "Moderate").sum())
peak = df_results.sort_values("rain_30m", ascending=False).iloc[0]
wettest = df_results.sort_values("rain_decay_72h", ascending=False).iloc[0]

# --- 5 TOP KPI METRIC CARDS ---------------------------------------------------------------
# Every card has the same three parts, each one line: label, value, footer (label | value).
_LABEL = (
    f"font-size:0.68rem; font-family:'Inter', sans-serif; font-weight:700; color:{tokens['text_muted']}; "
    "text-transform:uppercase; letter-spacing:0.05em; white-space:nowrap; overflow:hidden; "
    "text-overflow:ellipsis; display:block;"
)


def card_header(label: str) -> None:
    st.markdown(
        f'<div style="margin-bottom:4px;"><span style="{_LABEL}">{label}</span></div>',
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


tier_thresholds = getattr(model, "thresholds", None) or default_thresholds()
share = reporting_stations / total_stations if total_stations else 0.0
kpi_cols = st.columns(5)
with kpi_cols[0]:
    kpi_card(
        "High risk zones",
        "High Risk",
        f"{high_risk_count} / {NUM_ZONES}",
        ("High from", f"{tier_thresholds['high']:.2%}"),
        help="Zones whose chance of a reported flood in the next hour is at or above this.",
    )
with kpi_cols[1]:
    kpi_card(
        "Moderate risk zones",
        "Moderate Risk",
        f"{mod_risk_count} / {NUM_ZONES}",
        ("Moderate from", f"{tier_thresholds['moderate']:.2%}"),
        help="Zones whose chance of a reported flood in the next hour is at or above this.",
    )
with kpi_cols[2]:
    kpi_card(
        "Max 30-min rain",
        "Max 30-min Rain",
        f"{peak['rain_30m']:.0f} mm",
        ("Heaviest", peak["zone"].title()) if peak["rain_30m"] > 0 else ("Heaviest", "No rain"),
    )
with kpi_cols[3]:
    kpi_card(
        "Wet-ground index",
        "Wet-Ground Index",
        f"{wettest['rain_decay_72h']:.0f}",
        ("Wettest", wettest["zone"].title())
        if wettest["rain_decay_72h"] > 0
        else ("Wettest", "All dry"),
        help=WET_GROUND_HELP + (LIVE_WET_GROUND_NOTE if mode == "Live Feed" else ""),
    )
with kpi_cols[4]:
    kpi_card(
        "Gauges reporting",
        "Gauges Live",
        f"{reporting_stations} / {total_stations}",
        ("Reporting now", f"{share:.0%}"),
        help="Zone rainfall weights re-balance automatically over the gauges that reported.",
    )

# --- MAP & ZONE DETAILS --------------------------------------------------------------------
map_col, detail_col = st.columns([1.7, 1.3])

with map_col, st.container(border=True):
    st.markdown(
        f'<div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 2px;">'
        f'  <div style="display: flex; align-items: center; gap: 8px;">'
        f"    <span style=\"font-size: 1.15rem; font-weight: 700; color: {tokens['text_main']}; font-family: 'Inter', sans-serif;\">Singapore Urban Risk Map</span>"
        f"    <span style=\"background: {tokens['bg_subtle']}; border: 1px solid {tokens['border_main']}; color: {tokens['text_muted']}; font-family: 'Inter', sans-serif; font-size: 0.72rem; font-weight: 600; padding: 2px 8px; border-radius: 9999px;\">55 URA ZONES</span>"
        f"  </div>"
        f"</div>"
        f"<div style=\"font-size: 0.78rem; color: {tokens['text_muted']}; margin-bottom: 12px; font-family: 'Inter', sans-serif;\">"
        f"Rainfall at each zone interpolated from NEA rain gauges (inverse-distance weighting)."
        f"</div>"
        f"<div style=\"display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; font-family: 'Inter', sans-serif;\">"
        f'  <span style="background: {tokens["bg_subtle"]}; border: 1px solid {tokens["border_main"]}; color: {tokens["text_muted"]}; padding: 4px 12px; border-radius: 9999px; font-size: 0.75rem; font-weight: 500;"><span style="color:#0EA5E9;">●</span> Low ({NUM_ZONES - high_risk_count - mod_risk_count})</span>'
        f'  <span style="background: {tokens["bg_subtle"]}; border: 1px solid {tokens["border_main"]}; color: {tokens["text_muted"]}; padding: 4px 12px; border-radius: 9999px; font-size: 0.75rem; font-weight: 500;"><span style="color:#F59E0B;">●</span> Moderate ({mod_risk_count})</span>'
        f'  <span style="background: {tokens["bg_subtle"]}; border: 1px solid {tokens["border_main"]}; color: {tokens["text_muted"]}; padding: 4px 12px; border-radius: 9999px; font-size: 0.75rem; font-weight: 500;"><span style="color:#EF4444;">●</span> High ({high_risk_count})</span>'
        f'  <span style="background: {tokens["accent_muted"]}; border: 1px solid {tokens["accent"]}; color: {tokens["accent"]}; padding: 4px 12px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600;">{reporting_stations} of {total_stations} gauges reporting</span>'
        f"</div>",
        unsafe_allow_html=True,
    )
    map_func = getattr(px, "scatter_map", getattr(px, "scatter_mapbox", None))
    style_key = "map_style" if hasattr(px, "scatter_map") else "mapbox_style"
    map_kwargs = {
        "lat": "lat",
        "lon": "lon",
        "size": "rain_30m",
        "color": "risk_tier",
        "color_discrete_map": TIER_COLORS,
        "category_orders": {"risk_tier": list(TIER_COLORS)},
        "hover_name": "zone",
        "hover_data": {
            "risk_tier": True,
            "flood_probability": ":.4f",
            "rain_30m": ":.1f",
            "rain_decay_72h": False,
            "lat": False,
            "lon": False,
        },
        "size_max": 28,
        "zoom": 10.5,
        "center": {"lat": 1.3521, "lon": 103.8198},
        style_key: tokens["map_style"],
    }
    fig_map = map_func(df_results, **map_kwargs)  # type: ignore[misc]

    # Clean tooltip formatting
    for trace in fig_map.data:
        if trace.name in TIER_COLORS:
            trace.update(
                hovertemplate=(
                    "<b>ZONE: %{hovertext}</b><br>"
                    f"<span style='color:{tokens['text_muted']};'>Risk Classification:</span> %{{customdata[0]}}<br>"
                    f"<span style='color:{tokens['text_muted']};'>Flood Probability:</span> %{{customdata[1]:.2%}}<br>"
                    f"<span style='color:{tokens['text_muted']};'>Max 30m Rain:</span> %{{customdata[2]:.1f}} mm<extra></extra>"
                ),
            )

    # Weather station / sensor dots layer: cool cyan dots
    stns = load_station_snapshot()
    if stns:
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
    map_layers = []
    if ura_geojson:
        map_layers.append(
            {
                "sourcetype": "geojson",
                "source": ura_geojson,
                "type": "line",
                "color": "rgba(100, 116, 139, 0.45)" if IS_DARK else "rgba(148, 163, 184, 0.45)",
                "line": {"width": 1.0},
            }
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
            yanchor="top",
            y=0.98,
            xanchor="left",
            x=0.02,
            bgcolor=f"rgba({17 if IS_DARK else 255}, {24 if IS_DARK else 255}, {39 if IS_DARK else 255}, 0.92)",
            bordercolor=tokens["border_main"],
            borderwidth=1,
            font=dict(family="Inter, sans-serif", size=11, color=tokens["text_main"]),
            title=dict(
                text="RISK CLASSIFICATION",
                font=dict(family="Inter, sans-serif", size=10, color=tokens["text_muted"]),
            ),
        ),
        **{map_layout_key: {"style": tokens["map_style"], "layers": map_layers}},
    )
    st.plotly_chart(fig_map)
    st.caption(
        f"{model_caption} {history_note} Each risk marker sits inside its URA planning area; "
        "grey outlines are the URA Master Plan 2019 boundaries, small grey dots NEA gauge locations."
    )

with detail_col, st.container(border=True):
    st.markdown(
        f'<div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">'
        f'  <div style="display: flex; align-items: center; gap: 8px;">'
        f"    <span style=\"font-size: 1.15rem; font-weight: 700; color: {tokens['text_main']}; font-family: 'Inter', sans-serif;\">Zone Diagnostic</span>"
        f"  </div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    zone_data = df_results[df_results["zone"] == selected_zone].iloc[0]

    tier_label = {
        "High": "HIGH RISK",
        "Moderate": "MODERATE RISK",
        "Low": "LOW RISK",
    }[zone_data["risk_tier"]]
    tier_bg, tier_border, tier_text_color = tier_style(zone_data["risk_tier"], IS_DARK)

    st.markdown(
        f'<div style="margin: 8px 0 12px 0;">'
        f'  <div style="font-weight: 600; color: {tokens["text_main"]}; font-size: 0.95rem;">'
        f'📍 {selected_zone.title()} <span style="color: {tokens["text_muted"]}; font-weight: 500;">'
        f"· {zone_data['region']} Region · change in the sidebar</span></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div style="background: {tier_bg}; border: 1px solid {tier_border}; border-radius: 8px; padding: 10px 14px; margin: 10px 0 14px 0; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">'
        f'  <div style="display: flex; align-items: center; gap: 10px;">'
        f'    <span style="color: {tier_text_color}; font-size: 1.2rem; font-weight: 700;">{"✓" if zone_data["risk_tier"] == "Low" else "!"}</span>'
        f"    <div>"
        f"      <div style=\"font-size: 0.65rem; font-weight: 700; color: {tokens['text_muted']}; text-transform: uppercase; letter-spacing: 0.05em; font-family: 'Inter', sans-serif;\">STATUS</div>"
        f"      <div style=\"font-size: 0.95rem; font-weight: 700; color: {tier_text_color}; font-family: 'Inter', sans-serif;\">{tier_label}</div>"
        f"    </div>"
        f"  </div>"
        f"  <span style=\"background: {tokens['bg_surface']}; color: {tier_text_color}; border: 1px solid {tier_border}; border-radius: 9999px; padding: 3px 10px; font-size: 0.75rem; font-weight: 600; font-family: 'JetBrains Mono', monospace; white-space: nowrap;\">{zone_data['flood_probability']:.2%} chance</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.caption(
        f"Chance a flood is reported here in the next 60 min: "
        f"**{zone_data['flood_probability']:.2%}**. Flood reports are rare, so the alert levels are "
        f"low: Moderate from {tier_thresholds['moderate']:.2%}, "
        f"High from {tier_thresholds['high']:.2%}."
    )

    # Rolling rainfall and wet-ground tiles
    st.markdown(
        f'<div style="display: flex; justify-content: space-between; align-items: center; margin: 14px 0 8px 0;">'
        f"  <span style=\"font-size: 0.7rem; font-weight: 700; color: {tokens['text_muted']}; text-transform: uppercase; letter-spacing: 0.05em; font-family: 'Inter', sans-serif;\">ROLLING RAINFALL & WET-GROUND</span>"
        f"  <span style=\"font-size: 0.72rem; font-weight: 600; color: {tokens['accent']}; font-family: 'Inter', sans-serif;\">{'Live' if mode == 'Live Feed' else 'Replay'} · {view_time:%H:%M} SGT</span>"
        f"</div>"
        f'<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px;">'
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">5-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{zone_data["rain_5m"]:.2f} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">15-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{zone_data["rain_15m"]:.2f} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">30-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{zone_data["rain_30m"]:.2f} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">60-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{zone_data["rain_60m"]:.2f} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">120-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{zone_data["rain_120m"]:.2f} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: {tokens["stat_tile_bg"]}; border: 1px solid {tokens["stat_tile_border"]}; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: {tokens["text_muted"]}; margin-bottom: 2px;">Wet-Ground</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: {tokens["accent"]}; font-variant-numeric: tabular-nums;">{zone_data["rain_decay_72h"]:.1f} <span style="font-size: 0.72rem; color: {tokens["text_dim"]}; font-weight: 400;">idx</span></div>'
        f"  </div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    fitted = getattr(model, "rarity_quantiles", None)
    if fitted:
        rarity = float(rarity_scores(df_results[df_results["zone"] == selected_zone], fitted)[0])
        rarity_title = "Storm Rarity Percentile"
        rarity_basis = "of rainy half-hours in this zone, 2017–2023"
    else:
        rarity = float(zone_data["storm_rarity_score"])
        rarity_title = "Storm Rarity Percentile (uncalibrated)"
        rarity_basis = "against hand-set cut-offs: no trained model loaded"
    if zone_data["rain_30m"] <= 0:
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
                "font": {"family": "Inter, sans-serif", "size": 34, "color": tokens["gauge_num"]},
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
        "No rain here in the last 30 minutes, so there is no storm to rank."
        if zone_data["rain_30m"] <= 0
        else f"The last 30 minutes here were heavier than **{rarity * 100:.1f}%** {rarity_basis}."
    )
    st.button(
        "⟳ Replay the 17 Apr 2021 storm",
        on_click=_replay_pinned_storm,
        width="stretch",
        help="Real NEA readings from the day Dunearn Road flooded.",
    )


# --- PUBLIC TRANSPORT AT RISK -------------------------------------------------------------
with st.container(border=True):
    st.subheader(":material/train: Public transport at risk")
    try:
        at_risk = stations_at_risk(df_results, active_pub_alerts)
    except FileNotFoundError as exc:
        st.warning(str(exc))
        at_risk = None
    if at_risk is not None and at_risk.empty:
        st.caption(
            "No MRT/LRT station has an exit in a Moderate or High area"
            + (" or near an active PUB flood alert" if mode == "Live Feed" else "")
            + " right now."
        )
    elif at_risk is not None:
        counts = at_risk["tier"].value_counts()
        t_cols = st.columns(3)
        t_cols[0].metric("Stations in High areas", int(counts.get("High", 0)))
        t_cols[1].metric("Stations in Moderate areas", int(counts.get("Moderate", 0)))
        t_cols[2].metric("Stations near a PUB alert", int(counts.get("PUB alert", 0)))

        # An HTML table rather than st.dataframe: the dataframe is drawn on a canvas that the
        # page's light/dark styling can't reach.
        def _tier_chip(tier: str) -> str:
            bg, border, text = (
                tier_style(tier, IS_DARK) if tier != "PUB alert" else tier_style("High", IS_DARK)
            )
            return (
                f'<span style="background:{bg}; border:1px solid {border}; color:{text}; '
                f'padding:1px 8px; border-radius:9999px; font-weight:600;">{html.escape(tier)}</span>'
            )

        cell = f"padding:7px 10px; border-bottom:1px solid {c_border_subtle};"
        head = (
            f"position:sticky; top:0; background:{c_subtle}; color:{c_muted}; "
            f"text-align:left; font-weight:600; {cell}"
        )
        rows_html = "".join(
            "<tr>"
            f'<td style="{cell}">{html.escape(str(r.station))}</td>'
            f'<td style="{cell}">{html.escape(str(r.zone).title())}</td>'
            f'<td style="{cell}">{_tier_chip(str(r.tier))}</td>'
            f'<td style="{cell}">{"" if pd.isna(r.probability) else f"{r.probability:.2%}"}</td>'
            f'<td style="{cell} color:{c_muted};">{html.escape(str(r.reason))}</td>'
            "</tr>"
            for r in at_risk.itertuples(index=False)
        )
        headers = ["Station", "Planning area", "Risk", "Chance of flood (next hour)", "Why"]
        st.markdown(
            f'<div style="max-height:380px; overflow-y:auto; border:1px solid {c_border}; '
            f'border-radius:10px; background:{c_surface};">'
            f'<table style="width:100%; border-collapse:collapse; font-size:0.85rem; '
            f"font-family:'Inter', sans-serif; color:{c_main}; margin:0;\">"
            "<thead><tr>"
            + "".join(f'<th style="{head}">{h}</th>' for h in headers)
            + f"</tr></thead><tbody>{rows_html}</tbody></table></div>",
            unsafe_allow_html=True,
        )
    st.caption(
        f"Stations with an exit in a planning area rated Moderate or High, or within an active PUB "
        f"alert circle (exit locations: {MRT_SOURCE}). This shows exposure, not observed "
        "service disruptions."
        + (
            " Today's station list is used for every replay, so stations that opened after the "
            "replayed day can appear."
            if mode != "Live Feed"
            else ""
        )
    )

# --- CONTEXT & PROTOTYPE STATUS -----------------------------------------------------------
context_col, status_col = st.columns(2)

with context_col, st.container(border=True):
    st.subheader(":material/info: Hydrological Context & Archive")
    if day_events is not None:
        if day_events:
            lines = []
            for e in day_events:
                when = (
                    "time not reported"
                    if e.time_precision == "day_only"
                    else f"{e.timestamp_start:%H:%M}"
                    + ("" if e.time_precision == "exact" else " (approx.)")
                )
                lines.append(
                    f"- **{when}** · {e.ura_planning_area.title()}: {e.location_raw} "
                    f"([{e.source_name}]({e.source_url}))"
                )
            st.markdown("**Reported floods this day:**\n" + "\n".join(lines))
        else:
            st.markdown(
                "**Reported floods this day:** none in our 66 sourced events. Many floods are "
                "never reported, so this doesn't mean none happened."
            )
    st.markdown(
        "- **Flood-prone land:** about 3,200 ha in the 1970s, under 25 ha by 2025. "
        "([MSE, 4 Feb 2025](https://www.mse.gov.sg/latest-news/oral-reply-on-drainage-improvement-feb2025/))\n"
        "- **PUB monitoring:** more than 1,000 water-level sensors and over 500 CCTV cameras. "
        "([PUB](https://www.pub.gov.sg/Public/KeyInitiatives/Flood-Resilience/Flood-Forecasting-and-Monitoring))"
    )
    with st.expander("PUB flood-prone areas, 2022–2025", expanded=False):
        from floodsense.data.flood_prone import build_trend_chart, load_flood_prone_areas

        try:
            flood_prone = load_flood_prone_areas()
        except FileNotFoundError as exc:
            st.warning(str(exc))
        else:
            fig_trend = build_trend_chart(flood_prone)
            fig_trend.update_layout(
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                title=dict(font=dict(color=tokens["text_main"], family="Inter, sans-serif")),
                yaxis=dict(
                    gridcolor=tokens["grid_color"], tickfont=dict(color=tokens["text_muted"])
                ),
                xaxis=dict(tickfont=dict(color=tokens["text_muted"])),
            )
            st.plotly_chart(
                fig_trend,
                use_container_width=True,
                config={"displayModeBar": False},
            )
            first, last = flood_prone.iloc[0], flood_prone.iloc[-1]
            st.caption(
                f"PUB's published flood-prone land fell from {first.flood_prone_hectares:g} ha in "
                f"{first.year} to {last.flood_prone_hectares:g} ha in {last.year}. The land that "
                "stays flood-prone is small, but flash floods still happen outside it when "
                "intense rain overwhelms local drains."
            )

with status_col, st.container(border=True):
    st.subheader(":material/construction: Prototype Status & Verification")
    st.markdown(
        "- **Rainfall (real):** NEA 5-minute gauge readings, 2017 to Sep 2026 (60.8M readings), "
        "mapped to zones with distance weights that re-balance when gauges drop out.\n"
        "- **Flood labels (sourced):** 66 events, each with a source link, a quoted sentence and a "
        "human sign-off.\n"
        "- **Model:** a calibrated 60-minute-rainfall rule. It beat logistic regression and "
        "LightGBM at matched false-alarm levels on 2020–23.\n"
        "- **Held-out test (2024 to Sep 2026, 30 floods, 90% confidence intervals):** High caught "
        "12 (40%, 27–53%), median warning 10 min, 4 warned 15+ min ahead (13%, 3–23%); "
        "Moderate caught 21 (70%, 57–83%), median warning 15 min, 10 warned 15+ min ahead "
        "(33%, 20–47%). Gauges alone give little lead time; radar nowcasting is next.\n"
        "- **Zones:** the 55 URA Master Plan 2019 planning areas."
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
