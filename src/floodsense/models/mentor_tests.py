"""
FloodSense - Mentor suggestions, round 2 (B7): the pre-registered tests in
``docs/mentor_tests_2026-10.md``. Writes nothing the app serves.

    python -m floodsense.models.mentor_tests --date 2026-10-18 --tests t1 t2 t3

Each test is a one-input rule with exactly the ``rule_rain60`` procedure
(``training.cross_validate``: forward-chaining folds 2020-2023, cross-fitted calibration,
thresholds from the false-alarm budget rule). Only the 60-minute rain input changes:

    T1  rain_60m x C_zone, C = 0.9 b + 0.3 (1 - b), b = built-up share of land pixels
    T2  60-minute sum of (kriged mean + 1 kriging SD), from ``features.krig_upper``
    T3  (gated) Moderate from rule_rain30, High from rule_rain60

and writes ``models/candidates/mentor_tests_<date>.json``. Only 2017-2023 development rows are
used; 2024-2026 is never scored. The adoption margin is fixed in the pre-registration doc and
applied by ``verdict``.
"""

import argparse
import json
import logging
import subprocess
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from floodsense.common.config import settings
from floodsense.features.build_features import DataUnavailableError
from floodsense.models.artifact import git_sha
from floodsense.models.prior_sensitivity import git_dirty
from floodsense.models.select_candidates import (
    BASELINE,
    candidates_dir,
    paired_difference_ci,
    summarise,
)
from floodsense.models.static_candidates import zone_static_path
from floodsense.models.train import _write_json, load_training_data
from floodsense.models.training import LabelledData, cross_validate

logger = logging.getLogger("FloodSense.MentorTests")

PREREG_DOC = "docs/mentor_tests_2026-10.md"
RUNOFF_BUILT, RUNOFF_OTHER = 0.9, 0.3  # rational-method coefficients (paved, vegetated)
LAND_COLUMN = "built_up_land_pct"
KRIG_COLUMN = "rain_60m_krig_upper"
SCORE_MARGIN = 0.05  # (a) event score at least baseline + 0.05
MAX_OTHER_TIER_LOSS = 1  # (c) the other tier loses at most 1 flood
T3_MIN_UNIQUE = 3  # T3 runs only if more than 2 floods are Moderate-caught by rain30 alone
TIERS = ("high", "moderate")


# --------------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------------


def runoff_coefficients(path: Path | None = None) -> dict[str, float]:
    """C per zone from the built-up share of land pixels (fixed coefficients, not fitted)."""
    df = pd.read_csv(path or zone_static_path())
    if LAND_COLUMN not in df.columns or df[LAND_COLUMN].isna().any():
        raise ValueError(f"zone_static needs a complete {LAND_COLUMN} column")
    b = df[LAND_COLUMN].astype(float) / 100.0
    if ((b < 0) | (b > 1)).any():
        raise ValueError(f"{LAND_COLUMN} must be within 0-100")
    c = RUNOFF_BUILT * b + RUNOFF_OTHER * (1 - b)
    return dict(zip(df["ura_planning_area"].astype(str), c.astype(float), strict=True))


def with_rain_60m(dev: LabelledData, values: pd.Series) -> LabelledData:
    """A copy of ``dev`` whose ``rain_60m`` is replaced by ``values`` (aligned to its index), so
    ``rule_rain60``'s exact procedure scores the new input. Nothing else changes."""
    if not values.index.equals(dev.features.index):
        raise ValueError("replacement input must be aligned to the feature rows")
    if values.isna().any():
        raise ValueError(f"replacement input has {int(values.isna().sum())} missing rows")
    features = dev.features.copy()
    features["rain_60m"] = values.astype(float)
    return LabelledData(features, dev.label_prob, dev.windows)


def runoff_input(dev: LabelledData, coeffs: dict[str, float]) -> pd.Series:
    zones = dev.features["ura_planning_area"].astype(str)
    missing = sorted(set(zones) - set(coeffs))
    if missing:
        raise ValueError(f"no runoff coefficient for zones {missing}")
    return dev.features["rain_60m"] * zones.map(coeffs)


def krig_upper_input(dev: LabelledData, krig_dir: Path) -> pd.Series:
    """The kriged-upper 60-minute rain for every development row, read from
    ``features.krig_upper``'s output. Fails if any row is not covered (never filled)."""
    files = sorted(krig_dir.glob("year=*/month=*.parquet"))
    if not files:
        raise DataUnavailableError(
            f"No kriged-upper features in {krig_dir}. Build them with "
            "`python -m floodsense.features.krig_upper --start-year 2017 --end-year 2023`."
        )
    krig = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    keys = ["ura_planning_area", "timestamp"]
    krig["timestamp"] = krig["timestamp"].dt.tz_convert(settings.tzinfo)
    left = dev.features[keys].copy()
    left["timestamp"] = left["timestamp"].dt.tz_convert(settings.tzinfo)
    left["_row"] = dev.features.index
    merged = left.merge(krig[[*keys, KRIG_COLUMN]], on=keys, how="left", validate="one_to_one")
    out = pd.Series(merged[KRIG_COLUMN].to_numpy(), index=pd.Index(merged["_row"]))
    out = out.reindex(dev.features.index)
    if out.isna().any():
        raise ValueError(f"{int(out.isna().sum())} development rows have no kriged-upper value")
    return out


