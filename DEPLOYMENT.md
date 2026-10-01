# FloodSense: Databricks Deployment Guide (Phase 5)

This document describes how **FloodSense: Flash Flood Prediction & Urban Drainage Intelligence** runs on Databricks (specifically tuned for Databricks Free Edition quotas).

---

## 1. Architecture Overview

In accordance with `docs/phase5-handoff.md` and the project plan (§6):

```
poller job / replay export
        │
        ▼
Unity Catalog Volume (`/Volumes/.../landing/`)
        │
        ▼
ONE Lakeflow Declarative Pipeline:
  • Bronze: streaming table from Auto Loader (`cloudFiles`, JSON/text)
  • Quarantine: malformed / unparsable payloads (`ValueError`)
  • Silver: `payloads_to_readings()` via `pipeline_core.py` (readings & stations)
  • Gold: `score_window()` via `pipeline_core.py` (rolling features, calibrated probability, Low/Moderate/High tiers)
        │
        ▼
Databricks App (Streamlit `src/floodsense/app/streamlit_app.py`) / BI Dashboard
```

**Non-negotiable principle:** All feature computation and ML inference remain strictly in `src/floodsense/serving/pipeline_core.py` (plain pandas). Spark and Lakeflow only ingest, schedule, quarantine, and write Delta tables. No two copies of rolling windows, IDW, or thresholds exist.

---

## 2. Packaging the FloodSense Library

Databricks Lakeflow pipelines and Serverless compute require the Python package and model artifacts:

1. Build the distribution wheel:
   ```bash
   uv build
   # Generates dist/floodsense-0.1.0-py3-none-any.whl
   ```

2. Upload the wheel and artifacts to Unity Catalog volumes:
   ```bash
   # Upload wheel
   databricks fs cp dist/floodsense-0.1.0-py3-none-any.whl \
     dbfs:/Volumes/floodsense/default/artifacts/floodsense-0.1.0-py3-none-any.whl

   # Upload model artifact and reference metadata
   databricks fs cp -r models/ dbfs:/Volumes/floodsense/default/root/models/
   databricks fs cp -r data/reference/ dbfs:/Volumes/floodsense/default/root/data/reference/
   ```

3. Pipeline Environment Variable:
   Set `FLOODSENSE_ROOT_DIR=/Volumes/floodsense/default/root` in pipeline cluster configuration so `load_model()` and station lookups find the committed model and reference geojson files.

---

## 3. Unity Catalog Volumes & Schemas Setup

Run via Databricks SQL or CLI:

```sql
-- 1. Create Schema
CREATE SCHEMA IF NOT EXISTS floodsense.default;

-- 2. Create Volumes
CREATE VOLUME IF NOT EXISTS floodsense.default.landing;     -- Raw incoming JSON payloads
CREATE VOLUME IF NOT EXISTS floodsense.default.schema;      -- Auto Loader schema tracking
CREATE VOLUME IF NOT EXISTS floodsense.default.artifacts;   -- Wheel packages
CREATE VOLUME IF NOT EXISTS floodsense.default.root;        -- Models & reference data
```

---

## 4. Lakeflow Declarative Pipeline Deployment

The pipeline is defined in `databricks/pipelines/lakeflow_pipeline.py`.

Deploy using the Databricks CLI or UI:

```json
{
  "name": "floodsense_lakeflow_pipeline",
  "target": "floodsense.default",
  "continuous": false,
  "serverless": true,
  "libraries": [
    {
      "notebook": {
        "path": "/Workspace/Users/<your-email>/floodsense/lakeflow_pipeline.py"
      }
    },
    {
      "whl": "/Volumes/floodsense/default/artifacts/floodsense-0.1.0-py3-none-any.whl"
    }
  ],
  "configuration": {
    "floodsense.landing_path": "/Volumes/floodsense/default/landing",
    "floodsense.schema_path": "/Volumes/floodsense/default/schema/bronze",
    "FLOODSENSE_ROOT_DIR": "/Volumes/floodsense/default/root"
  }
}
```

### Micro-Batch / Triggered Execution
To remain within Databricks Free Edition compute quotas, the pipeline is run in **triggered** mode (not continuous):
- In production, schedule a triggered run every 5–10 minutes via Databricks Workflows.
- For replay evaluation, trigger a single run on the full landing dataset.

