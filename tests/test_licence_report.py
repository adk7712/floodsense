"""Tests for the offline licence report (no network)."""

from __future__ import annotations

import json
from pathlib import Path

from floodsense.checks import licence_report as lr

PKGS = [
    {"Name": "zeta", "Version": "1", "License": "MIT", "URL": "https://z"},
    {"Name": "alpha", "Version": "2", "License": "UNKNOWN", "URL": "UNKNOWN"},
    {"Name": "beta", "Version": "3", "License": "", "URL": "https://b"},
    {"Name": "gamma", "Version": "4", "URL": "https://g"},
    {"Name": "pip-licenses", "Version": "5", "License": "MIT", "URL": "x"},
    {"Name": "PrettyTable", "Version": "5", "License": "BSD", "URL": "x"},
    {"Name": "wcwidth", "Version": "5", "License": "MIT", "URL": "x"},
    {"Name": "floodsense", "Version": "0.1", "License": "UNKNOWN", "URL": "UNKNOWN"},
]


def test_rows_sorted_skipped_and_statuses() -> None:
    rows = lr.build_rows(PKGS)
    assert [r[0] for r in rows] == ["alpha", "beta", "floodsense", "gamma", "zeta"]
    status = {r[0]: r[4] for r in rows}
    assert status["alpha"] == status["beta"] == status["gamma"] == lr.STATUS_UNVERIFIED
    assert status["zeta"] == lr.STATUS_DECLARED
    assert status["floodsense"] == lr.STATUS_OWN


def test_summary_line() -> None:
    assert lr.summary(lr.build_rows(PKGS)) == "5 packages, 3 UNVERIFIED: alpha, beta, gamma"


def test_cli_writes_markdown(tmp_path: Path, capsys: object) -> None:
    src = tmp_path / "in.json"
    src.write_text(json.dumps(PKGS), encoding="utf-8")
    out = tmp_path / "out.md"
    assert lr.main([str(src), "--output", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("| Name | Version | Licence | URL | Status |")
    assert "| zeta | 1 | MIT | https://z | as declared in package metadata |" in text


def test_unparseable_input_exits_2(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert lr.main([str(bad)]) == 2
    assert lr.main([str(tmp_path / "missing.json")]) == 2


def test_vague_licence_strings_are_unverified() -> None:
    vague = [
        "OSI Approved",
        " osi approved ",
        "License :: OSI Approved",
        "Other/Proprietary License",
        "Dual License",
        "LicenseRef-Foo",
        "unknown",
        "   ",
    ]
    for text in vague:
        assert lr._is_unverified(text), text
    for text in ["MIT", "BSD-3-Clause", "Apache-2.0", "BSD License; OSI Approved :: MIT"]:
        assert not lr._is_unverified(text), text
