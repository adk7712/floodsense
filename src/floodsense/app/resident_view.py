"""
FloodSense - the resident / town-council view.

A plain-language page for non-technical readers: pick your area, read the risk now, the risk in
an hour (when the radar forecast exists), and what to do. It draws no charts or maps and uses no
technical terms. It takes the risk tables the analyst view already computed, so the two views
always agree.
"""

import html
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from floodsense.alerts.messages import ADVICE, DISCLAIMER, HONESTY_LINE
from floodsense.ingestion.flood_alerts import FloodAlert

# The same wording as the Telegram alerts and the zone panel (alerts/messages.py).
ADVICE_FOR_TIER = ADVICE
RISK_NOW = {
    "High": "High risk of a flash flood",
    "Moderate": "Moderate risk of a flash flood",
    "Low": "Low risk of a flash flood",
}
FORECAST_CAVEAT = "Forecast from radar; flood accuracy not yet tested."

# DRAFT wording (T2), off until adk signs off the wording and the figure. COPY CHANGE FOR REVIEW.
# Each tier gets a verb and a time window. PUB-style "avoid ... for at least one hour" is left
# out: no sourced PUB wording has been checked yet, so it is UNVERIFIED and not drafted here.
RISK_WORDING_DRAFT = False
DRAFT_TIER_LINE = {
    "High": "Flooding is likelier than usual in the next hour. Act now.",
    "Moderate": "Flooding is possible in the next hour. Prepare now.",
}
RISK_MULTIPLE_FILE = (
    Path(__file__).resolve().parents[3] / "docs/results/risk_multiple_2017_2023.json"
)

TierStyle = Callable[[str, bool], tuple[str, str, str]]


def risk_multiple_line(tier: str, path: Path = RISK_MULTIPLE_FILE) -> str | None:
    """The "N times the usual flood risk for a rainy hour" line, from the 2017–2023 figures.

    Reads the saved result (``docs/results/risk_multiple_2017_2023.py``) and shows its own
    ``display.phrase`` only when its rule allows it; None for Low, a missing file or a hidden
    figure. 2017–2023 data only (never the 2024–2026 test set)."""
    # High only: the Moderate figure is not recommended (docs/results/risk_multiple_2017_2023.md).
    if tier != "High":
        return None
    try:
        data = json.loads(path.read_text())
        entry = data["periods"][data["headline_period"]]["tiers"][tier.lower()]
        display = entry["display"]
        return (
            f"{str(display['phrase']).capitalize()} the usual flood risk for a rainy hour "
            "(Singapore-wide, from 2017–2023 floods)."
            if display["show"]
            else None
        )
    except (OSError, KeyError, ValueError, TypeError):
        return None


def _tier_of(table: pd.DataFrame, zone: str) -> str:
    return str(table.loc[table["ura_planning_area"] == zone, "risk_tier"].iloc[0])


_ALERT_TEXT_MAX = 160
_ALERTS_SHOWN = 3


def show_pub_alerts(alerts: list[FloodAlert] | None, zone: str) -> None:
    """The official PUB flood alert(s) active in ``zone``, in PUB's own words; nothing if none.

    An alert belongs to a zone only when its centre point falls inside that planning area
    (``FloodAlert.zone``, from the zone polygons). An alert without a point, or at sea, is never
    attached to a zone. PUB's alert is official; FloodSense's tier is a model, so it is styled
    apart from the tier card. Shared by the Zone Diagnostic panel and the Resident view."""
    here = [a for a in alerts or [] if a.msg_type == "Alert" and a.zone == zone]
    for a in here[:_ALERTS_SHOWN]:
        text = " ".join(a.description.split()) or a.headline or "flash flood observed"
        if len(text) > _ALERT_TEXT_MAX:
            text = text[: _ALERT_TEXT_MAX - 1].rstrip() + "…"
        st.error(
            f"**PUB flood alert active here:** {text} (issued {a.issued_at:%H:%M} SGT, "
            "official PUB alert)",
            icon=":material/flood:",
        )


