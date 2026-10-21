"""
FloodSense - Resident-view draft wording (T2): off by default, and the "N times" line only
shows what the saved 2017–2023 result allows.
"""

import json
from pathlib import Path

from floodsense.app import resident_view as rv


def _file(tmp_path: Path, show: bool) -> Path:
    path = tmp_path / "m.json"
    entry = {"display": {"show": show, "phrase": "at least 116 times"}}
    path.write_text(
        json.dumps({"headline_period": "p", "periods": {"p": {"tiers": {"high": entry}}}})
    )
    return path


def test_draft_wording_is_off_by_default():
    assert rv.RISK_WORDING_DRAFT is False


def test_multiple_line_uses_the_saved_phrase(tmp_path):
    line = rv.risk_multiple_line("High", _file(tmp_path, True))
    assert line is not None and line.startswith("At least 116 times the usual flood risk")


def test_multiple_line_hidden_when_rule_says_hide_or_data_missing(tmp_path):
    assert rv.risk_multiple_line("High", _file(tmp_path, False)) is None
    assert rv.risk_multiple_line("Moderate", _file(tmp_path, True)) is None  # High only
    assert rv.risk_multiple_line("Low", _file(tmp_path, True)) is None
    assert rv.risk_multiple_line("High", tmp_path / "missing.json") is None


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
