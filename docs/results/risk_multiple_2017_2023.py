"""
FloodSense - "About N times the usual flood risk for a rainy hour" (T2a), from 2017-2023 only.

    uv run python docs/results/risk_multiple_2017_2023.py            # writes the JSON beside it
    uv run python docs/results/risk_multiple_2017_2023.py --seed 7   # verification run, prints only

Definition (fixed before any number was looked at):

- Unit: one zone x one 5-minute step that passes the active-rain gate (>= 0.2 mm in the zone's last
  120 minutes; ``config.active_rain_mask``). These are exactly the rows of the feature store, which
  leaves out gated-out (dry) rows. A "rainy hour" in the resident line means such a step: the zone
  has had rain in the last two hours.
- Outcome: ``label_prob`` from the training label policy (``labels.policy.label_rows``): the
  probability that a verified flood *starts* in that zone within the next 60 minutes. Rows where the
  zone may already be flooding are excluded (NaN), as in training. Soft labels (imprecise report
  times) count fractionally, so "flood-steps" is a sum of probabilities.
- Usual risk (base rate): mean label over all gated, non-excluded steps, all 55 zones pooled.
- Tier risk: mean label over the steps the served champion puts in that tier. Tiers come from the
  champion's own calibrated probability and thresholds (``models/flood_model.joblib``, version 3,
  Moderate 0.531%, High 0.706%), read from the artifact and not recomputed.
- N = tier risk / usual risk. The time unit cancels, so N is the same per step or per hour.

Why this is leakage-free enough to show, and the one caveat:
``rule_rain60`` has no fitted parameters (score = rain_60m / (rain_60m + 25)); the Platt calibrator
is monotone, so tier membership is simply "rain_60m >= the millimetre cut" (37.65 / 43.15 mm) in
every year. The cuts were chosen on the 2020-2023 out-of-fold curve by the false-alarm budget,
which counts false alarms against these labels, so 2020-2023 is in-sample for the cut choice.
2017-2019 never touched the cuts, the calibrator or model selection: it is reported separately as
the clean check. The pooled 2017-2023 figure is the headline because it has twice the floods.

Interval: percentile bootstrap, resampling whole SGT calendar days (floods and alert steps
cluster within a storm day), B = 4000, seed 20261021. A 7-day-block bootstrap is a sensitivity.

Test-set rule: only 2017-2023 feature partitions are read, and the code asserts that no feature row
and no used flood event is from 2024 onward, and that leaving out the 2024+ events cannot change
any 2017-2023 label.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from floodsense.common.config import active_rain_mask, settings
from floodsense.data import flood_events
from floodsense.features.build_features import load_feature_store
from floodsense.labels.policy import event_window, label_rows
from floodsense.models.artifact import load_model
from floodsense.models.refit_champion import mm_equivalents

FIRST_YEAR = 2017
LAST_YEAR = 2023
assert LAST_YEAR == settings.last_training_year < settings.test_start_year == 2024
TEST_START = pd.Timestamp(year=settings.test_start_year, month=1, day=1, tz=settings.tzinfo)

SEED = 20261021
B = 4000
CI = (5.0, 95.0)  # 90% interval
TIERS = ("moderate", "high", "moderate_or_higher")
OUT_JSON = Path(__file__).with_suffix(".json")

# Display rule, written before the numbers were seen. "about N times" only when the 90% interval
# spans at most a factor of 2 (hi / lo <= 2); otherwise "at least L times" with L the lower bound
# rounded down, if L >= 2; otherwise withhold the line.
ABOUT_MAX_SPREAD = 2.0
AT_LEAST_MIN = 2.0


# --------------------------------------------------------------------------------------------
# Pure helpers (tested in tests/test_risk_multiple.py)
# --------------------------------------------------------------------------------------------


def assign_tiers(prob: np.ndarray, thresholds: dict[str, float]) -> dict[str, np.ndarray]:
    """Masks for the tiers exactly as ``scoring.risk_tier`` draws them (>= is inclusive)."""
    high = prob >= thresholds["high"]
    mod_up = prob >= thresholds["moderate"]
    return {"moderate": mod_up & ~high, "high": high, "moderate_or_higher": mod_up}


def per_day_sums(
    day: np.ndarray, label: np.ndarray, masks: dict[str, np.ndarray]
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Per-day (steps, flood-steps) for "all" and each mask: arrays shaped (days, groups)."""
    codes, uniq = pd.factorize(day, sort=True)
    groups = ["all", *masks]
    n_days = len(uniq)
    steps = np.zeros((n_days, len(groups)))
    floods = np.zeros((n_days, len(groups)))
    for j, g in enumerate(groups):
        m = np.ones(len(label), dtype=bool) if g == "all" else masks[g]
        steps[:, j] = np.bincount(codes[m], minlength=n_days)
        floods[:, j] = np.bincount(codes[m], weights=label[m], minlength=n_days)
    return steps, floods, groups


