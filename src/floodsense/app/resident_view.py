"""
FloodSense - the resident / town-council view.

A plain-language page for non-technical readers: pick your area, read the risk now, the risk in
an hour (when the radar forecast exists), and what to do. It draws no charts or maps and uses no
technical terms. It takes the risk tables the analyst view already computed, so the two views
always agree.
"""

import html
import re
from collections.abc import Callable, Mapping
from datetime import datetime

import pandas as pd
import streamlit as st

from floodsense.alerts.messages import ADVICE, DISCLAIMER, HONESTY_LINE
from floodsense.common.schemas import FloodEvent
from floodsense.ingestion.flood_alerts import FloodAlert
from floodsense.models.scoring import NO_DATA_TIER, risk_tier

RISK_NOW = {
    "High": "High risk of a flash flood",
    "Moderate": "Moderate risk of a flash flood",
    "Low": "Low risk of a flash flood",
    NO_DATA_TIER: "No gauge data — risk unknown",
}
NO_DATA_ADVICE = (
    "We have no rain gauge readings for this area right now, so we can't tell the flood risk. "
    "Check PUB's official flood alerts and take care if heavy rain starts."
)
PARTIAL_NOTE = (
    "Some rain readings from the last hour are missing, so the real risk may be higher than shown."
)
PUB_ALERTS_UNAVAILABLE = "PUB flood alerts can't be checked right now."
FORECAST_CAVEAT = "Forecast from radar; flood accuracy not yet tested."

# Action line per tier (T2): a verb and a time window, shown since 22 Oct (team decision; adk to
# review the strings). PUB-style "avoid ... for at least one hour" is left out: no sourced PUB
# wording has been checked yet, so it is UNVERIFIED and not drafted here. The "N times the usual
# risk" figure (docs/results/risk_multiple_2017_2023.*) is not shown to residents (22 Oct).
TIER_ACTION_LINE = {
    "High": "Flooding is likelier than usual in the next hour. Act now.",
    "Moderate": "Flooding is possible in the next hour. Prepare now.",
}

TierStyle = Callable[[str, bool], tuple[str, str, str]]


def areas_at_risk(table: pd.DataFrame) -> str:
    """How many planning areas are at High or Moderate risk, in one line. Shared by the Analyst
    headline and the Resident card, so the two views always say the same thing."""
    tiers = table["risk_tier"]
    high, moderate = int((tiers == "High").sum()), int((tiers == "Moderate").sum())
    if (tiers == NO_DATA_TIER).all():
        return "Risk unknown: too few rain gauges are reporting"
    if high:
        return f"{high} of {len(tiers)} areas at High risk" + (
            f", {moderate} more at Moderate" if moderate else ""
        )
    if moderate:
        return f"{moderate} of {len(tiers)} areas at Moderate risk, none at High"
    return "No area at Moderate or High risk"


def reported_floods_line(events: list[FloodEvent]) -> str:
    """The floods reported on a replayed day, in time order, with each one's time as precise as
    its source gives it. Shared by the Analyst headline and the Resident card."""
    reported = [
        f"{e.ura_planning_area.title()} "
        + (
            "(time not reported)"
            if e.time_precision == "day_only"
            else f"({e.timestamp_start:%H:%M}"
            + {"exact": "", "pub_issued": ", PUB alert time"}.get(e.time_precision, " approx.")
            + ")"
        )
        for e in sorted(events, key=lambda e: (e.timestamp_start, e.ura_planning_area))
    ]
    return f"Floods reported this day: {', '.join(dict.fromkeys(reported))}."


def _tier_of(table: pd.DataFrame, zone: str) -> str:
    """The zone's tier; "No data" when the table has no row for it (never a crash)."""
    tiers = table.loc[table["ura_planning_area"] == zone, "risk_tier"]
    return str(tiers.iloc[0]) if len(tiers) else NO_DATA_TIER


def _coverage_of(table: pd.DataFrame, zone: str) -> str:
    """ "ok", "partial" or "none" for the zone; "ok" when the table carries no coverage column."""
    if "coverage" not in table.columns:
        return "ok"
    cov = table.loc[table["ura_planning_area"] == zone, "coverage"]
    return str(cov.iloc[0]) if len(cov) else "none"


_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~$:<&])")


def escape_markdown(text: str) -> str:
    """Show ``text`` literally in st.markdown / st.error: no links, bold, lists or emoji codes."""
    return _MARKDOWN_SPECIALS.sub(r"\\\1", text)


def pct(p: float, thresholds: dict[str, float] | None = None) -> str:
    """A probability as a percentage to 3 significant figures ("0.706%").

    With ``thresholds``, a value just under a threshold gets more digits until it no longer
    prints as the threshold, so the text never contradicts the tier."""
    if pd.isna(p):
        return "—"
    value = p * 100
    if value < 0.001:
        return "<0.001%" if value > 0 else "0%"
    text = f"{value:.3g}"
    if thresholds:
        exact = {k: round(v, 12) for k, v in thresholds.items()}
        for digits in range(3, 10):
            text = f"{value:.{digits}g}"
            if risk_tier(round(float(text) / 100, 12), exact) == risk_tier(round(p, 12), exact):
                break
    return f"{text}%"


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
        text = escape_markdown(text)  # PUB's words, shown as plain text
        st.error(
            f"**PUB flood alert active here:** {text} (issued {a.issued_at:%H:%M} SGT, "
            "official PUB alert)",
            icon=":material/flood:",
        )


