"""
FloodSense - Training CLI (I/O and experiment tracking around ``floodsense.models.training``).

    python -m floodsense.models.train                       # train, calibrate, save, log
    python -m floodsense.models.train --final-report        # score the test years, once
    python -m floodsense.models.train --final-report --force

Training reads the feature store (``floodsense.features.build_features``) and the sourced flood
events; there is no synthetic fallback. It prints the selected candidate's hit-rate / false-alarm
trade-off table, which is how the false-alarm budgets (``settings.false_alarm_budget_*``) are
chosen. Thresholds are only selected once both budgets are set.
"""

import argparse
import dataclasses
import json
import logging
import math
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from floodsense.common.config import settings
from floodsense.data import flood_events
from floodsense.features.build_features import DATA_DOC, DataUnavailableError, load_feature_store
from floodsense.labels.policy import event_window, label_rows
from floodsense.models.artifact import FloodModel, git_sha
from floodsense.models.evaluation import select_threshold
from floodsense.models.training import (
    REFERENCE_BUDGETS,
    CandidateResult,
    LabelledData,
    TrainingRun,
    final_report,
    train,
)

logger = logging.getLogger("FloodSense.Trainer")

EXPERIMENT = "floodsense"
RAW_COUNTS = {"tp", "fp", "fn", "tn", "positives", "rows"}
TRADEOFF_COLUMNS = [
    "threshold",
    "hit_rate",
    "hits",
    "events",
    "false_episodes_per_zone_year",
    "episode_false_alarm_ratio",
    "median_lead_minutes",
    "false_alert_hours",
]


def model_card_path() -> Path:
    return settings.models_dir / "model_card.json"


def final_report_path() -> Path:
    return settings.models_dir / "final_report.json"


# --------------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------------


def load_training_data() -> LabelledData:
    """Feature store + sourced flood events -> labelled data. Fails loudly if either is missing."""
    features = load_feature_store()  # raises DataUnavailableError with build instructions
    try:
        events = flood_events.load_flood_events()
    except (NotImplementedError, FileNotFoundError) as exc:
        raise DataUnavailableError(
            f"Flood events are not available ({exc}). See {DATA_DOC}."
        ) from exc
    if not events:
        raise DataUnavailableError(f"No flood events loaded. See {DATA_DOC}.")

    labels, report = label_rows(features, events)
    logger.info(
        "Labelled %d rows from %d events (horizon %s): %s",
        len(features),
        len(report.events_used),
        report.horizon,
        report.counts,
    )
    if report.events_without_rows:
        logger.warning(
            "%d events have no feature rows (zone or period missing) and are unused: %s",
            len(report.events_without_rows),
            report.events_without_rows,
        )
    windows = [event_window(e) for e in events]
    return LabelledData(features, labels["label_prob"], windows)


# --------------------------------------------------------------------------------------------
# Serialisation helpers
# --------------------------------------------------------------------------------------------