def ratio(steps: np.ndarray, floods: np.ndarray) -> np.ndarray:
    """N per group (column 0 is the base): (floods_g / steps_g) / (floods_0 / steps_0).

    Works on (groups,) totals or on (B, groups) bootstrap totals. A group with no steps gives NaN.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        rate = floods / steps
        return rate / rate[..., :1]


def block_bootstrap(
    steps: np.ndarray, floods: np.ndarray, block: np.ndarray, n_boot: int, seed: int
) -> np.ndarray:
    """Bootstrap N, resampling whole blocks (rows of the per-day arrays grouped by ``block``)."""
    codes, uniq = pd.factorize(block, sort=True)
    k = len(uniq)
    bs = np.zeros((k, steps.shape[1]))
    bf = np.zeros((k, steps.shape[1]))
    np.add.at(bs, codes, steps)
    np.add.at(bf, codes, floods)
    rng = np.random.default_rng(seed)
    w = rng.multinomial(k, np.full(k, 1.0 / k), size=n_boot).astype(float)  # (B, k)
    return ratio(w @ bs, w @ bf)


def display_phrase(lo: float, hi: float) -> dict[str, Any]:
    """The pre-stated display rule applied to one 90% interval."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo <= 0:
        return {"show": False, "phrase": None, "rule": "interval reaches 0 or is undefined"}
    if hi / lo <= ABOUT_MAX_SPREAD:
        n = round(math.sqrt(lo * hi))
        return {"show": True, "phrase": f"about {n} times", "rule": "hi/lo <= 2"}
    floor = math.floor(lo)
    if floor >= AT_LEAST_MIN:
        return {"show": True, "phrase": f"at least {floor} times", "rule": "lower 90% bound"}
    return {"show": False, "phrase": None, "rule": "lower 90% bound below 2"}


# --------------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------------


def load_development() -> tuple[pd.DataFrame, list[Any], dict[str, int]]:
    years = list(range(FIRST_YEAR, LAST_YEAR + 1))
    features = load_feature_store(years=years)
    ts = features["timestamp"].dt.tz_convert(settings.tzinfo)
    assert ts.max() < TEST_START, "a 2024+ feature row was read"
    assert ts.dt.year.between(FIRST_YEAR, LAST_YEAR).all()

    events = flood_events.load_flood_events()
    horizon = timedelta(minutes=settings.prediction_lead_time_minutes)
    dev_events: list[Any] = []
    later: list[Any] = []
    for e in events:
        (later if event_window(e).start_lo >= TEST_START else dev_events).append(e)
    # Dropping the 2024+ events must not change a 2017-2023 label: their earliest positive row
    # (start_lo - horizon) must be in 2024 too.
    for e in later:
        assert event_window(e).start_lo - horizon >= TEST_START, e.event_id
    for e in dev_events:
        assert event_window(e).start_lo < TEST_START
    counts = {"events_loaded": len(events), "events_2017_2023": len(dev_events)}
    return features, dev_events, counts


def git_state() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=settings.root_dir, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "git_sha": run("rev-parse", "--short", "HEAD") or None,
        "dirty": bool(run("status", "--porcelain")),
    }


# --------------------------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------------------------


