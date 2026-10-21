"""Turn ``pip-licenses --from=mixed --format=json --with-urls`` output into a Markdown table.

Licences are reported exactly as declared in package metadata. Nothing is guessed: a package
whose licence is UNKNOWN, empty or missing is marked UNVERIFIED so a person can check it.
Informational only: exits 0 unless the input cannot be parsed (exit 2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

OVERLAY_TOOLS = frozenset({"pip-licenses", "prettytable", "wcwidth"})
OWN_PACKAGE = "floodsense"
STATUS_DECLARED = "as declared in package metadata"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_OWN = "own package, no licence declared"


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _is_unverified(licence: str) -> bool:
    """True when the declared string does not name a specific licence."""
    low = licence.strip().lower()
    return (
        low in {"", "unknown", "osi approved"}
        or low.endswith(":: osi approved")
        or "other/proprietary" in low
        or "dual license" in low
        or low.startswith("licenseref-")
    )


def build_rows(packages: list[dict[str, Any]]) -> list[tuple[str, str, str, str, str]]:
    """Return (name, version, licence, url, status) rows sorted by name, overlay tools skipped."""
    rows = []
    for pkg in packages:
        name = str(pkg.get("Name") or "").strip()
        if not name or _norm(name) in OVERLAY_TOOLS:
            continue
        licence = str(pkg.get("License") or "").strip()
        if _norm(name) == OWN_PACKAGE:
            status = STATUS_OWN
        elif _is_unverified(licence):
            status = STATUS_UNVERIFIED
        else:
            status = STATUS_DECLARED
        rows.append(
            (name, str(pkg.get("Version") or ""), licence, str(pkg.get("URL") or ""), status)
        )
    return sorted(rows, key=lambda r: r[0].lower())


def render(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Render the Markdown table plus a one-line summary."""
    lines = ["| Name | Version | Licence | URL | Status |", "| --- | --- | --- | --- | --- |"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines) + "\n\n" + summary(rows) + "\n"


def summary(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Return 'N packages, M UNVERIFIED: a, b, c'."""
    bad = [r[0] for r in rows if r[4] == STATUS_UNVERIFIED]
    return f"{len(rows)} packages, {len(bad)} UNVERIFIED: {', '.join(bad) if bad else 'none'}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON from pip-licenses --format=json")
    parser.add_argument("--output", type=Path, default=None, help="write Markdown here")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(isinstance(p, dict) for p in data):
            raise ValueError("expected a JSON list of objects")
    except (OSError, ValueError) as exc:
        print(f"licence_report: cannot parse {args.input}: {exc}", file=sys.stderr)
        return 2
    rows = build_rows(data)
    text = render(rows)
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    print(summary(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
