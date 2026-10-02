"""
Phase 5 parity: the Databricks pipeline reproduces local scoring on the 17 Apr 2021 replay.

Reads the export of a real workspace run (DEPLOYMENT.md, sections 2 and 4); skips without it:

    data/reference/phase5/databricks_replay_predictions.csv   gold rows, replay window only
    data/reference/phase5/run_info.json                       pipeline + update ids, row counts

The CSV alone could be produced locally, so it is not the proof on its own: a person checked the
run_info update id and row counts in the workspace (the sign-off in DEPLOYMENT.md). This test makes
sure that what ran there is the same computation as here.
"""

import json

import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.serving import pipeline_core as core

EXPORT_DIR = settings.root_dir / "data" / "reference" / "phase5"
EXPORT = EXPORT_DIR / "databricks_replay_predictions.csv"
RUN_INFO = EXPORT_DIR / "run_info.json"
REQUIRED_RUN_INFO = {
    "workspace_host",
    "pipeline_id",
    "update_id",
    "update_started_at",
    "bronze_rows",
    "silver_rows",
    "gold_rows",
    "landing_files",
}

pytestmark = pytest.mark.skipif(
    not EXPORT.exists(), reason="Phase 5 pending: no Databricks export committed yet"
)


@pytest.fixture(scope="module")
def exported() -> pd.DataFrame:
    df = pd.read_csv(EXPORT)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Singapore")
    return df.sort_values(["timestamp", "ura_planning_area"]).reset_index(drop=True)


@pytest.fixture(scope="module")
def expected() -> pd.DataFrame:
    return core.replay_predictions()


def test_run_info_identifies_a_real_pipeline_update():
    info = json.loads(RUN_INFO.read_text())
    missing = REQUIRED_RUN_INFO - set(info)
    assert not missing, f"run_info.json lacks {sorted(missing)}"
    assert str(info["workspace_host"]).startswith("https://")
    assert info["landing_files"] == 949, "the whole replay (warm-up included) must be landed"
    assert info["gold_rows"] >= 55 * 85


def test_export_has_the_gold_schema(exported):
    assert list(exported.columns) == core.PREDICTION_COLUMNS


def test_same_rows(exported, expected):
    key = ["ura_planning_area", "timestamp"]
    assert len(exported) == len(expected)
    assert exported[key].equals(expected[key])


# Rows whose 120-minute rain sits on the active-rain gate (0.2 mm, one gauge tip). Floating-point
# sums of the same readings land a hair either side of it depending on the library build, so the
# gate (``>=``) can zero the probability on one platform and not the other. Known issue, to be
# fixed after submission by giving the gate a tolerance in both training and scoring (that changes
# the training rows, so it needs a retrain): see DEPLOYMENT.md, "Known issues".
GATE_EPS = 1e-9


def _on_gate(df: pd.DataFrame) -> np.ndarray:
    return np.abs(df["rain_120m"].to_numpy(float) - settings.active_rain_min_mm_120m) < GATE_EPS


def test_same_features(exported, expected):
    for col in core.PREDICTION_COLUMNS[2:-2]:
        np.testing.assert_allclose(
            exported[col].to_numpy(float),
            expected[col].to_numpy(float),
            rtol=1e-6,
            atol=1e-9,
            err_msg=col,
        )


def test_same_probabilities_off_the_gate(exported, expected):
    """Probabilities match everywhere except rows exactly on the gate, which must still score
    far below the Moderate threshold on both sides (tiers are checked for every row below)."""
    gate = _on_gate(expected)
    got, want = (
        exported["flood_probability"].to_numpy(float),
        expected["flood_probability"].to_numpy(float),
    )
    np.testing.assert_allclose(got[~gate], want[~gate], rtol=1e-6, atol=1e-9)
    assert gate.sum() < 0.01 * len(gate)
    threshold = 0.003  # Moderate (models/final_report.json)
    assert (got[gate] < threshold / 100).all() and (want[gate] < threshold / 100).all()


def test_same_tiers(exported, expected):
    diff = exported["risk_tier"] != expected["risk_tier"]
    assert not diff.any(), exported.loc[
        diff, ["ura_planning_area", "timestamp", "risk_tier"]
    ].head()


def test_bukit_timah_goes_high_at_1245(exported):
    bt = exported[exported["ura_planning_area"] == "BUKIT TIMAH"]
    assert bt.loc[bt["risk_tier"] == "High", "timestamp"].min() == pd.Timestamp(
        "2021-04-17 12:45", tz="Asia/Singapore"
    )
