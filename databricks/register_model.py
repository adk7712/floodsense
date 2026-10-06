"""
FloodSense - Log the trained model to MLflow and register it in Unity Catalog.

Wraps the committed ``FloodModel`` in an ``mlflow.pyfunc`` model that scores through
``floodsense.models.scoring.score_zone_features`` (the same path the app and pipeline use),
registers it as ``workspace.floodsense.flood_model`` and points the ``champion`` alias at it.

    DATABRICKS_CONFIG_PROFILE=akul .venv/bin/python databricks/register_model.py
"""

import argparse
import json
import logging
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

EXPERIMENT = "/Users/akul.sharma009@gmail.com/floodsense-model"
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
    parser.add_argument("--experiment", default=EXPERIMENT)
    parser.add_argument("--name", default=MODEL_NAME)
    parser.add_argument("--alias", default="champion")
    parser.add_argument("--no-register", action="store_true", help="log the model only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(args.experiment)

    model: FloodModel = joblib.load(MODEL_PATH)
    report = json.loads((ROOT / "models" / "final_report.json").read_text())
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
        for tier in ("high", "moderate"):
            ev = report[f"events_{tier}"]
            for key in ("hit_rate", "median_lead_minutes", "false_episodes_per_zone_year"):
                mlflow.log_metric(f"test_{tier}_{key}", ev[key])
        for name in ("model_card.json", "final_report.json"):
            mlflow.log_artifact(str(ROOT / "models" / name))
        info = mlflow.pyfunc.log_model(
            name="model",
            python_model=FloodSenseModel(),
            artifacts={"flood_model": str(MODEL_PATH)},
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
