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

from datetime import date
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from floodsense.app import replay_days
from floodsense.common.config import settings
from floodsense.common.schemas import FloodEvent, StationMetadata
from floodsense.data.flood_events import load_flood_events
from floodsense.data.replay import load_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.ingestion.poller import LiveFeedUnavailable, NEAPoller
from floodsense.models.artifact import load_model, rarity_scores
from floodsense.models.scoring import default_thresholds, score_zone_features
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

st.set_page_config(
    page_title="FloodSense | Urban Drainage Intelligence",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded",
)

TIER_COLORS = {"High": "#ff4d4f", "Moderate": "#faad14", "Low": "#52c41a"}
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
    status_line = (
        f":material/sensors: **Live** • data.gov.sg NEA 5-minute rainfall • "
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
    status_line = (
        f":material/history: **Replay** • NEA gauge readings • `{view_time:%d %b %Y %H:%M} SGT`"
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
    status_line = f":material/history: **Replay** • {event_name} • `{view_time:%d %b %Y %H:%M} SGT`"
    history_note = "Features include the 72 hours of real readings before the replay window."
    day_events = replay_days.events_on(replay_days.PINNED_STORM, flood_events())

df_results = score_zone_features(features, model).merge(ZONE_META, on="ura_planning_area")
df_results["zone"] = df_results["ura_planning_area"]
reporting_stations = int(features["reporting_stations"].iloc[0])

# --- HEADER & KPIs -------------------------------------------------------------------------
st.title("🌊 FloodSense Intelligence Center")
st.markdown(status_line)
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
    fig_map.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=500,
        legend=dict(yanchor="top", y=0.98, xanchor="left", x=0.02, bgcolor="rgba(0,0,0,0.6)"),
    )
    st.plotly_chart(fig_map)
    st.caption(f"{model_caption} {history_note} Each marker sits inside its URA planning area.")

with detail_col, st.container(border=True):
    st.subheader(f":material/analytics: Zone Diagnostic: `{selected_zone}`")
    zone_data = df_results[df_results["zone"] == selected_zone].iloc[0]

    tier_badge = {
        "High": ":red[**High Risk**]",
        "Moderate": ":orange[**Moderate Risk**]",
        "Low": ":green[**Low Risk**]",
    }[zone_data["risk_tier"]]
    st.markdown(f"**Status:** {tier_badge}")
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
            title={"text": rarity_title, "font": {"size": 13}},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {
                    "color": "#00d26a"
                    if rarity < 0.7
                    else ("#faad14" if rarity < 0.9 else "#ff4d4f")
                },
                "steps": [
                    {"range": [0, 70], "color": "rgba(82, 196, 26, 0.15)"},
                    {"range": [70, 90], "color": "rgba(250, 173, 20, 0.25)"},
                    {"range": [90, 100], "color": "rgba(255, 77, 79, 0.35)"},
                ],
                "threshold": {
                    "line": {"color": "white", "width": 3},
                    "thickness": 0.75,
                    "value": 95.0,
                },
            },
        )
    )
    fig_gauge.update_layout(
        height=230, margin={"t": 30, "b": 10, "l": 20, "r": 20}, paper_bgcolor="rgba(0,0,0,0)"
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
