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

import json
from datetime import date
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
from floodsense.features.zone_features import compute_zone_feature_table
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

MODERN_TELEMETRY_LIGHT_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

/* --- 1. GLOBAL APP CANVAS & TYPOGRAPHY --- */
.stApp {
    background-color: #F8FAFC !important;
    color: #0F172A !important;
    font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
}

header[data-testid="stHeader"] {
    background-color: #F8FAFC !important;
    border-bottom: 1px solid #E2E8F0 !important;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background-color: #FFFFFF !important;
    border-right: 1px solid #E2E8F0 !important;
}

section[data-testid="stSidebar"] .stMarkdown h3 {
    font-family: 'Inter', sans-serif !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em !important;
    color: #0F172A !important;
    font-size: 1.05rem !important;
}

/* Headings */
h1, h2, h3, h4, h5, h6 {
    font-family: 'Inter', system-ui, sans-serif !important;
    color: #0F172A !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em !important;
}

h1 {
    font-size: 1.6rem !important;
    margin-bottom: 0.2rem !important;
    padding-bottom: 0px !important;
    border-bottom: none !important;
    letter-spacing: -0.02em !important;
}

h2, h3 {
    font-size: 1.1rem !important;
    color: #0F172A !important;
}

/* --- 2. CLEAN WHITE ENTERPRISE CARDS --- */
div[data-testid="stVerticalBlockBorderWrapper"] > div {
    background-color: #FFFFFF !important;
    border: 1px solid #E2E8F0 !important;
    border-radius: 12px !important;
    box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.04) !important;
    padding: 1.15rem 1.25rem !important;
}

/* Zone Diagnostic Card: Orange Accent Top Border */
div[data-testid="stColumn"]:nth-of-type(2) div[data-testid="stVerticalBlockBorderWrapper"] > div {
    border-top: 4px solid #EA580C !important;
}

/* Rarity gauge: no element toolbar (full screen would leave its text tiny) */
.st-key-rarity_gauge [data-testid="stElementToolbar"] {
    display: none !important;
}

/* --- 3. TOP KPI METRIC CARDS --- */
[data-testid="stMetric"] {
    background-color: transparent !important;
    padding: 0px !important;
}

[data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] p,
[data-testid="stMetricLabel"] div {
    font-family: 'Inter', sans-serif !important;
    font-size: clamp(0.65rem, 0.75vw, 0.72rem) !important;
    font-weight: 700 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
    color: #64748B !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    white-space: nowrap !important;
}

[data-testid="stMetricValue"] {
    font-family: 'Inter', system-ui, sans-serif !important;
    font-size: clamp(1.25rem, 1.8vw, 2rem) !important;
    font-weight: 800 !important;
    color: #0F172A !important;
    font-variant-numeric: tabular-nums !important;
    letter-spacing: -0.02em !important;
    line-height: 1.15 !important;
}

[data-testid="stMetricDelta"] {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    font-variant-numeric: tabular-nums !important;
}

/* --- 4. BUTTONS --- */
button[kind="primary"],
[data-testid="baseButton-primary"] {
    background-color: #EA580C !important;
    border: 1px solid #EA580C !important;
    color: #FFFFFF !important;
    border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
    box-shadow: 0 1px 2px rgba(234, 88, 12, 0.2) !important;
    transition: all 0.15s ease !important;
}

button[kind="primary"]:hover,
[data-testid="baseButton-primary"]:hover {
    background-color: #C2410C !important;
    border-color: #C2410C !important;
    color: #FFFFFF !important;
}

button[kind="secondary"],
[data-testid="baseButton-secondary"] {
    background-color: #F8FAFC !important;
    border: 1px solid #E2E8F0 !important;
    color: #334155 !important;
    border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
    box-shadow: none !important;
    transition: all 0.15s ease !important;
}

button[kind="secondary"]:hover,
[data-testid="baseButton-secondary"]:hover {
    background-color: #F1F5F9 !important;
    border-color: #CBD5E1 !important;
    color: #0F172A !important;
}

