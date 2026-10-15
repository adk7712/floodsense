"""
FloodSense - Candidate selection report (B4.2, B4.2b, B2.4). Writes nothing the app serves.

    python -m floodsense.models.select_candidates            # all candidates, save the best new one
    python -m floodsense.models.select_candidates --date 2026-10-16

Runs the existing selection procedure (``training.cross_validate``: forward-chaining CV with
validation years ``settings.cv_first_validation_year``..``settings.last_training_year``, cross-
fitted calibration, thresholds from the false-alarm budget rule) on the core candidates and the
static-feature candidates (``static_candidates``), and writes

    models/candidates/selection_<date>.json   every candidate's out-of-fold results
    models/candidates/<name>.joblib           the best static-feature candidate, refitted on all
                                              development years with its calibrator and thresholds

It never touches ``models/flood_model.joblib``, ``model_card.json`` or ``final_report.json``, and
never scores the test years: only ``LabelledData.development()`` rows are passed on.
"""

import argparse
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from floodsense.common.config import MODEL_FEATURE_COLUMNS, settings
from floodsense.features.build_features import DataUnavailableError
from floodsense.models.artifact import FloodModel, git_sha
from floodsense.models.evaluation import evaluate_alerts, hit_rate_ci, select_threshold
from floodsense.models.static_candidates import (
    STATIC_CANDIDATES,
    RuleStaticAdjustment,
    fit_static_candidate,
    load_zone_static,
)
from floodsense.models.train import _write_json, load_training_data
from floodsense.models.training import (
    REFERENCE_BUDGETS,
    CandidateResult,
    LabelledData,
    candidate_factories,
    choose_thresholds,
    cross_validate,
)

logger = logging.getLogger("FloodSense.SelectCandidates")

# The B2.4 ablation pairs: (with static features, same candidate without them).
ABLATION_PAIRS: list[tuple[str, str]] = [
    ("rule_rain60_static", "rule_rain60_nostatic"),
    ("logistic_static", "logistic"),
    ("lightgbm_static", "lightgbm"),
]
BASELINE = "rule_rain60"


def candidates_dir() -> Path:
    return settings.models_dir / "candidates"


def validation_windows(dev: LabelledData, result: CandidateResult) -> list[Any]:
    """The events ``cross_validate`` scored: those starting in a validation year."""
    years = {f["validation_year"] for f in result.folds}
    return [w for w in dev.windows if int(w.start_lo.year) in years]


