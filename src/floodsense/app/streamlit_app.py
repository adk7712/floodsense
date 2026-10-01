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
from pathlib import Path
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
    initial_sidebar_state="expanded",
)

TECHNICAL_UTILITY_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');

/* --- 1. GLOBAL GEOMETRY: STRICT 0PX BORDER RADIUS, NO BLURS/SHADOWS --- */
*, *::before, *::after {
    border-radius: 0px !important;
    box-shadow: none !important;
    text-shadow: none !important;
}

/* Base Canvas & Typography */
.stApp {
    background-color: #0B0F17 !important;
    color: #F3F4F6 !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
}

header[data-testid="stHeader"] {
    background-color: #0B0F17 !important;
    border-bottom: 1px solid #1F2937 !important;
}

/* Headings */
h1, h2, h3, h4, h5, h6 {
    font-family: 'Space Grotesk', 'Inter', -apple-system, sans-serif !important;
    color: #F3F4F6 !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em !important;
}

h1 {
    font-size: 1.7rem !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em !important;
    border-bottom: 1px solid #1F2937 !important;
    padding-bottom: 0.4rem !important;
    margin-bottom: 0.75rem !important;
}

h2, h3 {
    font-size: 1.05rem !important;
    letter-spacing: -0.01em !important;
}

/* Tabular figures for all dynamic telemetry and readouts */
[data-testid="stMetricValue"],
[data-testid="stMetricDelta"],
code, pre, kbd,
div[data-baseweb="input"] input,
div[data-baseweb="select"] {
    font-family: 'JetBrains Mono', 'IBM Plex Mono', 'SF Mono', Menlo, Consolas, monospace !important;
    font-variant-numeric: tabular-nums !important;
}

/* --- 2. RACK-MOUNTED PANELS & WIREFRAME BORDERS --- */
div[data-testid="stVerticalBlockBorderWrapper"] > div {
    background-color: #111827 !important;
    border: 1px solid #1F2937 !important;
    padding: 0.85rem !important;
}

section[data-testid="stSidebar"] {
    background-color: #0B0F17 !important;
    border-right: 1px solid #1F2937 !important;
}

section[data-testid="stSidebar"] .stMarkdown h3 {
    font-family: 'Space Grotesk', sans-serif !important;
    letter-spacing: 0.05em !important;
    text-transform: uppercase !important;
    font-size: 0.95rem !important;
    color: #F3F4F6 !important;
}

/* Telemetry Metric Cards */
[data-testid="stMetric"] {
    background-color: transparent !important;
    padding: 0.15rem 0.25rem !important;
}

[data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] p,
[data-testid="stMetricLabel"] div {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.60rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.04em !important;
    color: #9CA3AF !important;
    white-space: nowrap !important;
}

[data-testid="stMetricValue"] {
    font-family: 'JetBrains Mono', 'IBM Plex Mono', monospace !important;
    font-size: 1.55rem !important;
    font-weight: 700 !important;
    color: #F3F4F6 !important;
    font-variant-numeric: tabular-nums !important;
    letter-spacing: -0.02em !important;
    line-height: 1.2 !important;
}

[data-testid="stMetricDelta"] {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.72rem !important;
    font-variant-numeric: tabular-nums !important;
    font-weight: 500 !important;
}

/* --- 3. CONTROLS, INPUTS, BUTTONS --- */
button[kind="secondary"],
button[kind="primary"],
[data-testid="baseButton-secondary"],
[data-testid="baseButton-primary"] {
    border-radius: 0px !important;
    border: 1px solid #1F2937 !important;
    background-color: #111827 !important;
    color: #F3F4F6 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.78rem !important;
    font-weight: 500 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
    box-shadow: none !important;
    transition: none !important;
}

button[kind="primary"],
[data-testid="baseButton-primary"] {
    border: 1px solid #38BDF8 !important;
    background-color: rgba(56, 189, 248, 0.12) !important;
    color: #38BDF8 !important;
    font-weight: 600 !important;
}

button:hover {
    border-color: #38BDF8 !important;
    background-color: rgba(56, 189, 248, 0.08) !important;
    color: #F3F4F6 !important;
}