# --------------------------------------------------------------------------------------------
# Comparison and verdict
# --------------------------------------------------------------------------------------------


def tier_comparison(cand: dict[str, Any], base: dict[str, Any], tier: str) -> dict[str, Any]:
    """Paired comparison over the same floods at one tier."""
    a, b = cand["tiers"][tier]["outcomes"], base["tiers"][tier]["outcomes"]
    if set(a) != set(b):
        raise ValueError("candidate and baseline must be scored on the same floods")
    ids = sorted(a)
    lo, hi = paired_difference_ci([a[i] for i in ids], [b[i] for i in ids])
    gained = [i for i in ids if a[i] and not b[i]]
    lost = [i for i in ids if b[i] and not a[i]]
    return {
        "candidate_hits": int(sum(a.values())),
        "baseline_hits": int(sum(b.values())),
        "floods": len(ids),
        "difference": (sum(a.values()) - sum(b.values())) / len(ids) if ids else float("nan"),
        "paired_ci90": [lo, hi],
        "gained": gained,
        "lost": lost,
        "candidate_false_episodes_per_zone_year": cand["tiers"][tier][
            "false_episodes_per_zone_year"
        ],
        "baseline_false_episodes_per_zone_year": base["tiers"][tier][
            "false_episodes_per_zone_year"
        ],
    }