def summarise(
    frame: pd.DataFrame, masks: dict[str, np.ndarray], windows: dict[str, Any], seed: int
) -> dict[str, Any]:
    """Point estimates, raw counts and both bootstraps for one period."""
    label = frame["label_prob"].to_numpy()
    day = frame["day"].to_numpy()
    steps, floods, groups = per_day_sums(day, label, masks)
    point = ratio(steps.sum(0), floods.sum(0))
    days = np.array(sorted(set(day)))
    by_day = block_bootstrap(steps, floods, days, B, seed)
    week_block = (pd.to_datetime(days) - pd.Timestamp(f"{FIRST_YEAR}-01-01")).days // 7
    by_week = block_bootstrap(steps, floods, np.asarray(week_block), B, seed + 1)

    out: dict[str, Any] = {
        "days": int(len(days)),
        "base": {
            "steps": int(steps[:, 0].sum()),
            "zone_hours": round(float(steps[:, 0].sum()) / 12, 1),
            "flood_steps": round(float(floods[:, 0].sum()), 2),
            "rate_per_step_pct": round(100 * float(floods[:, 0].sum() / steps[:, 0].sum()), 4),
            "floods_with_a_positive_step": int(_floods_touching(frame, None, windows)),
        },
        "tiers": {},
    }
    for j, g in enumerate(groups[1:], start=1):
        n_steps, n_floods = float(steps[:, j].sum()), float(floods[:, j].sum())
        d_lo, d_hi = np.nanpercentile(by_day[:, j], CI)
        w_lo, w_hi = np.nanpercentile(by_week[:, j], CI)
        out["tiers"][g] = {
            "steps": int(n_steps),
            "zone_hours": round(n_steps / 12, 1),
            "flood_steps": round(n_floods, 2),
            "rate_per_step_pct": round(100 * n_floods / n_steps, 4) if n_steps else None,
            "mean_calibrated_prob_pct": round(100 * float(frame.loc[masks[g], "prob"].mean()), 4),
            "floods_with_a_positive_step": int(_floods_touching(frame, masks[g], windows)),
            "days_with_tier_steps": int((steps[:, j] > 0).sum()),
            "days_with_tier_flood_steps": int((floods[:, j] > 0).sum()),
            "N": round(float(point[j]), 2),
            "ci90_day_blocks": [round(float(d_lo), 2), round(float(d_hi), 2)],
            "ci90_week_blocks": [round(float(w_lo), 2), round(float(w_hi), 2)],
            "boot_share_N_below_1": round(float(np.mean(by_day[:, j] < 1)), 4),
            "display": display_phrase(float(d_lo), float(d_hi)),
        }
    return out


def _floods_touching(frame: pd.DataFrame, mask: np.ndarray | None, windows: dict[str, Any]) -> int:
    """Distinct floods with at least one row (label_prob > 0) in the mask: a flood that a step at
    that tier preceded within 60 minutes (or possibly preceded, for imprecise report times)."""
    rows = frame if mask is None else frame.loc[mask]
    rows = rows[rows["label_prob"] > 0]
    hit = set()
    t = rows["timestamp"]
    for eid, w in windows.items():
        lo = w.start_lo - timedelta(minutes=settings.prediction_lead_time_minutes)
        m = (rows["ura_planning_area"] == w.zone) & (t >= lo) & (t < w.start_lo)
        if m.any():
            hit.add(eid)
    return len(hit)