def render_resident_view(
    *,
    zone: str,
    risk_now: pd.DataFrame,
    risk_in_1h: pd.DataFrame | None,
    when: datetime,
    is_replay: bool,
    tier_style: TierStyle,
    is_dark: bool,
    tokens: Mapping[str, str],
    pub_alerts: list[FloodAlert] | None = None,
    pub_alerts_unavailable: bool = False,
    stale_age: str | None = None,
    day_events: list[FloodEvent] | None = None,
) -> str:
    """Draw the resident page for ``zone`` (picked in the sidebar, as in the Analyst view) and
    return it.

    ``pub_alerts_unavailable``: the PUB alert fetch failed (said so, instead of showing nothing).
    ``stale_age``: how old the data is, as text, when it is past the stale limit; the tier is then
    drawn faded under an "out of date" line. ``day_events``: the floods reported on a replayed day
    (None in Live), listed in the card under the Singapore-wide line."""
    tier = _tier_of(risk_now, zone)
    coverage = _coverage_of(risk_now, zone)
    bg, border, colour = tier_style(tier, is_dark)
    name = html.escape(zone.title())

    if stale_age:
        st.warning(f"Out of date: the latest data is {stale_age} old, so this may not be current.")
    # One card, coloured by the chosen area's tier: its risk now, the rest of Singapore, and the
    # radar forecast for an hour from now (when there is radar for this time).
    # A replay shows the replayed time, never "right now".
    when_text = f" at {when:%H:%M}, {when:%d %b %Y}" if is_replay else ", right now"
    island = areas_at_risk(risk_now)
    soon = ""
    if risk_in_1h is not None:
        tier_1h = _tier_of(risk_in_1h, zone)
        _, border_1h, colour_1h = tier_style(tier_1h, is_dark)
        soon = (
            f'<div class="fs-res-soon" style="border-top:1px solid {border}; '
            f'color:{tokens["text_main"]};">In 1 hour: '
            f'<span class="fs-res-chip" style="color:{colour_1h}; border:2px solid {border_1h}; '
            f'background:{tokens["bg_surface"]};">'
            f"{'Unknown' if tier_1h == NO_DATA_TIER else tier_1h}</span>"
            f'<span class="fs-res-note" style="color:{tokens["text_muted"]};">'
            f"{FORECAST_CAVEAT}</span></div>"
        )
    st.markdown(
        f'<div class="fs-res-card" style="background:{bg}; border:2px solid {border};'
        f'{" opacity:0.55;" if stale_age else ""}">'
        f'<div class="fs-res-small" style="color:{tokens["text_muted"]};">{name}{when_text}</div>'
        f'<div class="fs-res-big" style="color:{colour};">{RISK_NOW[tier]}</div>'
        f'<div class="fs-res-island" style="color:{tokens["text_main"]};">'
        f"Across Singapore: {html.escape(island[0].lower() + island[1:])}.</div>"
        + (
            f'<div class="fs-res-reported" style="color:{tokens["text_main"]};">'
            f"{html.escape(reported_floods_line(day_events))}</div>"
            if day_events
            else ""
        )
        + f"{soon}</div>",
        unsafe_allow_html=True,
    )

    if coverage == "partial" and tier != NO_DATA_TIER:
        st.caption(PARTIAL_NOTE)
    show_pub_alerts(pub_alerts, zone)
    if pub_alerts_unavailable:
        st.caption(PUB_ALERTS_UNAVAILABLE)

    if tier in TIER_ACTION_LINE:
        st.markdown(f"**{TIER_ACTION_LINE[tier]}**")

    st.markdown('<div class="fs-res-step">What to do</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="fs-res-advice" style="border-left:6px solid {colour}; '
        f'background:{tokens["bg_surface"]}; color:{tokens["text_main"]};">'
        f"{NO_DATA_ADVICE if tier == NO_DATA_TIER else ADVICE[tier]}</div>",
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
.fs-res-island { font-size: 1.1rem; font-weight: 600; margin-top: 12px; }
.fs-res-reported { font-size: 1.05rem; margin-top: 6px; }
.fs-res-soon { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px;
               font-size: 1.3rem; font-weight: 700; margin-top: 16px; padding-top: 14px; }
.fs-res-chip { display: inline-block; padding: 2px 16px; border-radius: 9999px; }
.fs-res-note { flex-basis: 100%; font-size: 0.95rem; font-weight: 400; }
.fs-res-advice { font-size: 1.35rem; line-height: 1.5; padding: 18px 22px; border-radius: 10px; }
.fs-res-foot { font-size: 1rem; margin-top: 28px; line-height: 1.6; }
.st-key-resident_page div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
.st-key-resident_page div[data-testid="stSelectbox"] input { font-size: 1.2rem !important; }
.st-key-resident_page div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    min-height: 3.2rem !important; }
"""