def verdict(cand: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered margin (T1, T2): (a) event score >= baseline + 0.05; (b) some tier's
    paired 90% CI lower bound > 0; (c) the other tier loses at most 1 flood."""
    tiers = {t: tier_comparison(cand, base, t) for t in TIERS}
    a = cand["event_score"] >= base["event_score"] + SCORE_MARGIN - 1e-12
    winners = [t for t in TIERS if tiers[t]["paired_ci90"][0] > 0]
    b = bool(winners)
    c = any(
        tiers[o]["baseline_hits"] - tiers[o]["candidate_hits"] <= MAX_OTHER_TIER_LOSS
        for w in winners
        for o in TIERS
        if o != w
    )
    adopted = a and b and c
    return {
        "event_score": cand["event_score"],
        "baseline_event_score": base["event_score"],
        "tiers": tiers,
        "criteria": {"a_event_score": a, "b_tier_ci_above_0": b, "c_other_tier_loss_le_1": c},
        "adopted": adopted,
        "result": "adopt" if adopted else "not distinguishable, kept the simpler rule",
    }


def t3_gate(selection_json: Path) -> dict[str, Any]:
    """Floods caught at Moderate by rule_rain30 but missed by rule_rain60, from the saved
    17 Oct per-flood outcomes (the pre-registered gate)."""
    payload = json.loads(selection_json.read_text())
    r30 = payload["candidates"]["rule_rain30"]["tiers"]["moderate"]["outcomes"]
    r60 = payload["candidates"][BASELINE]["tiers"]["moderate"]["outcomes"]
    only30 = sorted(i for i in r30 if r30[i] and not r60.get(i, False))
    only60 = sorted(i for i in r60 if r60[i] and not r30.get(i, False))
    return {
        "source": str(selection_json.relative_to(settings.root_dir))
        if selection_json.is_relative_to(settings.root_dir)
        else str(selection_json),
        "rain30_moderate_hits": int(sum(r30.values())),
        "rain60_moderate_hits": int(sum(r60.values())),
        "caught_by_rain30_only": only30,
        "caught_by_rain60_only": only60,
        "run_t3": len(only30) >= T3_MIN_UNIQUE,
    }


def t3_verdict(r30: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """T3 margin: the Moderate paired CI lower bound > 0 and Moderate false episodes within
    budget (High is rule_rain60's, unchanged by construction)."""
    mod = tier_comparison(r30, base, "moderate")
    budget = settings.false_alarm_budget_moderate
    within = budget is not None and mod["candidate_false_episodes_per_zone_year"] <= budget
    adopted = mod["paired_ci90"][0] > 0 and within
    return {
        "moderate": mod,
        "high": "rule_rain60's High, unchanged",
        "criteria": {"moderate_ci_above_0": mod["paired_ci90"][0] > 0, "within_budget": within},
        "adopted": adopted,
        "result": "adopt" if adopted else "not distinguishable, kept the simpler rule",
    }


# --------------------------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------------------------


def prereg_commit() -> str | None:
    """The commit that last touched the pre-registration doc (proof it predates the run)."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %cI", "--", PREREG_DOC],
            capture_output=True,
            text=True,
            check=True,
            cwd=settings.root_dir,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


def _strip(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in summary.items() if k != "folds"} | {
        "validation_years": [f["validation_year"] for f in summary["folds"]]
    }


def run_tests(
    data: LabelledData,
    tests: Sequence[str],
    krig_dir: Path | None = None,
    zone_static: Path | None = None,
    selection_json: Path | None = None,
    run_date: str | None = None,
) -> dict[str, Any]:
    dev = data.development()  # 2017..last_training_year only; test years never reach this
    base = summarise(cross_validate(BASELINE, dev), dev)
    results: dict[str, Any] = {}
    if "t1" in tests:
        coeffs = runoff_coefficients(zone_static)
        cand = summarise(
            cross_validate(BASELINE, with_rain_60m(dev, runoff_input(dev, coeffs))), dev
        )
        results["t1_runoff"] = {
            "input": "rain_60m x C, C = 0.9 b + 0.3 (1 - b), b = built_up_land_pct / 100",
            "runoff_coefficient": {
                "min": min(coeffs.values()),
                "max": max(coeffs.values()),
                "by_zone": coeffs,
            },
            "summary": _strip(cand),
            "verdict": verdict(cand, base),
        }
    if "t2" in tests:
        kdir = krig_dir or settings.root_dir / "data" / "processed" / "features_krig_upper"
        cand = summarise(
            cross_validate(BASELINE, with_rain_60m(dev, krig_upper_input(dev, kdir))), dev
        )
        results["t2_krig_upper"] = {
            "input": "sum over 12 steps of (kriged mean + 1 kriging SD), clipped at 0",
            "summary": _strip(cand),
            "verdict": verdict(cand, base),
        }
    if "t3" in tests:
        gate = t3_gate(selection_json or candidates_dir() / "selection_2026-10-17.json")
        entry: dict[str, Any] = {"gate": gate}
        if gate["run_t3"]:
            r30 = summarise(cross_validate("rule_rain30", dev), dev)
            entry["rain30_summary"] = _strip(r30)
            entry["verdict"] = t3_verdict(r30, base)
        else:
            entry["verdict"] = {"result": "skipped: rain30's extra Moderate catches overlap"}
        results["t3_hybrid"] = entry
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_date": run_date or date.today().isoformat(),
        "git_sha": git_sha(),
        "dirty": git_dirty(),
        "preregistration": {"doc": PREREG_DOC, "last_commit": prereg_commit()},
        "data": {
            "labels": "verified rows only (flood_events.load_flood_events default)",
            "development_events": len(dev.windows),
            "test_years_scored": False,
        },
        "budgets": {
            "high": settings.false_alarm_budget_high,
            "moderate": settings.false_alarm_budget_moderate,
        },
        "margin": {
            "event_score_margin": SCORE_MARGIN,
            "tier_ci": "paired 90% bootstrap over floods, lower bound > 0",
            "other_tier_max_loss": MAX_OTHER_TIER_LOSS,
        },
        "baseline": _strip(base),
        "tests": results,
    }


def _print(payload: dict[str, Any]) -> None:
    base = payload["baseline"]
    print(f"baseline {BASELINE}: event score {base['event_score']:.3f}")
    for name, entry in payload["tests"].items():
        v = entry["verdict"]
        print(f"{name}: {v['result']}")
        for tier, row in v.get("tiers", {}).items():
            lo, hi = row["paired_ci90"]
            print(
                f"  {tier}: {row['candidate_hits']}/{row['floods']} vs {row['baseline_hits']}/"
                f"{row['floods']} (paired 90% CI {lo:+.0%} to {hi:+.0%}), fa/zone-yr "
                f"{row['candidate_false_episodes_per_zone_year']:.2f} vs "
                f"{row['baseline_false_episodes_per_zone_year']:.2f}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pre-registered mentor tests (2020-2023 CV only).")
    parser.add_argument("--date", default=None, help="date in the output file name (YYYY-MM-DD)")
    parser.add_argument(
        "--tests", nargs="+", choices=["t1", "t2", "t3"], default=["t1", "t2", "t3"]
    )
    parser.add_argument("--krig-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    parser.add_argument(
        "--allow-dirty", action="store_true", help="run on a dirty tree (results are not final)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if git_dirty() and not args.allow_dirty:
        logger.error("Working tree is dirty; commit first (or --allow-dirty for a trial run).")
        return 2
    if prereg_commit() is None:
        logger.error("%s is not committed; the pre-registration must predate the run.", PREREG_DOC)
        return 2
    try:
        data = load_training_data()
        run_date = args.date or date.today().isoformat()
        payload = run_tests(data, args.tests, krig_dir=args.krig_dir, run_date=run_date)
    except DataUnavailableError as exc:
        logger.error("%s", exc)
        return 1
    out_dir: Path = args.out or candidates_dir()
    _write_json(out_dir / f"mentor_tests_{run_date}.json", payload)
    _print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
