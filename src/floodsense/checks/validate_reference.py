"""
FloodSense - offline check of the committed reference CSVs (run in CI).

    uv run python -m floodsense.checks.validate_reference

Fails (exit 1) and lists every problem if:
  * ``flood_events.csv`` has a missing column or a blank source_url, source_name, evidence_quote,
    time_precision or verified_by (CLAUDE.md rule 5; unlike ``load_flood_events``, which keeps
    blank-verified rows as "unverified", this check rejects them);
  * ``zone_static.csv`` has a missing column, a blank source_name, source_url or retrieved_at,
    or is not exactly 55 data rows.
"""

import argparse
import csv
from collections.abc import Sequence
from pathlib import Path

from floodsense.common.config import settings

FLOOD_EVENTS_REQUIRED = [
    "source_url",
    "source_name",
    "evidence_quote",
    "time_precision",
    "verified_by",
]
ZONE_STATIC_REQUIRED = ["source_name", "source_url", "retrieved_at"]
ZONE_STATIC_ROWS = 55


def _read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = [{k: (v or "") for k, v in row.items() if k is not None} for row in reader]
        return list(reader.fieldnames or []), rows


def validate_flood_events(path: Path) -> list[str]:
    """Return one message per problem in ``flood_events.csv`` (empty list = OK)."""
    name = path.name
    if not path.exists():
        return [f"{name}: file not found at {path}"]
    columns, rows = _read(path)
    problems = [f"{name}: missing column {c}" for c in FLOOD_EVENTS_REQUIRED if c not in columns]
    if not rows:
        problems.append(f"{name} has no data rows")
    present = [c for c in FLOOD_EVENTS_REQUIRED if c in columns]
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        for col in present:
            if not row.get(col, "").strip():
                eid = row.get("event_id", "").strip() or "?"
                problems.append(f"{name} row {n} (event_id {eid}): blank {col}")
    return problems


def validate_zone_static(path: Path) -> list[str]:
    """Return one message per problem in ``zone_static.csv`` (empty list = OK)."""
    name = path.name
    if not path.exists():
        return [f"{name}: file not found at {path}"]
    columns, rows = _read(path)
    problems = [f"{name}: missing column {c}" for c in ZONE_STATIC_REQUIRED if c not in columns]
    if len(rows) != ZONE_STATIC_ROWS:
        problems.append(f"{name}: expected {ZONE_STATIC_ROWS} data rows, found {len(rows)}")
    present = [c for c in ZONE_STATIC_REQUIRED if c in columns]
    for n, row in enumerate(rows, start=2):
        for col in present:
            if not row.get(col, "").strip():
                zone = row.get("ura_planning_area", "").strip() or "?"
                problems.append(f"{name} row {n} (zone {zone}): blank {col}")
    return problems


def _count_rows(path: Path) -> int:
    return len(_read(path)[1])


def main(argv: Sequence[str] | None = None) -> int:
    ref = settings.root_dir / "data" / "reference"
    parser = argparse.ArgumentParser(description="Validate the committed reference CSVs.")
    parser.add_argument("--flood-events", type=Path, default=ref / "flood_events.csv")
    parser.add_argument("--zone-static", type=Path, default=ref / "zone_static.csv")
    args = parser.parse_args(argv)

    problems = validate_flood_events(args.flood_events) + validate_zone_static(args.zone_static)
    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        print(f"{len(problems)} problem(s) found.")
        return 1
    print(
        f"OK: {args.flood_events.name} ({_count_rows(args.flood_events)} rows), "
        f"{args.zone_static.name} ({_count_rows(args.zone_static)} rows)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
