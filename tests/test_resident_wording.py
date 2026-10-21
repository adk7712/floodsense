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
