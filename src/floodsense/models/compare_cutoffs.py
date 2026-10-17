"""
FloodSense - Head-to-head of Moderate cut-offs on the same out-of-fold ``rule_rain60`` predictions.

    python -m floodsense.models.compare_cutoffs --date 2026-10-17

Cross-validates ``rule_rain60`` once (forward-chaining folds 2020-2023, verified labels only,
cross-fitted calibration, exactly as ``select_candidates``), then evaluates the Moderate tier at a
list of cut-offs on those same out-of-fold probabilities and writes

    models/candidates/cutoff_comparison_<date>.json

This is a comparison to inform a team decision, not a re-selection: the false-alarm budget rule
itself picked 0.00531. Only 2017-2023 development rows are used; 2024-2026 is never scored.
"""

import argparse
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from floodsense.common.config import settings
from floodsense.features.build_features import DataUnavailableError
from floodsense.models.artifact import git_sha
from floodsense.models.evaluation import EventOutcome, evaluate_alerts, hit_rate_ci
from floodsense.models.prior_sensitivity import git_dirty
from floodsense.models.select_candidates import candidates_dir, validation_windows
from floodsense.models.train import _write_json, load_training_data
from floodsense.models.training import CandidateResult, LabelledData, cross_validate

logger = logging.getLogger("FloodSense.CompareCutoffs")

CANDIDATE = "rule_rain60"
CURRENT_MODERATE = 0.003
REFIT_MODERATE = 0.00531
HIGH = 0.00706
DEFAULT_CUTOFFS: list[float] = [0.002, 0.003, 0.004, 0.0045, REFIT_MODERATE, 0.006, HIGH]
SCALE_MM = 25.0  # RuleModel: p = x / (x + 25); used only for the mm equivalents, see _mm


def _mm(p: float) -> float:
    """Approximate 60-minute rain (mm) equivalent of a calibrated probability, by the refit's own
    mapping (0.00531 -> 37.65 mm, 0.00706 -> 43.15 mm) interpolated linearly. Indicative only."""
    (p0, m0), (p1, m1) = (REFIT_MODERATE, 37.65), (HIGH, 43.15)
    return m0 + (p - p0) * (m1 - m0) / (p1 - p0)


def _outcome_map(outcomes: Sequence[EventOutcome]) -> dict[str, EventOutcome]:
    return {o.event_id: o for o in outcomes}


def evaluate_cutoff(
    scored: pd.DataFrame,
    windows: Sequence[Any],
    cutoff: float,
    zone_years: float,
    high_hits: dict[str, bool] | None = None,
) -> tuple[dict[str, Any], dict[str, EventOutcome]]:
    """One cut-off on out-of-fold probabilities: hits with 90% CI, warning time, false episodes
    per zone-year and, given High-tier hits, the floods caught here but not at High."""
    summary, outcomes, _ = evaluate_alerts(scored, windows, cutoff, zone_years)
    by_id = _outcome_map(outcomes)
    row: dict[str, Any] = {
        "cutoff": cutoff,
        "cutoff_percent": cutoff * 100,
        "floods_caught": int(summary["hits"]),
        "floods": int(summary["events"]),
        "hit_rate": summary["hit_rate"],
        "hit_rate_ci90": hit_rate_ci([o.hit for o in outcomes]),
        "median_warning_minutes": summary["median_lead_minutes"],
        "actionable_hits_15min": int(summary["actionable_hits"]),
        "false_episodes": int(summary["false_episodes"]),
        "false_episodes_per_zone_year": summary["false_episodes_per_zone_year"],
        "false_alert_hours": summary["false_alert_hours"],
    }
    if high_hits is not None:
        extra = sorted(i for i, o in by_id.items() if o.hit and not high_hits.get(i, False))
        row["caught_here_not_at_high"] = extra
        row["n_caught_here_not_at_high"] = len(extra)
    return row, by_id


def differing_floods(
    a: dict[str, EventOutcome], b: dict[str, EventOutcome]
) -> list[dict[str, Any]]:
    """Floods whose outcome changes going from cut-off ``a`` to cut-off ``b`` (a -> b).

    ``change`` is "caught only at a" / "caught only at b" / "earlier at a" / "earlier at b"
    (both caught, different first-alert time). Identical outcomes are omitted."""
    out: list[dict[str, Any]] = []
    for eid in sorted(set(a) | set(b)):
        oa, ob = a.get(eid), b.get(eid)
        ha, hb = bool(oa and oa.hit), bool(ob and ob.hit)
        if ha and not hb:
            change = "caught only at a"
        elif hb and not ha:
            change = "caught only at b"
        elif ha and hb and oa and ob and oa.first_alert != ob.first_alert:
            fa, fb = oa.first_alert, ob.first_alert
            if fa is None or fb is None:
                continue
            change = "earlier at a" if fa < fb else "earlier at b"
        else:
            continue
        ref = oa or ob
        out.append(
            {
                "event_id": eid,
                "zone": ref.zone if ref else None,
                "change": change,
                "a_first_alert": str(oa.first_alert) if oa and oa.first_alert is not None else None,
                "b_first_alert": str(ob.first_alert) if ob and ob.first_alert is not None else None,
                "a_warning_minutes": oa.lead_minutes if oa else None,
                "b_warning_minutes": ob.lead_minutes if ob else None,
            }
        )
    return out