/* --- 5. SEGMENTED CONTROL --- */
div[data-testid="stSegmentedControl"] {
    background-color: #F1F5F9 !important;
    border: 1px solid #E2E8F0 !important;
    border-radius: 20px !important;
    padding: 3px !important;
}

button[data-testid="stButtonGroupButton"] {
    border-radius: 16px !important;
    border: none !important;
    background-color: transparent !important;
    color: #64748B !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    padding: 5px 14px !important;
    transition: all 0.15s ease !important;
}

button[data-testid="stButtonGroupButton"][aria-pressed="true"] {
    background-color: #EA580C !important;
    color: #FFFFFF !important;
    box-shadow: 0 1px 3px rgba(234, 88, 12, 0.3) !important;
}

button[data-testid="stButtonGroupButton"]:hover {
    color: #0F172A !important;
}

/* --- 6. INPUTS & SELECTBOXES --- */
div[data-baseweb="select"] > div {
    background-color: #FFFFFF !important;
    border: 1px solid #CBD5E1 !important;
    border-radius: 8px !important;
    color: #0F172A !important;
    font-size: 0.875rem !important;
    font-family: 'Inter', sans-serif !important;
}

div[data-baseweb="input"] {
    background-color: #FFFFFF !important;
    border: 1px solid #CBD5E1 !important;
    border-radius: 8px !important;
}

div[data-baseweb="input"] input {
    color: #0F172A !important;
    font-size: 0.875rem !important;
    font-family: 'Inter', sans-serif !important;
}

div[data-baseweb="popover"],
ul[role="listbox"] {
    background-color: #FFFFFF !important;
    border: 1px solid #E2E8F0 !important;
    border-radius: 8px !important;
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -4px rgba(0, 0, 0, 0.1) !important;
}

li[role="option"] {
    color: #0F172A !important;
    font-size: 0.875rem !important;
    border-radius: 6px !important;
    font-family: 'Inter', sans-serif !important;
}

li[role="option"][aria-selected="true"] {
    background-color: #FFF7ED !important;
    color: #EA580C !important;
    font-weight: 600 !important;
}

/* --- 7. SLIDERS: SIGNAL ORANGE ACCENT --- */
div[data-testid="stSlider"] div[role="slider"],
div[data-testid="stSelectSlider"] div[role="slider"] {
    border-radius: 9999px !important;
    width: 16px !important;
    height: 16px !important;
    border: 2px solid #FFFFFF !important;
    background-color: #EA580C !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2) !important;
}

div[data-testid="stSlider"] div[data-baseweb="slider"] div,
div[data-testid="stSelectSlider"] div[data-baseweb="slider"] div {
    background-color: #EA580C !important;
}

/* --- 8. CODE & TELEMETRY --- */
code {
    font-family: 'JetBrains Mono', monospace !important;
    background-color: #F1F5F9 !important;
    border: 1px solid #E2E8F0 !important;
    color: #0284C7 !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
    font-size: 0.85em !important;
}

.stMarkdown code {
    white-space: nowrap !important;
    display: inline-block !important;
}

.stMarkdown ul li {
    white-space: normal !important;
    line-height: 1.5 !important;
    margin-bottom: 0.35rem !important;
}

/* --- 9. ALERTS & DIVIDERS --- */
div[data-testid="stAlert"] {
    border-radius: 8px !important;
    border: 1px solid #E2E8F0 !important;
    background-color: #FFFFFF !important;
    color: #0F172A !important;
}

hr {
    border: none !important;
    border-top: 1px solid #E2E8F0 !important;
    margin: 1.25rem 0 !important;
}

