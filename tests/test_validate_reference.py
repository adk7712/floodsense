"""Tests for the CI reference-data check (offline)."""

import csv
from pathlib import Path

from floodsense.checks.validate_reference import (
    FLOOD_EVENTS_REQUIRED,
    ZONE_STATIC_REQUIRED,
    main,
    validate_flood_events,
    validate_zone_static,
)

EV_COLS = ["event_id", "ura_planning_area", *FLOOD_EVENTS_REQUIRED]
ZONE_COLS = ["ura_planning_area", *ZONE_STATIC_REQUIRED]


def _write(path: Path, cols: list[str], rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, restval="x")
        w.writeheader()
        w.writerows(rows)
    return path


def _event(**over: str) -> dict[str, str]:
    row = {c: "x" for c in EV_COLS}
    row["event_id"] = "ev1"
    row["drafted_by"] = "y"
    row.update(over)
    return row


def _zones(n: int = 55, **over: str) -> list[dict[str, str]]:
    rows = [{c: "x" for c in ZONE_COLS} | {"ura_planning_area": f"Z{i}"} for i in range(n)]
    rows[0].update(over)
    return rows


def test_committed_files_pass() -> None:
    assert main([]) == 0


def test_blank_verified_by(tmp_path: Path) -> None:
    p = _write(tmp_path / "e.csv", EV_COLS, [_event(), _event(event_id="ev2", verified_by="")])
    assert validate_flood_events(p) == ["e.csv row 3 (event_id ev2): blank verified_by"]


def test_whitespace_evidence_quote(tmp_path: Path) -> None:
    p = _write(tmp_path / "e.csv", EV_COLS, [_event(evidence_quote="   ")])
    assert validate_flood_events(p) == ["e.csv row 2 (event_id ev1): blank evidence_quote"]


def test_missing_column(tmp_path: Path) -> None:
    cols = [c for c in EV_COLS if c != "source_name"]
    p = _write(tmp_path / "e.csv", cols, [{c: "x" for c in cols}])
    assert "e.csv: missing column source_name" in validate_flood_events(p)


def test_zone_54_rows(tmp_path: Path) -> None:
    p = _write(tmp_path / "z.csv", ZONE_COLS, _zones(54))
    assert validate_zone_static(p) == ["z.csv: expected 55 data rows, found 54"]


def test_zone_blank_retrieved_at(tmp_path: Path) -> None:
    p = _write(tmp_path / "z.csv", ZONE_COLS, _zones(retrieved_at=""))
    assert validate_zone_static(p) == ["z.csv row 2 (zone Z0): blank retrieved_at"]


def test_zone_missing_column(tmp_path: Path) -> None:
    cols = [c for c in ZONE_COLS if c != "source_url"]
    p = _write(tmp_path / "z.csv", cols, [{c: "x" for c in cols}] * 55)
    assert validate_zone_static(p) == ["z.csv: missing column source_url"]


def test_main_fails_on_bad_input(tmp_path: Path, capsys: object) -> None:
    ev = _write(tmp_path / "e.csv", EV_COLS, [_event(verified_by="")])
    zs = _write(tmp_path / "z.csv", ZONE_COLS, _zones())
    assert main(["--flood-events", str(ev), "--zone-static", str(zs)]) == 1
    good = _write(tmp_path / "e2.csv", EV_COLS, [_event()])
    assert main(["--flood-events", str(good), "--zone-static", str(zs)]) == 0


def test_bom_prefixed_files_pass(tmp_path: Path) -> None:
    ev = _write(tmp_path / "e.csv", EV_COLS, [_event()])
    zs = _write(tmp_path / "z.csv", ZONE_COLS, _zones())
    for p in (ev, zs):
        p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
    assert main(["--flood-events", str(ev), "--zone-static", str(zs)]) == 0


def test_header_only_flood_events_fails(tmp_path: Path) -> None:
    p = _write(tmp_path / "e.csv", EV_COLS, [])
    assert validate_flood_events(p) == ["e.csv has no data rows"]


def test_drafter_cannot_be_the_verifier(tmp_path: Path) -> None:
    p = _write(tmp_path / "e.csv", EV_COLS, [_event(verified_by="ab", drafted_by="ab")])
    assert validate_flood_events(p) == ["e.csv row 2 (event_id ev1): drafted_by is the verifier"]