/* Segmented Control */
div[data-testid="stSegmentedControl"] {
    border: 1px solid #1F2937 !important;
    background-color: #0B0F17 !important;
    padding: 2px !important;
    gap: 2px !important;
}

button[data-testid="stButtonGroupButton"] {
    border-radius: 0px !important;
    border: 1px solid transparent !important;
    background-color: transparent !important;
    color: #9CA3AF !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.75rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.06em !important;
    text-transform: uppercase !important;
    transition: none !important;
}

button[data-testid="stButtonGroupButton"][aria-pressed="true"] {
    background-color: #111827 !important;
    border: 1px solid #38BDF8 !important;
    color: #38BDF8 !important;
}

button[data-testid="stButtonGroupButton"]:hover {
    color: #F3F4F6 !important;
}

/* Selectbox & Date Input */
div[data-baseweb="select"] > div {
    border-radius: 0px !important;
    border: 1px solid #1F2937 !important;
    background-color: #0B0F17 !important;
    color: #F3F4F6 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.82rem !important;
}

div[data-baseweb="input"] {
    border-radius: 0px !important;
    border: 1px solid #1F2937 !important;
    background-color: #0B0F17 !important;
}

div[data-baseweb="input"] input {
    color: #F3F4F6 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.82rem !important;
    font-variant-numeric: tabular-nums !important;
}

/* Slider */
div[data-testid="stSlider"] div[role="slider"],
div[data-testid="stSelectSlider"] div[role="slider"] {
    border-radius: 0px !important;
    border: 1px solid #38BDF8 !important;
    background-color: #0B0F17 !important;
    box-shadow: none !important;
    width: 14px !important;
    height: 14px !important;
}

div[data-testid="stSlider"] div[data-baseweb="slider"] div,
div[data-testid="stSelectSlider"] div[data-baseweb="slider"] div {
    border-radius: 0px !important;
}

/* Popover listbox */
div[data-baseweb="popover"],
ul[role="listbox"] {
    border-radius: 0px !important;
    border: 1px solid #1F2937 !important;
    background-color: #111827 !important;
}

li[role="option"] {
    border-radius: 0px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.8rem !important;
    color: #F3F4F6 !important;
}

li[role="option"][aria-selected="true"] {
    background-color: #1F2937 !important;
    color: #38BDF8 !important;
}

/* Telemetry Code Blocks */
code {
    font-family: 'JetBrains Mono', 'IBM Plex Mono', monospace !important;
    font-variant-numeric: tabular-nums !important;
    background-color: #0B0F17 !important;
    border: 1px solid #1F2937 !important;
    color: #38BDF8 !important;
    padding: 1px 5px !important;
    border-radius: 0px !important;
    font-size: 0.85em !important;
}

.stMarkdown code {
    white-space: nowrap !important;
    display: inline-block !important;
}

.stMarkdown ul li {
    white-space: nowrap !important;
}

/* Alerts */
div[data-testid="stAlert"] {
    border-radius: 0px !important;
    border: 1px solid #1F2937 !important;
    background-color: #111827 !important;
    color: #F3F4F6 !important;
}

hr {
    border: none !important;
    border-top: 1px solid #1F2937 !important;
    margin: 0.75rem 0 !important;
}

.stCaption, [data-testid="stCaptionContainer"] {
    color: #9CA3AF !important;
    font-size: 0.75rem !important;
}