def paired_difference_ci(
    a: Sequence[bool], b: Sequence[bool], n_boot: int = 2000, alpha: float = 0.1, seed: int = 0
) -> tuple[float, float]:
    """Bootstrap (1 - alpha) interval for hit rate(a) - hit rate(b) over the same events,
    resampling events (same method and seed as ``evaluation.hit_rate_ci``)."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if x.shape != y.shape:
        raise ValueError("paired outcomes must cover the same events")
    if x.size == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, x.size, size=(n_boot, x.size))
    diffs = x[idx].mean(axis=1) - y[idx].mean(axis=1)
    return float(np.quantile(diffs, alpha / 2)), float(np.quantile(diffs, 1 - alpha / 2))


def tier_results(
    result: CandidateResult, dev: LabelledData, thresholds: dict[str, float] | None
) -> dict[str, Any]:
    """Out-of-fold event results at the budget thresholds, with 90% intervals and outcomes."""
    if thresholds is None:
        return {}
    windows = validation_windows(dev, result)
    zone_years = float(result.oof["ura_planning_area"].nunique() * len(result.folds))
    scored = result.oof[["ura_planning_area", "timestamp", "prob"]]
    out: dict[str, Any] = {}
    for tier in ("high", "moderate"):
        summary, outcomes, _ = evaluate_alerts(scored, windows, thresholds[tier], zone_years)
        out[tier] = {
            **summary,
            "hit_rate_ci90": hit_rate_ci([o.hit for o in outcomes]),
            "outcomes": {o.event_id: o.hit for o in outcomes},
        }
    return out


def summarise(result: CandidateResult, dev: LabelledData) -> dict[str, Any]:
    thresholds = choose_thresholds(result.curve)
    at_budgets = {}
    for budget in REFERENCE_BUDGETS:
        best = select_threshold(result.curve, budget)
        at_budgets[f"{budget:g}"] = (
            None
            if best is None
            else {k: float(best[k]) for k in ("threshold", "hits", "events", "hit_rate")}
        )
    return {
        "event_score": result.event_score,
        "pr_auc": result.row["pr_auc"],
        "brier": result.row["brier"],
        "ece": result.ece,
        "calibration_method": result.calibrator.method,
        "thresholds": thresholds,
        "tiers": tier_results(result, dev, thresholds),
        "hits_at_reference_budgets": at_budgets,
        "oof_rows": len(result.oof),
        "folds": result.folds,
    }


def ablation(summaries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """With vs without static features, per pair and tier, paired over the same events."""
    rows = []
    for with_static, without in ABLATION_PAIRS:
        if with_static not in summaries or without not in summaries:
            continue
        for tier in ("high", "moderate"):
            a = summaries[with_static]["tiers"].get(tier)
            b = summaries[without]["tiers"].get(tier)
            if a is None or b is None:
                continue
            ids = sorted(a["outcomes"])
            if ids != sorted(b["outcomes"]):
                raise ValueError(f"{with_static} and {without} scored different events")
            ha, hb = [a["outcomes"][i] for i in ids], [b["outcomes"][i] for i in ids]
            rows.append(
                {
                    "with_static": with_static,
                    "without_static": without,
                    "tier": tier,
                    "events": len(ids),
                    "hits_with": int(sum(ha)),
                    "hits_without": int(sum(hb)),
                    "gained": [i for i, x, y in zip(ids, ha, hb, strict=True) if x and not y],
                    "lost": [i for i, x, y in zip(ids, ha, hb, strict=True) if y and not x],
                    "difference_ci90": paired_difference_ci(ha, hb),
                    "false_episodes_per_zone_year_with": a["false_episodes_per_zone_year"],
                    "false_episodes_per_zone_year_without": b["false_episodes_per_zone_year"],
                }
            )
    return rows


def refit_candidate(
    name: str, dev: LabelledData, result: CandidateResult, zone_static: dict[str, list[float]]
) -> FloodModel:
    """Refit on all development years and attach the OOF calibrator and budget thresholds,
    exactly as ``training.train`` does for the selected candidate."""
    model = fit_static_candidate(name, dev, MODEL_FEATURE_COLUMNS, zone_static)
    model.calibrator = result.calibrator
    model.thresholds = choose_thresholds(result.curve)
    model.provenance.update(
        {
            "selected_by": (
                "best static-feature candidate by mean out-of-fold event hit rate at "
                f"{list(REFERENCE_BUDGETS)} false episodes per zone-year (tie: PR-AUC)"
            ),
            "validation_years": [f["validation_year"] for f in result.folds],
            "events_in_development": len(dev.windows),
            "label_horizon_minutes": settings.prediction_lead_time_minutes,
            "calibration": result.calibrator.method,
            "git_sha": git_sha(),
            "status": "candidate (not champion); test years not scored",
        }
    )
    if isinstance(model.estimator, RuleStaticAdjustment):
        model.provenance["coefficients"] = model.estimator.summary()
    return model


def run_selection(
    data: LabelledData,
    out_dir: Path | None = None,
    run_date: str | None = None,
    candidates: list[str] | None = None,
    save: bool = True,
) -> dict[str, Any]:
    out_dir = out_dir or candidates_dir()
    run_date = run_date or date.today().isoformat()
    dev = data.development()  # 2017..last_training_year only; test years never reach this
    zone_static = load_zone_static()
    core = list(candidate_factories(MODEL_FEATURE_COLUMNS))
    names = candidates or core + STATIC_CANDIDATES

    fold_coefficients: dict[str, list[dict[str, Any]]] = {}

    def fit_static(name: str, train: LabelledData, cols: list[str]) -> FloodModel:
        model = fit_static_candidate(name, train, cols, zone_static)
        if isinstance(model.estimator, RuleStaticAdjustment):
            summary = model.estimator.summary()
            summary["train_to"] = model.provenance["trained_to"]
            fold_coefficients.setdefault(name, []).append(summary)
        return model

    results: dict[str, CandidateResult] = {}
    for name in names:
        logger.info("Cross-validating %s", name)
        fit = fit_static if name in STATIC_CANDIDATES else None
        results[name] = cross_validate(name, dev, MODEL_FEATURE_COLUMNS, fit=fit)
        logger.info("%s: event score %.3f", name, results[name].event_score)

    summaries = {n: summarise(r, dev) for n, r in results.items()}
    for name, coefs in fold_coefficients.items():
        summaries[name]["fold_coefficients"] = coefs

    def key(n: str) -> tuple[float, float]:
        return (results[n].event_score, float(np.nan_to_num(results[n].row["pr_auc"], nan=-1.0)))

    selected_overall = max(results, key=key)
    static_names = [n for n in results if n in STATIC_CANDIDATES and n != "rule_rain60_nostatic"]
    best_static = max(static_names, key=key) if static_names else None

    any_result = next(iter(results.values()))
    windows = validation_windows(dev, any_result)
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "data": {
            "development_rows": len(dev.features),
            "development_from": dev.features["timestamp"].min(),
            "development_to": dev.features["timestamp"].max(),
            "events_in_development": len(dev.windows),
            "validation_years": [f["validation_year"] for f in any_result.folds],
            "validation_events": len(windows),
            "labels": "verified rows only (flood_events.load_flood_events default)",
            "test_years_scored": False,
        },
        "budgets": {
            "high": settings.false_alarm_budget_high,
            "moderate": settings.false_alarm_budget_moderate,
        },
        "selection_rule": (
            f"mean out-of-fold event hit rate at {list(REFERENCE_BUDGETS)} false episodes per "
            "zone-year (tie: PR-AUC)"
        ),
        "selected_overall": selected_overall,
        "best_static_candidate": best_static,
        "candidates": summaries,
        "ablation": ablation(summaries),
    }
    if save:
        if best_static is not None:
            model = refit_candidate(best_static, dev, results[best_static], zone_static)
            path = model.save(out_dir / f"{best_static}.joblib")
            root = settings.root_dir
            payload["saved_artifact"] = str(
                path.relative_to(root) if path.is_relative_to(root) else path
            )
            payload["saved_artifact_coefficients"] = model.provenance.get("coefficients")
        _write_json(out_dir / f"selection_{run_date}.json", payload)
    return payload


def _print(payload: dict[str, Any]) -> None:
    rows = []
    for name, s in payload["candidates"].items():
        row: dict[str, Any] = {"candidate": name, "event_score": round(s["event_score"], 3)}
        for tier in ("high", "moderate"):
            t = s["tiers"].get(tier)
            if t is None:
                continue
            lo, hi = t["hit_rate_ci90"]
            row[tier] = (
                f"{int(t['hits'])}/{int(t['events'])} ({lo:.0%}-{hi:.0%}) "
                f"fa {t['false_episodes_per_zone_year']:.2f}"
            )
        rows.append(row)
    print(pd.DataFrame(rows).to_string(index=False))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-validated candidate selection report.")
    parser.add_argument("--date", default=None, help="date in the output file name (YYYY-MM-DD)")
    parser.add_argument("--candidates", nargs="*", default=None)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        data = load_training_data()
    except DataUnavailableError as exc:
        logger.error("%s", exc)
        return 1
    payload = run_selection(
        data, run_date=args.date, candidates=args.candidates, save=not args.no_save
    )
    _print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