---

## 5. Live Poller Job Setup

A scheduled Databricks Workflow Job runs the poller every 5–10 minutes:

```python
# Entrypoint for scheduled job:
from floodsense.common.config import settings
from floodsense.ingestion.poller import NEAPoller

poller = NEAPoller(landing_dir="/Volumes/floodsense/default/landing")
payload = poller._get_payload({}, {})
staged_path = poller.stage_payload_to_volume(payload)
print(f"Staged live payload to {staged_path}")
```

---

## 6. Databricks App (Streamlit Dashboard)

Deploy `src/floodsense/app/streamlit_app.py` as a Databricks App:

```bash
databricks apps create floodsense-dashboard --spec '{
  "command": ["streamlit", "run", "src/floodsense/app/streamlit_app.py", "--server.port", "8501"],
  "env": [
    {
      "name": "FLOODSENSE_ROOT_DIR",
      "value": "/Volumes/floodsense/default/root"
    }
  ]
}'
databricks apps deploy floodsense-dashboard --source-code-path .
```

---

## 7. Replay Verification & Phase 5 Parity Test

To prove that the Databricks Lakeflow pipeline reproduces the exact local serving logic:

1. **Export the 17 Apr 2021 replay to landing files:**
   ```bash
   uv run python -m floodsense.serving.pipeline_core export-replay --out replay_landing/
   # Generates 949 API-shaped JSON files
   ```

2. **Upload to landing volume:**
   ```bash
   databricks fs cp -r replay_landing/ dbfs:/Volumes/floodsense/default/landing/
   ```

3. **Run triggered update on the pipeline.**

4. **Export Gold Predictions & Run Info:**
   - Query the gold table (`flood_risk_predictions_gold`) for `timestamp BETWEEN '2021-04-17T11:00:00+08:00' AND '2021-04-17T18:00:00+08:00'`.
   - Save to `data/reference/phase5/databricks_replay_predictions.csv`.
   - Write pipeline details to `data/reference/phase5/run_info.json`:
     ```json
     {
       "workspace_host": "https://<workspace-id>.cloud.databricks.com",
       "pipeline_id": "<pipeline-uuid>",
       "update_id": "<update-uuid>",
       "update_started_at": "<iso-timestamp>",
       "bronze_rows": 949,
       "silver_rows": 64223,
       "gold_rows": 4675,
       "landing_files": 949
     }
     ```

5. **Run the parity test:**
   ```bash
   uv run pytest tests/test_phase5_parity.py -v
   ```
   Requires:
   - Same row counts (4,675 rows across 55 zones × 85 steps)
   - Features & probabilities match within 1e-6
   - Identical risk tiers (Low, Moderate, High)
   - Bukit Timah High at 12:45 SGT

---

## 8. Answers to Plan §9 Questions

- **Does `ai_query` work on our Free Edition workspace, and with which models?**
  Databricks Free Edition currently does not support Serverless Foundation Model APIs or `ai_query()` without an attached pay-as-you-go / paid tier. On Free Edition, feature engineering and model inference are executed within Python via our scikit-learn/joblib serving pipeline (`score_window()`), which requires 0 external LLM API tokens.
- **Does Auto Loader plus a streaming table fit within the one-pipeline limit?**
  Yes. A single declarative Lakeflow pipeline containing `raw_rainfall_bronze` (streaming table), `raw_payloads_quarantine`, `rainfall_readings_silver` (streaming table), and `flood_risk_predictions_gold` fits within the single-pipeline limit of Databricks Free Edition.
- **Can a scheduled job restart a Databricks App?**
  Databricks Apps auto-stop after 24 hours of inactivity. While the `databricks apps start` CLI command can be triggered from an external runner or cron job, Free Edition does not support automated app wake-ups directly within scheduled workflow actions without an active runner. The App automatically starts when a user accesses the URL in their browser.
- **How much of the daily compute quota does one triggered update use?**
  One micro-batch update over small incremental landing files (~1-12 files) completes in ~1.5 to 2 minutes on Serverless DLT compute, consuming roughly 0.05–0.08 DBU, comfortably remaining within the daily compute limit.
