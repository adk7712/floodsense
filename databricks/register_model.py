"""
FloodSense - Log the trained model to MLflow and register it in Unity Catalog.

Wraps a ``FloodModel`` in an ``mlflow.pyfunc`` model that scores through
``floodsense.models.scoring.score_zone_features`` (the same path the app and pipeline use),
registers it as a new version of ``workspace.floodsense.flood_model`` and points an alias at it:
``candidate`` by default. Only the team promotes a version to ``champion`` (moving that alias), after
sign-off.

    # a candidate, e.g. a refit (its summary JSON is logged with it)
    DATABRICKS_CONFIG_PROFILE=akul .venv/bin/python databricks/register_model.py \
        --model-path models/candidates/rule_rain60_refit_<date>.joblib \
        --summary models/candidates/rule_rain60_refit_<date>.json
Version 1 (1 Oct, Moderate 0.30%) was registered with its test-set report, models/final_report.json;
its file is now models/archive/flood_model_v1_2026-10-01.joblib. Version 3, the 88-label refit, is
the champion and the committed models/flood_model.joblib (DEPLOYMENT.md section 21).
"""

import argparse
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

import joblib
import mlflow
import pandas as pd
from mlflow.models import infer_signature
from mlflow.pyfunc import PythonModel
from mlflow.tracking import MlflowClient

from floodsense.common.config import settings
from floodsense.data.replay import load_replay
from floodsense.features.zone_features import compute_zone_feature_table
from floodsense.models.artifact import FloodModel, git_sha
from floodsense.models.scoring import score_zone_features
from floodsense.serving.pipeline_core import _snapshots, payloads_to_readings, snapshot_to_payload

log = logging.getLogger("floodsense.register_model")

# Under the signed-in user's workspace folder (the name "floodsense" is taken by that folder).
EXPERIMENT_NAME = "floodsense-model"


def default_experiment() -> str:
    from databricks.sdk import WorkspaceClient

    return f"/Users/{WorkspaceClient().current_user.me().user_name}/{EXPERIMENT_NAME}"


MODEL_NAME = "workspace.floodsense.flood_model"
ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "flood_model.joblib"
GATE_COLUMNS = ["ura_planning_area", "rain_30m", "rain_120m"]


class FloodSenseModel(PythonModel):
    """Zone feature rows in; ``flood_probability`` (unrounded) and ``risk_tier`` out."""

    def load_context(self, context: Any) -> None:
        self.model = joblib.load(context.artifacts["flood_model"])

    def predict(self, context: Any, model_input: pd.DataFrame, params: Any = None) -> pd.DataFrame:
        scored = score_zone_features(model_input, self.model)
        return scored[["flood_probability", "risk_tier"]].reset_index(drop=True)


def replay_feature_rows(model: FloodModel, replay_path: Path | None = None) -> pd.DataFrame:
    """Model-input feature rows for a replay, built by the pipeline's own feature code."""
    replay = load_replay(replay_path or settings.replay_file)
    readings, stations = payloads_to_readings(snapshot_to_payload(s) for s in replay.snapshots)
    snaps = _snapshots(readings, stations)
    table = compute_zone_feature_table(snaps, snaps[0].stations)
    stamps = pd.to_datetime(table["timestamp"])
    table = table[(stamps >= replay.display_start) & (stamps <= replay.display_end)]
    cols = list(
        dict.fromkeys(
            GATE_COLUMNS + [c for c in model.feature_columns if c != "storm_rarity_score"]
        )
    )
    rows = table[cols].reset_index(drop=True)
    return rows.astype({c: float for c in cols if c != "ura_planning_area"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment", help="MLflow experiment path (default: /Users/<you>/floodsense-model)"
    )
    parser.add_argument("--name", default=MODEL_NAME)
    parser.add_argument(
        "--model-path",
        type=Path,
        default=MODEL_PATH,
        help="FloodModel joblib to register (default: the committed models/flood_model.joblib)",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        required=True,
        help="JSON describing the model, logged with it (e.g. "
        "models/candidates/rule_rain60_refit_<date>.json)",
    )
    # A new version is a candidate until the team promotes it (CLAUDE.md, rule 4).
    parser.add_argument("--alias", default="candidate")
    parser.add_argument("--no-register", action="store_true", help="log the model only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(args.experiment or default_experiment())

    model_path = args.model_path.resolve()
    model: FloodModel = joblib.load(model_path)
    # Every version is logged with its own summary (cross-validation on 2017-2023). The test-set
    # report (models/final_report.json) belongs to version 1 alone and is never attached to another.
    rows = replay_feature_rows(model)
    example = rows.sample(min(len(rows), 200), random_state=0).reset_index(drop=True)
    signature = infer_signature(
        example, score_zone_features(example, model)[["flood_probability", "risk_tier"]]
    )
    log.info("replay feature rows: %d (example %d)", len(rows), len(example))

    with mlflow.start_run(run_name="flood_model") as run:
        mlflow.log_params(
            {
                "candidate": model.provenance.get("candidate"),
                "threshold_moderate": model.thresholds["moderate"],
                "threshold_high": model.thresholds["high"],
                "git_sha": git_sha(),
                "calibration": model.provenance.get("calibration"),
                "label_horizon_minutes": model.provenance.get("label_horizon_minutes"),
            }
        )
        summary = json.loads(args.summary.read_text())
        mlflow.set_tag("test_years_scored", str(summary.get("test_years_scored", False)))
        mlflow.log_param("summary_git_sha", summary.get("git_sha"))
        oof = (summary.get("training") or {}).get("oof_event_score")
        if oof is not None:
            mlflow.log_metric("cv_oof_event_score", oof)
        mlflow.log_artifact(str(args.summary))
        # Always logged as artifacts/flood_model.joblib, whatever the local file is called: the
        # pipeline's loader (models.artifact.load_registered_model) reads that name.
        staged = Path(tempfile.mkdtemp()) / "flood_model.joblib"
        shutil.copyfile(model_path, staged)
        info = mlflow.pyfunc.log_model(
            name="model",
            python_model=FloodSenseModel(),
            artifacts={"flood_model": str(staged)},
            code_paths=[str(ROOT / "src" / "floodsense")],
            pip_requirements=[
                "scikit-learn==1.9.1",
                "pandas",
                "numpy",
                "joblib",
                "pydantic",
                "pydantic-settings",
            ],
            signature=signature,
            input_example=example.head(5),
            registered_model_name=None if args.no_register else args.name,
        )
        log.info("run %s  model_uri %s", run.info.run_id, info.model_uri)

    if not args.no_register:
        client = MlflowClient()
        version = info.registered_model_version
        client.set_registered_model_alias(args.name, args.alias, version)
        log.info("registered %s v%s alias %s", args.name, version, args.alias)


if __name__ == "__main__":
    main()
