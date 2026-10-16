"""
FloodSense - Refit the champion rule on the currently verified development labels (B4.2 re-run).

    python -m floodsense.models.refit_champion --date 2026-10-17

Uses the existing training path (``training.train`` with one candidate: cross-validate on the
development years, fit the Platt calibrator on out-of-fold scores, pick Moderate/High thresholds
with the unchanged false-alarm budgets, refit on all development years). Writes only to
``models/candidates/``:

    rule_rain60_refit_<date>.joblib   the refitted artifact (gitignored)
    rule_rain60_refit_<date>.json     thresholds, millimetre equivalents, training counts, git_sha

It never touches ``models/flood_model.joblib``, ``model_card.json`` or ``final_report.json`` and
never scores the test years (``train`` only sees ``LabelledData.development()``).
"""

import argparse
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import numpy as np

from floodsense.common.config import settings
from floodsense.features.build_features import DataUnavailableError
from floodsense.models.artifact import FloodModel, git_sha
from floodsense.models.prior_sensitivity import git_dirty
from floodsense.models.select_candidates import candidates_dir
from floodsense.models.train import _write_json, load_training_data
from floodsense.models.training import LabelledData, train

logger = logging.getLogger("FloodSense.RefitChampion")

CANDIDATE = "rule_rain60"


def mm_equivalents(model: FloodModel) -> dict[str, float | None]:
    """60-minute rain (mm) at which the calibrated probability first reaches each threshold.

    Valid for the rule only: its score is a monotone function of ``rain_60m`` alone."""
    est = model.estimator
    grid = np.arange(0.0, 200.0, 0.05)
    raw = grid / (grid + est.scale_mm)
    assert model.calibrator is not None
    prob = model.calibrator(raw)
    out: dict[str, float | None] = {}
    for tier, thr in (model.thresholds or {}).items():
        hit = np.flatnonzero(prob >= thr)
        out[tier] = float(grid[hit[0]]) if hit.size else None
    return out


def refit(data: LabelledData, run_date: str) -> dict[str, Any]:
    dev = data.development()
    run = train(data, [CANDIDATE])
    model = run.model
    model.provenance["status"] = "refit of the champion rule; not registered; test years not scored"
    path = model.save(candidates_dir() / f"{CANDIDATE}_refit_{run_date}.joblib")
    result = run.results[CANDIDATE]
    payload: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "git_sha": git_sha(),
        "dirty": git_dirty(),
        "candidate": CANDIDATE,
        "artifact": str(path.relative_to(settings.root_dir)),
        "thresholds": model.thresholds,
        "thresholds_percent": {k: round(v * 100, 3) for k, v in (model.thresholds or {}).items()},
        "rain_60m_mm_equivalent": mm_equivalents(model),
        "calibration_method": result.calibrator.method,
        "budgets": {
            "high": settings.false_alarm_budget_high,
            "moderate": settings.false_alarm_budget_moderate,
        },
        "training": {
            "rows": model.provenance["trained_rows"],
            "from": model.provenance["trained_from"],
            "to": model.provenance["trained_to"],
            "verified_events_2017_to_2023": len(dev.windows),
            "validation_years": model.provenance["validation_years"],
            "oof_event_score": result.event_score,
        },
        "test_years_scored": False,
        "status": "not registered; compare with models/model_card.json before any promotion",
    }
    _write_json(candidates_dir() / f"{CANDIDATE}_refit_{run_date}.json", payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refit rule_rain60 on verified 2017-2023 labels.")
    parser.add_argument("--date", default=None, help="date in the output file names")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        data = load_training_data()
    except DataUnavailableError as exc:
        logger.error("%s", exc)
        return 1
    payload = refit(data, args.date or date.today().isoformat())
    print({k: payload[k] for k in ("git_sha", "dirty", "thresholds", "rain_60m_mm_equivalent")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