.stCaption, [data-testid="stCaptionContainer"] {
    color: #64748B !important;
    font-size: 0.78rem !important;
}
</style>
"""
st.markdown(MODERN_TELEMETRY_LIGHT_CSS, unsafe_allow_html=True)


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


def _replay_pinned_storm() -> None:
    """Jump to the 17 Apr 2021 replay. A callback, so it runs before the widgets are drawn."""
    st.session_state["mode"] = "Replay Storm"
    st.session_state["replay_date"] = replay_days.PINNED_STORM
    st.session_state[f"replay-time-{replay_days.PINNED_STORM}"] = REPLAY_DEFAULT_TIME


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


model, model_caption = get_model()

# --- SIDEBAR -------------------------------------------------------------------------------
st.sidebar.markdown("### :material/water_damage: **FloodSense**")

st.session_state.setdefault("mode", "Live Feed")
mode = st.sidebar.segmented_control(
    "Mode",
    options=["Live Feed", "Replay Storm"],
    key="mode",
    help="Live readings from data.gov.sg, or a replay of any past day's real readings.",
)

st.sidebar.markdown("---")
st.sidebar.subheader(":material/tune: Zone Diagnostic")
selected_zone = st.sidebar.selectbox(
    "Select Planning Area",
    options=sorted(URA_PLANNING_AREAS),
    index=sorted(URA_PLANNING_AREAS).index("BUKIT TIMAH"),
)

# --- DATA FOR THE SELECTED VIEW ------------------------------------------------------------
replay_warning: str | None = None
day_events: list[FloodEvent] | None = None  # reported floods for the replayed day
if mode == "Live Feed":
    features, total_stations, live_error = live_features()
    if features is None:
        st.title("🌊 FloodSense Intelligence Center")
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
    live_badge = (
        '<span style="background: #ECFDF5; border: 1px solid #A7F3D0; '
        "color: #059669; padding: 4px 12px; border-radius: 9999px; font-size: 0.78rem; "
        f"font-family: 'Inter', sans-serif; font-weight: 600;\">"
        f"● Live: {int(features['reporting_stations'].iloc[0])} of {total_stations} gauges "
        "reporting (data.gov.sg)</span>"
    )
    status_line = (
        f"{live_badge} &nbsp;·&nbsp; <span style='color: #64748B; font-size: 0.8rem; font-family: \"Inter\", sans-serif; font-weight: 500;'>Latest Reading:</span> "
        f"`{view_time:%d %b %Y %H:%M} SGT`"
    )
    history_note = (
        f"Rolling features use the last {settings.live_history_hours:g} h of live readings, so the "
        "72-hour wet-ground index is understated."
    )
elif replay_days.store_available():
    first_day, last_day = store_range()
    st.session_state.setdefault("replay_date", replay_days.PINNED_STORM)
    replay_day = st.sidebar.date_input(
        "Replay date (SGT)",
        key="replay_date",
        min_value=first_day,
        max_value=last_day,
        format="DD/MM/YYYY",
        help=f"Any day with NEA gauge readings, {first_day:%d %b %Y} to {last_day:%d %b %Y}.",
    )
    view = day_features(replay_day.isoformat())
    if view.features.empty:
        storm_buttons(replay_day)
        st.title("🌊 FloodSense Intelligence Center")
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
    chosen = st.sidebar.select_slider(
        f"Replay time (SGT, {replay_day:%d %b %Y})",
        options=list(times),
        value=default,
        key=f"replay-time-{replay_day}",
        help="Opens at the day's heaviest island-wide 30-minute rain.",
    )
    storm_buttons(replay_day)
    view_time = times[chosen]
    features = table[table["timestamp"] == view_time].reset_index(drop=True)
    replay_badge = (
        '<span style="background: #FEF3C7; border: 1px solid #FDE68A; '
        "color: #D97706; padding: 4px 12px; border-radius: 9999px; font-size: 0.78rem; "
        "font-family: 'Inter', sans-serif; font-weight: 600;\">"
        "⟳ Replay: NEA gauge readings</span>"
    )
    status_line = (
        f"{replay_badge} &nbsp;·&nbsp; <span style='color: #64748B; font-size: 0.8rem; font-family: \"Inter\", sans-serif; font-weight: 500;'>Timestamp:</span> "
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
    chosen = st.sidebar.select_slider(
        "Replay time (SGT, 17 Apr 2021)",
        options=list(times),
        value=REPLAY_DEFAULT_TIME if REPLAY_DEFAULT_TIME in times else next(iter(times)),
        help="Real NEA readings. The rainfall store is not available here, so only this storm "
        "can be replayed.",
    )
    view_time = times[chosen]
    features = table[table["timestamp"] == view_time].reset_index(drop=True)
    replay_badge = (
        '<span style="background: #FEF3C7; border: 1px solid #FDE68A; '
        "color: #D97706; padding: 4px 12px; border-radius: 9999px; font-size: 0.78rem; "
        f"font-family: 'Inter', sans-serif; font-weight: 600;\">"
        f"⟳ Historical Replay: {event_name}</span>"
    )
    status_line = (
        f"{replay_badge} &nbsp;·&nbsp; <span style='color: #64748B; font-size: 0.8rem; font-family: \"Inter\", sans-serif; font-weight: 500;'>Timestamp:</span> "
        f"`{view_time:%d %b %Y %H:%M} SGT`"
    )
    history_note = "Features include the 72 hours of real readings before the replay window."
    day_events = replay_days.events_on(replay_days.PINNED_STORM, flood_events())

df_results = score_zone_features(features, model).merge(ZONE_META, on="ura_planning_area")
df_results["zone"] = df_results["ura_planning_area"]
reporting_stations = int(features["reporting_stations"].iloc[0])

# --- TOP BAR & SCOPE INDICATORS -----------------------------------------------------------
st.title("🌊 FloodSense Intelligence Center")
st.markdown(status_line, unsafe_allow_html=True)
st.markdown(
    '<div style="background: #EEF2F6; border: 1px solid #E2E8F0; border-radius: 9999px; '
    "padding: 4px 14px; margin: 8px 0 18px 0; display: inline-flex; align-items: center; "
    "gap: 8px; font-size: 0.78rem; font-family: 'Inter', sans-serif; color: #475569;\">"
    "  <span>📍 Singapore Urban Flash-Flood Risk</span>"
    '  <span style="color: #CBD5E1;">•</span>'
    "  <span>55 URA Planning Areas</span>"
    '  <span style="color: #CBD5E1;">•</span>'
    "  <span>Chance of a flood in the next hour</span>"
    "</div>",
    unsafe_allow_html=True,
)
if replay_warning:
    st.warning(replay_warning, icon=":material/warning:")

high_risk_count = int((df_results["risk_tier"] == "High").sum())
mod_risk_count = int((df_results["risk_tier"] == "Moderate").sum())
peak = df_results.sort_values("rain_30m", ascending=False).iloc[0]
wettest = df_results.sort_values("rain_decay_72h", ascending=False).iloc[0]

# --- 5 TOP KPI METRIC CARDS ---------------------------------------------------------------
# Every card has the same three parts, each one line: label, value, footer (label | value).
_LABEL = (
    "font-size:0.68rem; font-family:'Inter', sans-serif; font-weight:700; color:#64748B; "
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
        '<div style="display:flex; justify-content:space-between; font-size:0.72rem; '
        "font-family:'Inter', sans-serif; color:#64748B; border-top:1px solid #F1F5F9; "
        'padding-top:6px; margin-top:2px; gap:8px; white-space:nowrap;">'
        f"<span>{left}</span>"
        '<span style="font-weight:700; color:#0F172A; overflow:hidden; text-overflow:ellipsis;" '
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
        '<div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 2px;">'
        '  <div style="display: flex; align-items: center; gap: 8px;">'
        "    <span style=\"font-size: 1.15rem; font-weight: 700; color: #0F172A; font-family: 'Inter', sans-serif;\">🗺️ Singapore Urban Risk Map</span>"
        "    <span style=\"background: #F1F5F9; border: 1px solid #E2E8F0; color: #475569; font-family: 'Inter', sans-serif; font-size: 0.72rem; font-weight: 600; padding: 2px 8px; border-radius: 9999px;\">55 URA ZONES</span>"
        "  </div>"
        "</div>"
        "<div style=\"font-size: 0.78rem; color: #64748B; margin-bottom: 12px; font-family: 'Inter', sans-serif;\">"
        "Rainfall at each zone interpolated from NEA rain gauges (inverse-distance weighting)."
        "</div>"
        f"<div style=\"display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; font-family: 'Inter', sans-serif;\">"
        f'  <span style="background: #F8FAFC; border: 1px solid #E2E8F0; color: #64748B; padding: 4px 12px; border-radius: 9999px; font-size: 0.75rem; font-weight: 500;"><span style="color:#0EA5E9;">●</span> Low ({NUM_ZONES - high_risk_count - mod_risk_count})</span>'
        f'  <span style="background: #F8FAFC; border: 1px solid #E2E8F0; color: #64748B; padding: 4px 12px; border-radius: 9999px; font-size: 0.75rem; font-weight: 500;"><span style="color:#F59E0B;">●</span> Moderate ({mod_risk_count})</span>'
        f'  <span style="background: #F8FAFC; border: 1px solid #E2E8F0; color: #64748B; padding: 4px 12px; border-radius: 9999px; font-size: 0.75rem; font-weight: 500;"><span style="color:#EF4444;">●</span> High ({high_risk_count})</span>'
        f'  <span style="background: #E0F2FE; border: 1px solid #BAE6FD; color: #0284C7; padding: 4px 12px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600;">{reporting_stations} of {total_stations} gauges reporting</span>'
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
        style_key: "carto-positron",
    }
    fig_map = map_func(df_results, **map_kwargs)  # type: ignore[misc]

    # Clean tooltip formatting
    for trace in fig_map.data:
        if trace.name in TIER_COLORS:
            trace.update(
                hovertemplate=(
                    "<b>ZONE: %{hovertext}</b><br>"
                    "<span style='color:#64748B;'>Risk Classification:</span> %{customdata[0]}<br>"
                    "<span style='color:#64748B;'>Flood Probability:</span> %{customdata[1]:.2%}<br>"
                    "<span style='color:#64748B;'>Max 30m Rain:</span> %{customdata[2]:.1f} mm<extra></extra>"
                ),
            )

    # Weather station / sensor dots layer: cool cyan dots
    stns = load_station_snapshot()
    if stns:
        stn_lats = [s["lat"] for s in stns.values()]
        stn_lons = [s["lon"] for s in stns.values()]
        stn_names = [
            f"<b>NEA rain gauge {sid}</b><br>"
            f"<span style='color:#64748B;'>{s.get('name', sid)}</span>"
            for sid, s in stns.items()
        ]
        trace_cls = getattr(go, "Scattermap", getattr(go, "Scattermapbox", None))
        if trace_cls:
            fig_map.add_trace(
                trace_cls(
                    lat=stn_lats,
                    lon=stn_lons,
                    mode="markers",
                    marker=dict(size=4, color="#64748B", opacity=0.7),
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
                "color": "rgba(148, 163, 184, 0.45)",
                "line": {"width": 1.0},
            }
        )

    map_layout_key = "map" if hasattr(px, "scatter_map") else "mapbox"
    fig_map.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=500,
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        hoverlabel=dict(
            bgcolor="#FFFFFF",
            bordercolor="#E2E8F0",
            font_family="Inter, sans-serif",
            font_size=12,
            font_color="#0F172A",
        ),
        legend=dict(
            yanchor="top",
            y=0.98,
            xanchor="left",
            x=0.02,
            bgcolor="rgba(255, 255, 255, 0.95)",
            bordercolor="#E2E8F0",
            borderwidth=1,
            font=dict(family="Inter, sans-serif", size=11, color="#334155"),
            title=dict(
                text="RISK CLASSIFICATION",
                font=dict(family="Inter, sans-serif", size=10, color="#64748B"),
            ),
        ),
        **{map_layout_key: {"style": "carto-positron", "layers": map_layers}},
    )
    st.plotly_chart(fig_map)
    st.caption(
        f"{model_caption} {history_note} Each risk marker sits inside its URA planning area; "
        "grey outlines are the URA Master Plan 2019 boundaries, small grey dots NEA gauge locations."
    )

with detail_col, st.container(border=True):
    st.markdown(
        '<div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">'
        '  <div style="display: flex; align-items: center; gap: 8px;">'
        "    <span style=\"font-size: 1.15rem; font-weight: 700; color: #0F172A; font-family: 'Inter', sans-serif;\">📊 Zone Diagnostic</span>"
        "  </div>"
        "</div>",
        unsafe_allow_html=True,
    )
    zone_data = df_results[df_results["zone"] == selected_zone].iloc[0]

    tier_label = {
        "High": "HIGH RISK",
        "Moderate": "MODERATE RISK",
        "Low": "LOW RISK",
    }[zone_data["risk_tier"]]
    tier_bg = {
        "High": "#FEF2F2",
        "Moderate": "#FFFBEB",
        "Low": "#EFF6FF",
    }[zone_data["risk_tier"]]
    tier_border = {
        "High": "#FECACA",
        "Moderate": "#FDE68A",
        "Low": "#BFDBFE",
    }[zone_data["risk_tier"]]
    tier_text_color = {
        "High": "#DC2626",
        "Moderate": "#D97706",
        "Low": "#1E40AF",
    }[zone_data["risk_tier"]]

    st.markdown(
        f'<div style="margin: 8px 0 12px 0;">'
        f'  <div style="font-weight: 600; color: #0F172A; font-size: 0.95rem;">'
        f'📍 {selected_zone.title()} <span style="color: #64748B; font-weight: 500;">'
        f"· {zone_data['region']} Region · change in the sidebar</span></div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div style="background: {tier_bg}; border: 1px solid {tier_border}; border-radius: 8px; padding: 12px 16px; margin: 10px 0 14px 0; display: flex; align-items: center; justify-content: space-between;">'
        f'  <div style="display: flex; align-items: center; gap: 10px;">'
        f'    <span style="color: {tier_text_color}; font-size: 1.2rem; font-weight: 700;">{"✓" if zone_data["risk_tier"] == "Low" else "!"}</span>'
        f"    <div>"
        f"      <div style=\"font-size: 0.65rem; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em; font-family: 'Inter', sans-serif;\">STATUS</div>"
        f"      <div style=\"font-size: 0.95rem; font-weight: 700; color: {tier_text_color}; font-family: 'Inter', sans-serif;\">{tier_label}</div>"
        f"    </div>"
        f"  </div>"
        f"  <span style=\"background: #FFFFFF; color: {tier_text_color}; border: 1px solid {tier_border}; border-radius: 9999px; padding: 3px 10px; font-size: 0.75rem; font-weight: 600; font-family: 'JetBrains Mono', monospace;\">{zone_data['flood_probability']:.2%} chance</span>"
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
        f"  <span style=\"font-size: 0.7rem; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em; font-family: 'Inter', sans-serif;\">ROLLING RAINFALL & WET-GROUND</span>"
        f"  <span style=\"font-size: 0.72rem; font-weight: 600; color: #0284C7; font-family: 'Inter', sans-serif;\">{'Live' if mode == 'Live Feed' else 'Replay'} · {view_time:%H:%M} SGT</span>"
        f"</div>"
        f'<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px;">'
        f'  <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: #64748B; margin-bottom: 2px;">5-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: #0284C7; font-variant-numeric: tabular-nums;">{zone_data["rain_5m"]:.2f} <span style="font-size: 0.72rem; color: #94A3B8; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: #64748B; margin-bottom: 2px;">15-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: #0284C7; font-variant-numeric: tabular-nums;">{zone_data["rain_15m"]:.2f} <span style="font-size: 0.72rem; color: #94A3B8; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: #64748B; margin-bottom: 2px;">30-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: #0284C7; font-variant-numeric: tabular-nums;">{zone_data["rain_30m"]:.2f} <span style="font-size: 0.72rem; color: #94A3B8; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: #64748B; margin-bottom: 2px;">60-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: #0284C7; font-variant-numeric: tabular-nums;">{zone_data["rain_60m"]:.2f} <span style="font-size: 0.72rem; color: #94A3B8; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: #64748B; margin-bottom: 2px;">120-min Rain</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: #0284C7; font-variant-numeric: tabular-nums;">{zone_data["rain_120m"]:.2f} <span style="font-size: 0.72rem; color: #94A3B8; font-weight: 400;">mm</span></div>'
        f"  </div>"
        f'  <div style="background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 8px 6px; text-align: center;">'
        f'    <div style="font-size: 0.68rem; font-weight: 600; color: #64748B; margin-bottom: 2px;">Wet-Ground</div>'
        f'    <div style="font-size: 1.15rem; font-weight: 700; color: #0284C7; font-variant-numeric: tabular-nums;">{zone_data["rain_decay_72h"]:.1f} <span style="font-size: 0.72rem; color: #94A3B8; font-weight: 400;">idx</span></div>'
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
        f"  <span style=\"font-size: 0.7rem; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.05em; font-family: 'Inter', sans-serif;\">{rarity_title}</span>"
        f"  <span style=\"font-size: 0.72rem; font-weight: 600; color: #0284C7; font-family: 'Inter', sans-serif;\">{rarity_status}</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    fig_gauge = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=rarity * 100,
            number={
                "font": {"family": "Inter, sans-serif", "size": 34, "color": "#0F172A"},
                "valueformat": ".1f",
                "suffix": "%",
            },
            gauge={
                "axis": {
                    "range": [0, 100],
                    "tickcolor": "#CBD5E1",
                    "tickfont": {"family": "Inter, sans-serif", "size": 11, "color": "#64748B"},
                },
                "bar": {
                    "color": "#0EA5E9"
                    if rarity < 0.7
                    else ("#F59E0B" if rarity < 0.9 else "#EF4444"),
                    "thickness": 0.26,
                },
                "steps": [
                    {"range": [0, 70], "color": "#E0F2FE"},
                    {"range": [70, 90], "color": "#FEF3C7"},
                    {"range": [90, 100], "color": "#FEE2E2"},
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
    with st.expander("📊 PUB Flood-Prone Areas Trend (2022–2025)", expanded=False):
        from floodsense.data.flood_prone import build_trend_chart
        st.plotly_chart(build_trend_chart(), use_container_width=True, config={"displayModeBar": False})
        st.caption("Official PUB annual hectarage gazette. While physical drainage infrastructure has reduced flood-prone land by 99% since the 1970s, high-intensity microbursts require real-time hyper-local predictive intelligence.")

with status_col, st.container(border=True):
    st.subheader(":material/construction: Prototype Status & Verification")
    st.markdown(
        "- **Rainfall (real):** NEA 5-minute gauge readings, 2017 to Sep 2026 (60.8M readings), "
        "mapped to zones with distance weights that re-balance when gauges drop out.\n"
        "- **Flood labels (sourced):** 66 events, each with a source link, a quoted sentence and a "
        "human sign-off.\n"
        "- **Model:** a calibrated 60-minute-rainfall rule. It beat logistic regression and "
        "LightGBM at matched false-alarm levels on 2020–23.\n"
        "- **Held-out test (2024 to Sep 2026, 30 floods):** High caught 12 (40%), median warning "
        "7.5 min; Moderate caught 21 (70%), median warning 15 min. Gauges alone give little lead "
        "time; radar nowcasting is next.\n"
        "- **Zones:** the 55 URA Master Plan 2019 planning areas."
    )

# --- FOOTER --------------------------------------------------------------------------------
st.markdown(
    '<div style="padding: 24px 0 16px 0; border-top: 1px solid #E2E8F0; margin-top: 32px; '
    "color: #64748B; font-size: 0.78rem; font-family: 'Inter', sans-serif;\">"
    "FloodSense · flash-flood risk for Singapore's 55 planning areas · Rainfall: NEA via "
    "data.gov.sg · Boundaries: URA Master Plan 2019"
    "</div>",
    unsafe_allow_html=True,
)
