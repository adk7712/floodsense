"""
FloodSense - the resident / town-council view.

A plain-language page for non-technical readers: pick your area, read the risk now, the risk in
an hour (when the radar forecast exists), and what to do. It draws no charts or maps and uses no
technical terms. It takes the risk tables the analyst view already computed, so the two views
always agree.
"""

import html
from collections.abc import Callable, Mapping
from datetime import datetime

import pandas as pd
import streamlit as st

from floodsense.alerts.messages import ADVICE, DISCLAIMER

# The same wording as the Telegram alerts and the zone panel (alerts/messages.py).
ADVICE_FOR_TIER = ADVICE
RISK_NOW = {
    "High": "High risk of a flash flood",
    "Moderate": "Moderate risk of a flash flood",
    "Low": "Low risk of a flash flood",
}
FORECAST_CAVEAT = "Forecast from radar; flood accuracy not yet tested."

TierStyle = Callable[[str, bool], tuple[str, str, str]]


def _tier_of(table: pd.DataFrame, zone: str) -> str:
    return str(table.loc[table["ura_planning_area"] == zone, "risk_tier"].iloc[0])


def render_resident_view(
    *,
    risk_now: pd.DataFrame,
    risk_in_1h: pd.DataFrame | None,
    when: datetime,
    is_replay: bool,
    tier_style: TierStyle,
    is_dark: bool,
    tokens: Mapping[str, str],
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
        f"{DISCLAIMER}</div>",
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
