"""
FloodSense - Prior-SD sensitivity of ``rule_rain60_static`` (post-hoc, not used for selection).

    python -m floodsense.models.prior_sensitivity
    python -m floodsense.models.prior_sensitivity --date 2026-10-16 --prior-sd 0.05 0.1 0.25 0.5

Re-runs the cross-validation of ``rule_rain60_static`` with several prior SDs on the static
coefficients, with the same folds, verified labels and false-alarm budgets as
``select_candidates`` (it reuses ``select_candidates.summarise``), and writes

    models/candidates/prior_sensitivity_<date>.json

Only 2017-2023 development rows are used (``LabelledData.development()``); the test years are never
scored. 0.5 is the pre-registered prior SD (``DEFAULT_PRIOR_SD``); the others are a post-hoc look.
For comparison it also runs ``rule_rain60_nostatic`` once and lists, per tier, which floods each
prior SD gained or lost against it.

The output records ``git_sha`` and a ``dirty`` flag. The script refuses to run on a tree with
uncommitted changes unless ``--allow-dirty`` is given (then the output says ``"dirty": true``).
"""

import argparse
import logging
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from floodsense.common.config import MODEL_FEATURE_COLUMNS, settings
from floodsense.features.build_features import DataUnavailableError
from floodsense.models import static_candidates
from floodsense.models.artifact import git_sha
from floodsense.models.select_candidates import candidates_dir, summarise
from floodsense.models.static_candidates import (
    DEFAULT_PRIOR_SD,
    RuleStaticAdjustment,
    fit_static_candidate,
    load_zone_static,
)
from floodsense.models.train import _write_json, load_training_data
from floodsense.models.training import LabelledData, cross_validate

logger = logging.getLogger("FloodSense.PriorSensitivity")

CANDIDATE = "rule_rain60_static"
BASELINE = "rule_rain60_nostatic"
DEFAULT_PRIOR_SDS: list[float] = [0.05, 0.1, 0.25, 0.5]


def git_dirty() -> bool | None:
    """True if the working tree has uncommitted changes (``git status --porcelain`` is not
    empty); None if git cannot be run."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            cwd=settings.root_dir,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(out.strip())


@contextmanager
def _prior_sd(sd: float) -> Iterator[None]:
    """Temporarily make ``rule_rain60_static`` use ``sd`` as its prior SD."""
    original = static_candidates.static_factories

    def patched(cols: list[str]) -> dict[str, Callable[[], Any]]:
        factories = original(cols)
        index, n = cols.index("rain_60m"), len(cols)
        factories[CANDIDATE] = lambda: RuleStaticAdjustment(index, n, prior_sd=sd)
        return factories

    static_candidates.static_factories = patched  # type: ignore[assignment]
    try:
        yield
    finally:
        static_candidates.static_factories = original  # type: ignore[assignment]


def _run(name: str, dev: LabelledData, zone_static: dict[str, list[float]]) -> dict[str, Any]:
    result = cross_validate(
        name,
        dev,
        MODEL_FEATURE_COLUMNS,
        fit=lambda n, d, c: fit_static_candidate(n, d, c, zone_static),
    )
    return summarise(result, dev)


def _vs_baseline(summary: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tier in ("high", "moderate"):
        a = summary["tiers"].get(tier)
        b = base["tiers"].get(tier)
        if a is None or b is None:
            continue
        ao, bo = a["outcomes"], b["outcomes"]
        out[tier] = {
            "gained": sorted(i for i in ao if ao[i] and not bo.get(i)),
            "lost": sorted(i for i in ao if bo.get(i) and not ao[i]),
        }
    return out


def run_sensitivity(
    data: LabelledData, prior_sds: Sequence[float], run_date: str | None = None
) -> dict[str, Any]:
    run_date = run_date or date.today().isoformat()
    dev = data.development()  # 2017..last_training_year only; test years never reach this
    zone_static = load_zone_static()

    baseline = _run(BASELINE, dev, zone_static)
    runs: list[dict[str, Any]] = []
    for sd in prior_sds:
        logger.info("Cross-validating %s with prior_sd=%g", CANDIDATE, sd)
        with _prior_sd(sd):
            summary = _run(CANDIDATE, dev, zone_static)
        logger.info("prior_sd=%g: event score %.3f", sd, summary["event_score"])
        runs.append(
            {
                "prior_sd": sd,
                "pre_registered": sd == DEFAULT_PRIOR_SD,
                "summary": summary,
                f"vs_{BASELINE}": _vs_baseline(summary, baseline),
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "dirty": git_dirty(),
        "data": {
            "development_rows": len(dev.features),
            "events_in_development": len(dev.windows),
            "labels": "verified rows only (flood_events.load_flood_events default)",
            "test_years_scored": False,
        },
        "status": f"post-hoc sensitivity; not used for selection; {DEFAULT_PRIOR_SD} is pre-registered",
        "budgets": {
            "high": settings.false_alarm_budget_high,
            "moderate": settings.false_alarm_budget_moderate,
        },
        "candidate": CANDIDATE,
        "baseline": {"name": BASELINE, "summary": baseline},
        "runs": runs,
    }


def _print(payload: dict[str, Any]) -> None:
    for run in payload["runs"]:
        s = run["summary"]
        h, m = s["tiers"]["high"], s["tiers"]["moderate"]
        print(
            f"prior_sd={run['prior_sd']:g}: score {s['event_score']:.3f} "
            f"high {int(h['hits'])}/{int(h['events'])} "
            f"(90% CI {h['hit_rate_ci90'][0]:.0%}-{h['hit_rate_ci90'][1]:.0%}) "
            f"fa {h['false_episodes_per_zone_year']:.2f} | "
            f"moderate {int(m['hits'])}/{int(m['events'])} "
            f"(90% CI {m['hit_rate_ci90'][0]:.0%}-{m['hit_rate_ci90'][1]:.0%}) "
            f"fa {m['false_episodes_per_zone_year']:.2f}"
        )
        for tier, d in run[f"vs_{BASELINE}"].items():
            print(f"    {tier} vs {BASELINE}: gained {d['gained']} lost {d['lost']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Post-hoc prior-SD sensitivity of rule_rain60_static (2017-2023 CV only)."
    )
    parser.add_argument("--date", default=None, help="date in the output file name (YYYY-MM-DD)")
    parser.add_argument(
        "--prior-sd", nargs="+", type=float, default=DEFAULT_PRIOR_SDS, help="prior SDs to run"
    )
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="run on a tree with uncommitted changes (the output is marked dirty)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dirty = git_dirty()
    if dirty and not args.allow_dirty:
        logger.error(
            "The working tree has uncommitted changes; commit first so the run is "
            "reproducible from git_sha (or pass --allow-dirty for a trial run)."
        )
        return 2
    try:
        data = load_training_data()
    except DataUnavailableError as exc:
        logger.error("%s", exc)
        return 1
    run_date = args.date or date.today().isoformat()
    payload = run_sensitivity(data, args.prior_sd, run_date)
    out_dir: Path = args.out or candidates_dir()
    _write_json(out_dir / f"prior_sensitivity_{run_date}.json", payload)
    _print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