def run(seed: int) -> dict[str, Any]:
    features, events, counts = load_development()
    labels, report = label_rows(features, events)
    model = load_model()
    assert model is not None and model.thresholds is not None, "models/flood_model.joblib missing"
    assert model.provenance.get("candidate") == "rule_rain60"
    thresholds = dict(model.thresholds)  # read from the champion, not recomputed

    frame = features[["ura_planning_area", "timestamp", "rain_60m", "rain_120m"]].copy()
    frame["label_prob"] = labels["label_prob"]
    gated = active_rain_mask(frame["rain_120m"])
    missing = frame["rain_60m"].isna().to_numpy()
    excluded = frame["label_prob"].isna().to_numpy()
    keep = gated & ~missing & ~excluded
    frame = frame.loc[keep].reset_index(drop=True)
    frame["prob"] = model.predict_proba(features.loc[keep].reset_index(drop=True))
    frame["day"] = frame["timestamp"].dt.tz_convert(settings.tzinfo).dt.strftime("%Y-%m-%d")
    year = frame["timestamp"].dt.tz_convert(settings.tzinfo).dt.year.to_numpy()
    assert year.max() <= LAST_YEAR

    windows = {w.event_id: w for w in map(event_window, events)}
    periods = {
        "2017_2023": (FIRST_YEAR, LAST_YEAR),
        "2017_2019_clean": (FIRST_YEAR, settings.cv_first_validation_year - 1),
        "2020_2023_cut_selection_years": (settings.cv_first_validation_year, LAST_YEAR),
    }
    results = {}
    for name, (a, b) in periods.items():
        sel = (year >= a) & (year <= b)
        sub = frame.loc[sel].reset_index(drop=True)
        masks = assign_tiers(sub["prob"].to_numpy(), thresholds)
        win = {k: w for k, w in windows.items() if a <= w.start_lo.year <= b}
        results[name] = summarise(sub, masks, win, seed)

    # Millimetre cut equivalent of each threshold, read off the champion (monotone in rain_60m).
    mm = mm_equivalents(model)
    # Tier membership must be exactly "rain_60m >= cut": check it on the rows used.
    for k, v in thresholds.items():
        cut = mm[k]
        assert cut is not None
        assert (frame.loc[frame["rain_60m"] >= cut, "prob"] >= v).all()
        assert (frame.loc[frame["rain_60m"] < cut - 0.05, "prob"] < v).all()

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        **git_state(),
        "script": "docs/results/risk_multiple_2017_2023.py",
        "years_used": [FIRST_YEAR, LAST_YEAR],
        "test_years_read": False,
        "model": {
            "path": "models/flood_model.joblib",
            "candidate": model.provenance.get("candidate"),
            "model_git_sha": model.provenance.get("git_sha"),
            "calibration": model.provenance.get("calibration"),
            "thresholds": thresholds,
            "thresholds_pct": {k: round(100 * v, 3) for k, v in thresholds.items()},
            "rain_60m_mm_equivalent": mm,
        },
        "definition": {
            "unit": "zone x 5-minute step passing the active-rain gate (>= 0.2 mm in last 120 min)",
            "outcome": "label_prob: P(a verified flood starts in the zone within 60 min); "
            "in-flood rows excluded; soft labels count fractionally",
            "base": "mean outcome over all gated steps, 55 zones pooled",
            "tier": "mean outcome over steps the champion puts in the tier "
            "(moderate = [moderate, high), high = >= high)",
            "N": "tier rate / base rate (unit-free)",
            "zone_hours": "steps / 12",
        },
        "rows": {
            "feature_rows_2017_2023": int(len(features)),
            "failing_gate": int((~gated).sum()),
            "missing_rain_60m_dropped": int(missing.sum()),
            "in_flood_excluded": int(excluded.sum()),
            "used": int(keep.sum()),
            **counts,
            "events_without_rows": report.events_without_rows,
        },
        "bootstrap": {
            "method": "percentile; resample SGT calendar days (sensitivity: 7-day blocks)",
            "B": B,
            "seed": seed,
            "interval": "90%",
        },
        "display_rule": (
            f"'about N times' only if hi/lo <= {ABOUT_MAX_SPREAD} (day-block 90% CI); else "
            f"'at least L times' with L = floor(lower bound) if L >= {AT_LEAST_MIN:g}; else withhold"
        ),
        "headline_period": "2017_2023",
        "periods": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    payload = run(args.seed)
    if args.seed == SEED and not args.no_write:
        OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {OUT_JSON}")
    for name, res in payload["periods"].items():
        print(name, "base", res["base"])
        for t, r in res["tiers"].items():
            keys = (
                "steps",
                "flood_steps",
                "floods_with_a_positive_step",
                "N",
                "ci90_day_blocks",
                "ci90_week_blocks",
                "display",
            )
            print("  ", t, {k: r[k] for k in keys})
    return 0


if __name__ == "__main__":
    sys.exit(main())