def render_resident_view(
    *,
    risk_now: pd.DataFrame,
    risk_in_1h: pd.DataFrame | None,
    when: datetime,
    is_replay: bool,
    tier_style: TierStyle,
    is_dark: bool,
    tokens: Mapping[str, str],
    pub_alerts: list[FloodAlert] | None = None,
) -> str:
    """Draw the resident page and return the chosen planning area."""
    names = sorted(risk_now["ura_planning_area"])
    st.markdown('<div class="fs-res-step">Your area</div>', unsafe_allow_html=True)
    zone = str(
        st.selectbox(
            "Choose your area",
            options=names,
            key="zone",
            format_func=str.title,
            label_visibility="collapsed",
            placeholder="Type to search for your area",
        )
    )
    tier = _tier_of(risk_now, zone)
    bg, border, colour = tier_style(tier, is_dark)
    name = html.escape(zone.title())

    st.markdown(
        f'<div class="fs-res-card" style="background:{bg}; border:2px solid {border};">'
        f'<div class="fs-res-small" style="color:{tokens["text_muted"]};">{name}, right now</div>'
        f'<div class="fs-res-big" style="color:{colour};">{RISK_NOW[tier]}</div>'
        "</div>",
        unsafe_allow_html=True,
    )

    show_pub_alerts(pub_alerts, zone)

    if RISK_WORDING_DRAFT and tier in DRAFT_TIER_LINE:
        st.markdown(f"**{DRAFT_TIER_LINE[tier]}**")
        multiple = risk_multiple_line(tier)
        if multiple:
            st.caption(multiple)

    if risk_in_1h is not None:
        tier_1h = _tier_of(risk_in_1h, zone)
        _, border_1h, colour_1h = tier_style(tier_1h, is_dark)
        st.markdown(
            '<div class="fs-res-line">'
            f'<span style="color:{tokens["text_main"]};">In 1 hour: </span>'
            f'<span class="fs-res-chip" style="color:{colour_1h}; border:2px solid {border_1h};">'
            f"{tier_1h}</span></div>",
            unsafe_allow_html=True,
        )
        st.caption(FORECAST_CAVEAT)

    st.markdown('<div class="fs-res-step">What to do</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="fs-res-advice" style="border-left:6px solid {colour}; '
        f'background:{tokens["bg_surface"]}; color:{tokens["text_main"]};">'
        f"{ADVICE_FOR_TIER[tier]}</div>",
        unsafe_allow_html=True,
    )

    stamp = f"{when:%H:%M, %d %b %Y} (Singapore time)"
    st.markdown(
        f'<div class="fs-res-foot" style="color:{tokens["text_muted"]};">'
        f"{'Replay of past data: showing' if is_replay else 'Latest data from'} {stamp}.<br>"
        f"{DISCLAIMER} {HONESTY_LINE}</div>",
        unsafe_allow_html=True,
    )
    return zone


RESIDENT_CSS = """
.fs-res-step { font-size: 1.15rem; font-weight: 700; margin: 22px 0 8px 0; }
.fs-res-card { border-radius: 16px; padding: 26px 28px; margin: 18px 0 14px 0; }
.fs-res-small { font-size: 1.15rem; font-weight: 600; }
.fs-res-big { font-size: clamp(2rem, 5vw, 3.2rem); font-weight: 800; line-height: 1.15;
              margin-top: 6px; }
.fs-res-line { font-size: 1.5rem; font-weight: 700; margin: 10px 0 2px 0; }
.fs-res-chip { display: inline-block; padding: 2px 16px; border-radius: 9999px; }
.fs-res-advice { font-size: 1.35rem; line-height: 1.5; padding: 18px 22px; border-radius: 10px; }
.fs-res-foot { font-size: 1rem; margin-top: 28px; line-height: 1.6; }
.st-key-resident_page div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
.st-key-resident_page div[data-testid="stSelectbox"] input { font-size: 1.2rem !important; }
.st-key-resident_page div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    min-height: 3.2rem !important; }
"""