def _jsonable(obj: Any) -> Any:
    """Recursively convert to strict JSON: Timestamps -> ISO, dataclasses -> dicts, NaN -> null."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return _jsonable(obj.to_dict(orient="records"))
    if obj is pd.NaT:
        return None
    if isinstance(obj, pd.Timestamp | datetime):
        return obj.isoformat()
    if hasattr(obj, "item") and not isinstance(obj, str | bytes):  # numpy scalar
        return _jsonable(obj.item())
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if obj is None or isinstance(obj, str | int | bool):
        return obj
    return str(obj)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2, allow_nan=False) + "\n")


def _model_card(run: TrainingRun) -> dict[str, Any]:
    model = run.model
    return {
        "selected_candidate": run.selected,
        "generated_at": datetime.now(UTC).isoformat(),
        "feature_columns": model.feature_columns,
        "thresholds": model.thresholds,
        "calibration_method": run.results[run.selected].calibrator.method,
        "provenance": model.provenance,
        "notes": run.notes,
        "candidates": {
            name: {
                "event_score": result.event_score,
                "row_metrics_oof": result.row,
                "ece": result.ece,
                "calibration_method": result.calibrator.method,
                "oof_rows": len(result.oof),
                "folds": result.folds,
            }
            for name, result in run.results.items()
        },
        "tradeoff_selected": run.results[run.selected].curve,
    }


# --------------------------------------------------------------------------------------------
# Plots
# --------------------------------------------------------------------------------------------


def _reliability_figure(result: CandidateResult) -> Any:
    import plotly.graph_objects as go

    t = result.reliability[result.reliability["weight"] > 0]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=[0, 1], y=[0, 1], mode="lines", name="perfect", line={"dash": "dash"})
    )
    fig.add_trace(
        go.Scatter(
            x=t["mean_predicted"],
            y=t["observed_rate"],
            mode="lines+markers",
            name=result.name,
            customdata=t["weight"],
            hovertemplate="predicted %{x:.3f}<br>observed %{y:.3f}<br>weight %{customdata:.1f}",
        )
    )
    fig.update_layout(
        title=f"Reliability (out-of-fold, cross-fitted): {result.name}, ECE {result.ece:.3f}",
        xaxis_title="Predicted probability",
        yaxis_title="Observed frequency",
        template="plotly_white",
    )
    return fig


def _tradeoff_figure(result: CandidateResult) -> Any:
    import plotly.graph_objects as go

    c = result.curve
    fig = go.Figure(
        go.Scatter(
            x=c["false_episodes_per_zone_year"],
            y=c["hit_rate"],
            mode="lines+markers",
            name=result.name,
            customdata=c["threshold"],
            hovertemplate=(
                "threshold %{customdata:.2f}<br>false episodes / zone-year %{x:.3f}"
                "<br>hit rate %{y:.3f}"
            ),
        )
    )
    fig.update_layout(
        title=f"Hit rate vs false alarms: {result.name}",
        xaxis_title="False alert episodes per zone-year",
        yaxis_title="Event hit rate",
        template="plotly_white",
    )
    return fig


def _print_tradeoff(result: CandidateResult) -> None:
    curve = result.curve[TRADEOFF_COLUMNS].copy()
    curve["threshold"] = curve["threshold"].map(lambda v: f"{v:.3g}")
    table = curve.to_string(index=False, float_format=lambda v: f"{v:.3f}")
    print(f"\nTrade-off table for the selected candidate ({result.name}):")
    print("Choose settings.false_alarm_budget_high / _moderate from the false-episode column.\n")
    print(table)
    print()


def _print_candidates(results: dict[str, CandidateResult]) -> None:
    """Floods caught by each candidate at matched false-alarm levels (out-of-fold)."""
    rows = []
    for name, r in results.items():
        row = {"candidate": name, "event_score": round(r.event_score, 3)}
        for budget in REFERENCE_BUDGETS:
            best = select_threshold(r.curve, budget)
            row[f"hits@{budget:g}/zone-yr"] = (
                "-" if best is None else f"{int(best['hits'])}/{int(best['events'])}"
            )
        rows.append(row)
    print("\nCandidates: floods caught at matched false-alarm levels (validation years, OOF)\n")
    print(pd.DataFrame(rows).to_string(index=False))


# --------------------------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------------------------


def _finite_metrics(metrics: dict[str, float]) -> dict[str, float]:
    return {k: float(v) for k, v in metrics.items() if v is not None and math.isfinite(v)}


def _select_experiment(mlflow: Any, tracking_uri: str | None) -> None:
    """Point MLflow at ``tracking_uri`` (default: a SQLite file in the repo root; the file-store
    backend is deprecated) and select the experiment. For SQLite stores the artifacts go in a
    ``mlruns/`` directory next to the database; both are gitignored."""
    uri = tracking_uri or f"sqlite:///{settings.root_dir / 'mlflow.db'}"
    mlflow.set_tracking_uri(uri)
    if uri.startswith("sqlite:///") and ":memory:" not in uri:
        client = mlflow.MlflowClient()
        if client.get_experiment_by_name(EXPERIMENT) is None:
            artifacts = Path(uri.removeprefix("sqlite:///")).parent / "mlruns"
            client.create_experiment(EXPERIMENT, artifact_location=artifacts.as_uri())
    mlflow.set_experiment(EXPERIMENT)


def run_training(data: LabelledData, tracking_uri: str | None = None) -> TrainingRun:
    """Train, save the model and model card, log to MLflow, print the trade-off table."""
    import mlflow  # optional dependency: `uv sync --extra train`

    run = train(data)
    model_path = run.model.save()
    card = _model_card(run)
    _write_json(model_card_path(), card)
    logger.info("Saved %s (selected: %s) and %s", model_path, run.selected, model_card_path())

    _select_experiment(mlflow, tracking_uri)
    with mlflow.start_run(run_name=f"train-{run.selected}"):
        mlflow.log_params(
            {
                "selected": run.selected,
                "calibration": run.results[run.selected].calibrator.method,
                "git_sha": git_sha() or "unknown",
                "last_training_year": settings.last_training_year,
                "false_alarm_budgets_set": run.model.thresholds is not None,
            }
        )
        mlflow.log_artifact(str(model_card_path()))
        for name, result in run.results.items():
            with mlflow.start_run(run_name=name, nested=True), tempfile.TemporaryDirectory() as tmp:
                mlflow.log_params(
                    {
                        "candidate": name,
                        "feature_columns": ",".join(run.model.feature_columns),
                        "validation_years": ",".join(
                            str(f["validation_year"]) for f in result.folds
                        ),
                    }
                )
                row = {k: v for k, v in result.row.items() if k not in RAW_COUNTS}
                mlflow.log_metrics(_finite_metrics({**row, "ece": result.ece}))
                out = Path(tmp)
                _reliability_figure(result).write_html(out / "reliability.html")
                result.curve.to_csv(out / "tradeoff_curve.csv", index=False)
                _tradeoff_figure(result).write_html(out / "tradeoff_curve.html")
                for artifact in sorted(out.iterdir()):
                    mlflow.log_artifact(str(artifact))

    _print_candidates(run.results)
    _print_tradeoff(run.results[run.selected])
    for note in run.notes:
        logger.warning(note)
    return run


# --------------------------------------------------------------------------------------------
# Final report
# --------------------------------------------------------------------------------------------


def run_final_report(
    data: LabelledData,
    model_path: Path | None = None,
    force: bool = False,
    tracking_uri: str | None = None,
) -> dict[str, Any]:
    """Score the test years with the saved model. Refuses to run twice unless ``force``."""
    out_path = final_report_path()
    if out_path.exists() and not force:
        raise FileExistsError(
            f"{out_path} already exists. The test years are meant to be scored once; "
            "pass force=True (--force) to overwrite it deliberately."
        )
    model_path = model_path or settings.flood_model_path
    if not model_path.exists():
        raise FileNotFoundError(f"No trained model at {model_path}; run training first")
    model = joblib.load(model_path)
    if not isinstance(model, FloodModel):
        raise TypeError(f"{model_path} is not a FloodModel")

    report = final_report(model, data)
    payload = _jsonable(
        {**report, "model_provenance": model.provenance, "thresholds": model.thresholds}
    )
    _write_json(out_path, payload)
    logger.info("Final report written to %s", out_path)

    import mlflow

    _select_experiment(mlflow, tracking_uri)
    with mlflow.start_run(run_name="final-report"):
        mlflow.log_params(
            {
                "model_path": str(model_path),
                "selected": model.provenance.get("candidate", "unknown"),
                "test_years": ",".join(str(y) for y in report["test_years"]),
                "git_sha": git_sha() or "unknown",
            }
        )
        row = {k: v for k, v in report["row"].items() if k not in RAW_COUNTS}
        lo, hi = report["hit_rate_ci90"]
        mlflow.log_metrics(
            _finite_metrics(
                {
                    **{f"row_{k}": v for k, v in row.items()},
                    **{f"events_{k}": v for k, v in report["events_high"].items()},
                    "hit_rate_ci90_lo": lo,
                    "hit_rate_ci90_hi": hi,
                    **{
                        f"ci90_{tier}_{rate}_{end}": v
                        for tier, rates in report["ci90"].items()
                        for rate, pair in rates.items()
                        for end, v in zip(("lo", "hi"), pair, strict=True)
                    },
                }
            )
        )
        mlflow.log_artifact(str(out_path))
    return payload  # type: ignore[no-any-return]


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the FloodSense model on real data.")
    parser.add_argument(
        "--final-report", action="store_true", help="score the test years with the saved model"
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing final report")
    parser.add_argument("--tracking-uri", default=None, help="MLflow tracking URI")
    args = parser.parse_args(argv)
    if args.force and not args.final_report:
        parser.error("--force only applies with --final-report")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        data = load_training_data()
        if args.final_report:
            report = run_final_report(data, force=args.force, tracking_uri=args.tracking_uri)
            print(
                json.dumps(
                    {k: report[k] for k in ("test_years", "events_high", "hit_rate_ci90")}, indent=2
                )
            )
        else:
            run_training(data, tracking_uri=args.tracking_uri)
    except (DataUnavailableError, FileExistsError, FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
