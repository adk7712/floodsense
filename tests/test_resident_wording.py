"""
FloodSense - Resident-view wording (T2): the action line has a time window, and the page shows
PUB text literally.
"""

from floodsense.app import resident_view as rv


def test_action_line_has_a_time_window():
    for tier in ("High", "Moderate"):
        assert "next hour" in rv.TIER_ACTION_LINE[tier]
    assert "Low" not in rv.TIER_ACTION_LINE


def test_markdown_in_pub_text_is_shown_literally():
    escaped = rv.escape_markdown("Flash flood at [Click](http://x.invalid) **now**\n- injected")
    assert "[" not in escaped.replace("\\[", "") and "**" not in escaped.replace("\\*", "")
    assert rv.escape_markdown("plain words") == "plain words"


def test_percentages_use_three_figures_and_never_round_onto_a_threshold():
    from floodsense.models.scoring import risk_tier

    levels = {"moderate": 0.00531, "high": 0.00706}
    assert rv.pct(0.00706, levels) == "0.706%" and rv.pct(0.00531, levels) == "0.531%"
    just_under = 0.00706 - 4e-7  # Moderate, but 3 figures would print the High threshold
    assert risk_tier(just_under, levels) == "Moderate"
    assert rv.pct(just_under, levels) != "0.706%"
    assert float(rv.pct(just_under, levels).rstrip("%")) < 0.706
    assert rv.pct(0.1234) == "12.3%" and rv.pct(float("nan")) == "—" and rv.pct(1e-9) == "<0.001%"


def test_a_zone_missing_from_the_table_reads_no_data():
    import pandas as pd

    table = pd.DataFrame({"ura_planning_area": ["BEDOK"], "risk_tier": ["Low"]})
    assert rv._tier_of(table, "BEDOK") == "Low"
    assert rv._tier_of(table, "BUKIT TIMAH") == "No data"
    assert rv._coverage_of(table, "BEDOK") == "ok"


def test_ampersands_in_pub_text_show_literally():
    assert rv.escape_markdown("Bedok &amp; Tampines") == r"Bedok \&amp; Tampines"