::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}
::-webkit-scrollbar-track {
    background: #0B0F17;
}
::-webkit-scrollbar-thumb {
    background: #1F2937;
}
::-webkit-scrollbar-thumb:hover {
    background: #374151;
}
</style>
"""
st.markdown(TECHNICAL_UTILITY_CSS, unsafe_allow_html=True)


@st.cache_data
def load_ura_boundaries() -> dict[str, Any] | None:
    path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "data"
        / "reference"
        / "ura_planning_areas_mp2019.geojson"
    )
    if not path.exists():
        path = Path("data/reference/ura_planning_areas_mp2019.geojson")
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                raw_geojson = json.load(f)
            simplified_features: list[dict[str, Any]] = []
            for feat in raw_geojson.get("features", []):
                s = shape(feat["geometry"]).simplify(0.001, preserve_topology=True)
                simplified_features.append({
                    "type": "Feature",
                    "properties": feat.get("properties", {}),
                    "geometry": mapping(s),
                })
            return {"type": "FeatureCollection", "features": simplified_features}
        except Exception:
            return None
    return None


TIER_COLORS = {
    "High": "#EF4444",       # Signal Crimson
    "Moderate": "#F59E0B",   # Industrial Amber
    "Low": "#334155",        # Subdued cool slate
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
    if model.is_synthetic:
        return model, (
            "Risk tiers come from a prototype model trained on synthetic rainfall, so treat them "
            "as illustrative. Rainfall values are real NEA measurements."
        )
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
        if st.button("Retry now"):
            live_features.clear()
            st.rerun()
        st.stop()
    view_time = features["timestamp"].iloc[0]
    live_badge = (
        '<span style="background: rgba(16, 185, 129, 0.15); border: 1px solid #10B981; '
        'color: #34D399; padding: 2px 7px; font-family: monospace; font-size: 0.75rem; '
        'font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;">'
        '● OPERATIONAL / LIVE</span>'
    )
    status_line = (
        f"{live_badge} &nbsp; **data.gov.sg NEA Telemetry** &nbsp;•&nbsp; "
        f"latest reading `{view_time:%d %b %Y %H:%M} SGT`"
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
        '<span style="background: rgba(245, 158, 11, 0.15); border: 1px solid #F59E0B; '
        'color: #FBBF24; padding: 2px 7px; font-family: monospace; font-size: 0.75rem; '
        'font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;">'
        '⟳ HISTORICAL REPLAY</span>'
    )
    status_line = (
        f"{replay_badge} &nbsp; **NEA Gauge Telemetry** &nbsp;•&nbsp; `{view_time:%d %b %Y %H:%M} SGT`"
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
        '<span style="background: rgba(245, 158, 11, 0.15); border: 1px solid #F59E0B; '
        'color: #FBBF24; padding: 2px 7px; font-family: monospace; font-size: 0.75rem; '
        'font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;">'
        '⟳ HISTORICAL REPLAY</span>'
    )
    status_line = (
        f"{replay_badge} &nbsp; **{event_name}** &nbsp;•&nbsp; `{view_time:%d %b %Y %H:%M} SGT`"
    )
    history_note = "Features include the 72 hours of real readings before the replay window."
    day_events = replay_days.events_on(replay_days.PINNED_STORM, flood_events())

df_results = score_zone_features(features, model).merge(ZONE_META, on="ura_planning_area")
df_results["zone"] = df_results["ura_planning_area"]
reporting_stations = int(features["reporting_stations"].iloc[0])

# --- HEADER & KPIs -------------------------------------------------------------------------
st.title("🌊 FloodSense Intelligence Center")
st.markdown(status_line, unsafe_allow_html=True)
if replay_warning:
    st.warning(replay_warning, icon=":material/warning:")

high_risk_count = int((df_results["risk_tier"] == "High").sum())
mod_risk_count = int((df_results["risk_tier"] == "Moderate").sum())
peak = df_results.sort_values("rain_30m", ascending=False).iloc[0]
wettest = df_results.sort_values("rain_decay_72h", ascending=False).iloc[0]

kpi_cols = st.columns(5)
with kpi_cols[0], st.container(border=True):
    st.metric(
        label="High Risk",
        value=f"{high_risk_count} / {NUM_ZONES}",
        delta=f"{high_risk_count} Alert" if high_risk_count > 0 else "Normal",
        delta_color="inverse",
    )
with kpi_cols[1], st.container(border=True):
    st.metric(label="Moderate Risk", value=f"{mod_risk_count} / {NUM_ZONES}")
with kpi_cols[2], st.container(border=True):
    st.metric(label="Max 30-min Rain", value=f"{peak['rain_30m']:.0f} mm", delta=peak["zone"])
with kpi_cols[3], st.container(border=True):
    st.metric(
        label="Wet-Ground Index",
        value=f"{wettest['rain_decay_72h']:.0f}",
        delta=wettest["zone"],
        delta_color="off",
        help=WET_GROUND_HELP + (LIVE_WET_GROUND_NOTE if mode == "Live Feed" else ""),
    )
with kpi_cols[4], st.container(border=True):
    st.metric(
        label="Gauges Live",
        value=f"{reporting_stations} / {total_stations}",
        help="Zone rainfall weights re-balance automatically over the gauges that reported.",
    )

# --- MAP & ZONE DETAILS --------------------------------------------------------------------
map_col, detail_col = st.columns([1.7, 1.3])

with map_col, st.container(border=True):
    st.subheader(f":material/map: Singapore Urban Risk Map ({NUM_ZONES} URA Zones)")
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
        "size_max": 36,
        "zoom": 10.5,
        "center": {"lat": 1.3521, "lon": 103.8198},
        style_key: "carto-darkmatter",
    }
    fig_map = map_func(df_results, **map_kwargs)  # type: ignore[misc]  # None only on very old plotly

    # Format zone hover tooltips into industrial card format
    for trace in fig_map.data:
        if trace.name in TIER_COLORS:
            trace.update(
                hovertemplate=(
                    "<b>[URA ZONE: %{hovertext}]</b><br>"
                    "<span style='color:#9CA3AF;'>ALERT LEVEL:</span> %{customdata[0]}<br>"
                    "<span style='color:#9CA3AF;'>30M RAIN:</span> %{customdata[2]:.1f} mm<br>"
                    "<span style='color:#9CA3AF;'>P(FLOOD 60M):</span> %{customdata[1]:.4f}<extra></extra>"
                ),
            )

    # Weather station / sensor dots layer: cool cyan spectrum
    stns = load_station_snapshot()
    if stns:
        stn_lats = [s["lat"] for s in stns.values()]
        stn_lons = [s["lon"] for s in stns.values()]
        stn_names = [
            f"<b>[NEA GAUGE: {sid}]</b><br>"
            f"<span style='color:#9CA3AF;'>NAME:</span> {s.get('name', sid)}<br>"
            f"<span style='color:#9CA3AF;'>STATUS:</span> ACTIVE TELEMETRY"
            for sid, s in stns.items()
        ]
        trace_cls = getattr(go, "Scattermap", getattr(go, "Scattermapbox", None))
        if trace_cls:
            fig_map.add_trace(
                trace_cls(
                    lat=stn_lats,
                    lon=stn_lons,
                    mode="markers",
                    marker=dict(size=4, color="#38BDF8", opacity=0.75),
                    name="Rain Gauges (NEA)",
                    hoverinfo="text",
                    hovertext=stn_names,
                )
            )

    # 55 URA planning area polygon boundaries: thin 1px wireframe outlines
    ura_geojson = load_ura_boundaries()
    map_layers = []
    if ura_geojson:
        map_layers.append({
            "sourcetype": "geojson",
            "source": ura_geojson,
            "type": "line",
            "color": "rgba(75, 85, 99, 0.45)",
            "line": {"width": 1.0},
        })

    map_layout_key = "map" if hasattr(px, "scatter_map") else "mapbox"
    fig_map.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=500,
        paper_bgcolor="#111827",
        plot_bgcolor="#0B0F17",
        hoverlabel=dict(
            bgcolor="#111827",
            bordercolor="#1F2937",
            font_family="JetBrains Mono, monospace",
            font_size=12,
            font_color="#F3F4F6",
        ),
        legend=dict(
            yanchor="top",
            y=0.98,
            xanchor="left",
            x=0.02,
            bgcolor="rgba(11, 15, 23, 0.88)",
            bordercolor="#1F2937",
            borderwidth=1,
            font=dict(family="JetBrains Mono, monospace", size=10, color="#F3F4F6"),
            title=dict(font=dict(family="Inter, sans-serif", size=10, color="#9CA3AF")),
        ),
        **{map_layout_key: {"style": "carto-darkmatter", "layers": map_layers}},
    )
    st.plotly_chart(fig_map)
    st.caption(f"{model_caption} {history_note} Each marker sits inside its URA planning area.")

with detail_col, st.container(border=True):
    st.subheader(f":material/analytics: Zone Diagnostic: `{selected_zone}`")
    zone_data = df_results[df_results["zone"] == selected_zone].iloc[0]

    tier_styles = {
        "High": "background: rgba(239, 68, 68, 0.15); border: 1px solid #EF4444; color: #EF4444;",
        "Moderate": "background: rgba(245, 158, 11, 0.15); border: 1px solid #F59E0B; color: #F59E0B;",
        "Low": "background: rgba(30, 41, 59, 0.5); border: 1px solid #334155; color: #94A3B8;",
    }
    tier_label = {
        "High": "CRITICAL // HIGH RISK",
        "Moderate": "ELEVATED // MODERATE RISK",
        "Low": "NOMINAL // LOW RISK",
    }[zone_data["risk_tier"]]
    tier_badge = (
        f'<span style="{tier_styles[zone_data["risk_tier"]]} padding: 3px 8px; '
        f'font-family: monospace; font-size: 0.8rem; font-weight: 700; '
        f'letter-spacing: 0.06em;">{tier_label}</span>'
    )
    st.markdown(f"**Status:** {tier_badge}", unsafe_allow_html=True)
    thresholds = getattr(model, "thresholds", None) or default_thresholds()
    st.caption(
        f"Chance a flood is reported here in the next 60 min: "
        f"**{zone_data['flood_probability'] * 100:.2f}%**. Flood reports are rare, so the alert "
        f"levels are low: Moderate from {thresholds['moderate'] * 100:.2f}%, "
        f"High from {thresholds['high'] * 100:.2f}%."
    )

    z_col1, z_col2 = st.columns(2)
    with z_col1:
        st.markdown(f"- **5-min Rain:** `{zone_data['rain_5m']:.2f} mm`")
        st.markdown(f"- **15-min Rain:** `{zone_data['rain_15m']:.2f} mm`")
        st.markdown(f"- **30-min Rain:** `{zone_data['rain_30m']:.2f} mm`")
    with z_col2:
        st.markdown(f"- **60-min Rain:** `{zone_data['rain_60m']:.2f} mm`")
        st.markdown(
            f"- **Wet-ground index:** `{zone_data['rain_decay_72h']:.1f}`", help=WET_GROUND_HELP
        )
        st.markdown(f"- **120-min Rain:** `{zone_data['rain_120m']:.2f} mm`")

    fitted = getattr(model, "rarity_quantiles", None)
    if fitted:
        rarity = float(rarity_scores(df_results[df_results["zone"] == selected_zone], fitted)[0])
        rarity_title = "Storm rarity: heavier than this % of rainy half-hours here, 2017–23"
    else:
        rarity = float(zone_data["storm_rarity_score"])
        rarity_title = "Storm rarity percentile (uncalibrated: no trained model)"
    fig_gauge = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=rarity * 100,
            title={"text": rarity_title, "font": {"size": 12, "color": "#9CA3AF"}},
            number={
                "font": {"family": "JetBrains Mono, monospace", "size": 30, "color": "#F3F4F6"},
                "valueformat": ".1f",
                "suffix": "%",
            },
            gauge={
                "axis": {
                    "range": [0, 100],
                    "tickcolor": "#374151",
                    "tickfont": {"family": "JetBrains Mono, monospace", "size": 9, "color": "#9CA3AF"},
                },
                "bar": {
                    "color": "#06B6D4"
                    if rarity < 0.7
                    else ("#F59E0B" if rarity < 0.9 else "#EF4444")
                },
                "steps": [
                    {"range": [0, 70], "color": "rgba(6, 182, 212, 0.12)"},
                    {"range": [70, 90], "color": "rgba(245, 158, 11, 0.18)"},
                    {"range": [90, 100], "color": "rgba(239, 68, 68, 0.25)"},
                ],
                "threshold": {
                    "line": {"color": "#F3F4F6", "width": 2},
                    "thickness": 0.8,
                    "value": 95.0,
                },
            },
        )
    )
    fig_gauge.update_layout(
        height=230,
        margin={"t": 30, "b": 10, "l": 20, "r": 20},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_gauge)

# --- CONTEXT & STATUS ----------------------------------------------------------------------
context_col, status_col = st.columns(2)

with context_col, st.container(border=True):
    st.subheader(":material/info: Context")
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

with status_col, st.container(border=True):
    st.subheader(":material/construction: Prototype Status")
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
