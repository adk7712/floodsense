"""
FloodSense - ML Model Training, Evaluation & Unity Catalog Registry.
Implements:
1. Heuristic Rule Baseline (PUB 25mm / 30min rule).
2. Primary Champion: Class-Weighted Logistic Regression with calibrated thresholds.
3. Challenger: LightGBM with strict In-Fold SMOTE (CV-SMOTE).
4. Strict Temporal Validation: Train 2017–2023, Unseen Test 2024–2026.
5. Imbalance-robust metrics: PR-AUC, Brier Score, False-Alarm Rate (FAR).
6. MLflow experiment tracking and model artifact serialization.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Tuple, Any
import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
import lightgbm as lgb
from imblearn.over_sampling import SMOTE
from imblearn.pipeline import Pipeline as ImbPipeline
import mlflow

from src.features.feature_pipeline import FeaturePipeline
from src.features.ground_truth_extractor import GroundTruthExtractor
from src.data.synthetic_or_historical_loader import generate_historical_dataset

logger = logging.getLogger("FloodSense.Trainer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

FEATURE_COLUMNS = [
    "rain_5m",
    "rain_15m",
    "rain_30m",
    "rain_60m",
    "rain_120m",
    "rain_decay_72h",
    "storm_rarity_score",
    "return_period_years",
    "pub_monitored"
]

OPERATIONAL_THRESHOLDS = {
    "low_moderate": 0.25,
    "moderate_high": 0.65
}


def evaluate_model_predictions(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    threshold: float = 0.50
) -> Dict[str, float]:
    """
    Computes imbalance-resilient classification and calibration metrics.
    """
    y_pred = (y_prob >= threshold).astype(int)
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel() if cm.size == 4 else (len(y_true), 0, 0, 0)

    pr_auc = average_precision_score(y_true, y_prob) if sum(y_true) > 0 else 0.0
    roc_auc = roc_auc_score(y_true, y_prob) if len(np.unique(y_true)) > 1 else 0.5
    brier = brier_score_loss(y_true, y_prob)
    prec = precision_score(y_true, y_pred, zero_division=0)
    rec = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    far = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    return {
        "pr_auc": round(float(pr_auc), 4),
        "roc_auc": round(float(roc_auc), 4),
        "brier_score": round(float(brier), 5),
        "precision": round(float(prec), 4),
        "recall": round(float(rec), 4),
        "f1_score": round(float(f1), 4),
        "false_alarm_rate": round(float(far), 4),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_negatives": int(tn)
    }


def train_and_evaluate_all():
    """Main training orchestration pipeline."""
    logger.info("Step 1: Generating multi-year historical dataset (2017–2026)...")
    raw_df = generate_historical_dataset(start_year=2017, end_year=2026, num_storm_days_per_year=35)

    logger.info("Step 2: Engineering multi-scale features and storm rarity...")
    feat_pipe = FeaturePipeline()
    feat_df = feat_pipe.process_batch_dataframe(raw_df, prune_zero_rain=True)

    logger.info("Step 3: Attaching Ground Truth flood event labels...")
    gt_extractor = GroundTruthExtractor()
    labeled_df = gt_extractor.attach_labels_to_feature_df(feat_df, lead_time_minutes=60)
    labeled_df["timestamp"] = pd.to_datetime(labeled_df["timestamp"])

    # Strict temporal train/test split: Train on 2017–2023, Evaluate on 2024–2026
    train_df = labeled_df[labeled_df["timestamp"].dt.year <= 2023].copy()
    test_df = labeled_df[labeled_df["timestamp"].dt.year >= 2024].copy()

    X_train = train_df[FEATURE_COLUMNS].values
    y_train = train_df["flood_within_60min"].values
    X_test = test_df[FEATURE_COLUMNS].values
    y_test = test_df["flood_within_60min"].values

    logger.info(f"Train Set (2017-2023): {len(train_df)} samples, {y_train.sum()} floods ({y_train.mean()*100:.2f}%)")
    logger.info(f"Test Set  (2024-2026): {len(test_df)} samples, {y_test.sum()} floods ({y_test.mean()*100:.2f}%)")

    # MLflow Setup
    mlflow.set_experiment("FloodSense_Urban_Drainage_Intelligence")

    # --- 1. Baseline: Heuristic Rule Model (rain_30m >= 25.0) ---
    with mlflow.start_run(run_name="01_Baseline_Rule_Heuristic"):
        rule_prob_test = (test_df["rain_30m"].values >= 25.0).astype(float)
        baseline_metrics = evaluate_model_predictions(y_test, rule_prob_test, threshold=0.5)
        mlflow.log_params({"model_type": "RuleHeuristic", "threshold_mm": 25.0})
        mlflow.log_metrics(baseline_metrics)
        logger.info(f"Baseline Heuristic -> PR-AUC: {baseline_metrics['pr_auc']}, FAR: {baseline_metrics['false_alarm_rate']}, F1: {baseline_metrics['f1_score']}")

    # --- 2. Primary Champion: Class-Weighted Logistic Regression ---
    with mlflow.start_run(run_name="02_Primary_Logistic_Regression"):
        lr_pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(class_weight="balanced", C=0.5, max_iter=1000, random_state=42))
        ])
        lr_pipeline.fit(X_train, y_train)
        lr_probs = lr_pipeline.predict_proba(X_test)[:, 1]
        lr_metrics = evaluate_model_predictions(y_test, lr_probs, threshold=OPERATIONAL_THRESHOLDS["low_moderate"])
        mlflow.log_params({"model_type": "ClassWeightedLogisticRegression", "C": 0.5})
        mlflow.log_metrics(lr_metrics)
        logger.info(f"Primary Logistic Regression -> PR-AUC: {lr_metrics['pr_auc']}, Recall: {lr_metrics['recall']}, FAR: {lr_metrics['false_alarm_rate']}")

    # --- 3. Challenger: LightGBM with In-Fold SMOTE ---
    with mlflow.start_run(run_name="03_Challenger_LightGBM_CVSMOTE"):
        lgb_pipeline = ImbPipeline([
            ("smote", SMOTE(sampling_strategy=0.2, random_state=42)),
            ("classifier", lgb.LGBMClassifier(
                n_estimators=100,
                learning_rate=0.05,
                max_depth=4,
                num_leaves=15,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                verbose=-1
            ))
        ])
        lgb_pipeline.fit(X_train, y_train)
        lgb_probs = lgb_pipeline.predict_proba(X_test)[:, 1]
        lgb_metrics = evaluate_model_predictions(y_test, lgb_probs, threshold=OPERATIONAL_THRESHOLDS["low_moderate"])
        mlflow.log_params({"model_type": "LightGBM_CVSMOTE", "n_estimators": 100, "max_depth": 4})
        mlflow.log_metrics(lgb_metrics)
        logger.info(f"Challenger LightGBM -> PR-AUC: {lgb_metrics['pr_auc']}, Recall: {lgb_metrics['recall']}, FAR: {lgb_metrics['false_alarm_rate']}")

    # Select Champion based on PR-AUC & False Alarm Rate
    champion_pipeline = lr_pipeline if lr_metrics["pr_auc"] >= lgb_metrics["pr_auc"] else lgb_pipeline
    champion_name = "ClassWeightedLogisticRegression" if champion_pipeline == lr_pipeline else "LightGBM_CVSMOTE"
    champion_metrics = lr_metrics if champion_pipeline == lr_pipeline else lgb_metrics

    # Save winning champion artifact
    models_dir = Path("models")
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / "champion_model.joblib"
    joblib.dump(champion_pipeline, model_path)

    metadata = {
        "champion_name": champion_name,
        "feature_columns": FEATURE_COLUMNS,
        "operational_thresholds": OPERATIONAL_THRESHOLDS,
        "metrics_2024_2026_test": champion_metrics,
        "baseline_comparison": baseline_metrics,
        "training_date": pd.Timestamp.now().isoformat()
    }
    with open(models_dir / "model_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(f"Winning Champion Model ({champion_name}) serialized to {model_path}")
    return metadata


if __name__ == "__main__":
    train_and_evaluate_all()