def sweep(
    result: CandidateResult, dev: LabelledData, cutoffs: Sequence[float], high: float = HIGH
) -> dict[str, Any]:
    windows = validation_windows(dev, result)
    zone_years = float(result.oof["ura_planning_area"].nunique() * len(result.folds))
    scored = result.oof[["ura_planning_area", "timestamp", "prob"]]
    high_row, high_out = evaluate_cutoff(scored, windows, high, zone_years)
    high_hits = {i: o.hit for i, o in high_out.items()}
    rows, outs = [], {}
    for c in cutoffs:
        row, by_id = evaluate_cutoff(scored, windows, c, zone_years, high_hits)
        row["approx_rain_60m_mm"] = _mm(c)
        rows.append(row)
        outs[c] = by_id
    diffs: list[dict[str, Any]] = []
    if CURRENT_MODERATE in outs and REFIT_MODERATE in outs:
        diffs = differing_floods(outs[CURRENT_MODERATE], outs[REFIT_MODERATE])
    return {
        "zone_years": zone_years,
        "validation_years": [f["validation_year"] for f in result.folds],
        "validation_events": len(windows),
        "high_reference": high_row,
        "moderate_cutoffs": rows,
        "floods_that_differ_0.003_vs_0.00531": {
            "a": CURRENT_MODERATE,
            "b": REFIT_MODERATE,
            "floods": diffs,
        },
    }


def run_comparison(
    data: LabelledData, cutoffs: Sequence[float] = DEFAULT_CUTOFFS, run_date: str | None = None
) -> dict[str, Any]:
    dev = data.development()  # 2017..last_training_year only; test years never reach this
    result = cross_validate(CANDIDATE, dev)
    body = sweep(result, dev, sorted(set(cutoffs)))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_date": run_date or date.today().isoformat(),
        "git_sha": git_sha(),
        "dirty": git_dirty(),
        "candidate": CANDIDATE,
        "data": {
            "labels": "verified rows only (flood_events.load_flood_events default)",
            "development_events": len(dev.windows),
            "test_years_scored": False,
        },
        "budgets": {
            "high": settings.false_alarm_budget_high,
            "moderate": settings.false_alarm_budget_moderate,
        },
        "notes": [
            "Comparison on out-of-fold 2020-2023 rule_rain60 predictions (cross-fitted "
            "calibration) to inform a team decision; it is not a re-selection. The false-alarm "
            "budget rule itself picked 0.00531 (Moderate) and 0.00706 (High).",
            "2024-2026 is not scored. Intervals are 90% bootstrap intervals over events; with "
            "this few floods they are wide and the cut-offs are not statistically distinguishable.",
            "approx_rain_60m_mm is a linear interpolation through the refit's two reported "
            "points (0.00531 -> 37.65 mm, 0.00706 -> 43.15 mm); indicative only, extrapolated "
            "outside that range.",
            "Warning time is the reported start minus the start of the alert episode; floods "
            "with date-only reports have no warning time and are left out of the median.",
        ],
        **body,
    }


def _print(payload: dict[str, Any]) -> None:
    h = payload["high_reference"]
    print(
        f"High {h['cutoff']:g}: {h['floods_caught']}/{h['floods']} "
        f"(90% CI {h['hit_rate_ci90'][0]:.0%}-{h['hit_rate_ci90'][1]:.0%}) "
        f"median warning {h['median_warning_minutes']:.0f} min, fa/zone-yr "
        f"{h['false_episodes_per_zone_year']:.2f}"
    )
    for r in payload["moderate_cutoffs"]:
        print(
            f"Moderate {r['cutoff']:<8g} ~{r['approx_rain_60m_mm']:.1f}mm "
            f"{r['floods_caught']}/{r['floods']} "
            f"(90% CI {r['hit_rate_ci90'][0]:.0%}-{r['hit_rate_ci90'][1]:.0%}) "
            f"warn {r['median_warning_minutes']:.0f} min "
            f"fa/zone-yr {r['false_episodes_per_zone_year']:.2f} "
            f"extra-over-High {r['n_caught_here_not_at_high']}"
        )
    for d in payload["floods_that_differ_0.003_vs_0.00531"]["floods"]:
        print(f"  differs: {d['event_id']} {d['change']} (a=0.003, b=0.00531)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare Moderate cut-offs on out-of-fold rule_rain60 (2020-2023 CV only)."
    )
    parser.add_argument("--date", default=None, help="date in the output file name (YYYY-MM-DD)")
    parser.add_argument("--cutoffs", nargs="+", type=float, default=DEFAULT_CUTOFFS)
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        data = load_training_data()
    except DataUnavailableError as exc:
        logger.error("%s", exc)
        return 1
    run_date = args.date or date.today().isoformat()
    payload = run_comparison(data, args.cutoffs, run_date)
    out_dir: Path = args.out or candidates_dir()
    _write_json(out_dir / f"cutoff_comparison_{run_date}.json", payload)
    _print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
