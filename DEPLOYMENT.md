# FloodSense: Databricks Deployment Guide (DAISI Challenge 2026)

This document provides copy-paste commands to deploy **FloodSense: Flash Flood Prediction & Urban Drainage Intelligence** to a Databricks workspace (fully compliant with Databricks Free Edition quotas).

---

## 1. Prerequisites

- Databricks CLI (`>= 0.213.0`) installed locally:
  ```bash
  curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
  ```
- Active Databricks workspace (Free Edition / Trial / Enterprise).
- Python 3.10+ virtual environment.

---

## 2. Authenticate with Databricks Workspace

Run browser-based OAuth authentication:

```bash
databricks auth login --host https://<your-databricks-instance>.cloud.databricks.com
```

Verify connection:
```bash
databricks current-user me
```

---

## 3. Unity Catalog Schema & Volumes Setup

Create the catalog schema and raw landing volume:

```bash
# 1. Create Schema
databricks sql exec --statement "CREATE SCHEMA IF NOT EXISTS floodsense;"

# 2. Create Unity Catalog Landing Volume for Ingestion
databricks sql exec --statement "CREATE VOLUME IF NOT EXISTS floodsense.raw_landing;"

# 3. Create Unity Catalog Schema Directory for Auto Loader
databricks sql exec --statement "CREATE VOLUME IF NOT EXISTS floodsense.schema;"
```

Upload the pre-computed static IDW weight matrix to Unity Catalog:

```bash
# Upload static weights to Volume
databricks fs cp data/processed/station_zone_weights.parquet dbfs:/Volumes/floodsense/raw_landing/static/station_zone_weights.parquet

# Register as Delta Table
databricks sql exec --statement "
CREATE OR REPLACE TABLE floodsense.station_zone_weights AS
SELECT * FROM read_files('/Volumes/floodsense/raw_landing/static/station_zone_weights.parquet');
"
```

---

## 4. Deploy Unified Lakeflow Declarative Pipeline

Upload the pipeline file and create the Lakeflow pipeline:

```bash
# Upload pipeline definition to Databricks Workspace
databricks workspace import databricks/pipelines/lakeflow_pipeline.py /Workspace/Users/<your-email>/floodsense/lakeflow_pipeline.py --language PYTHON --overwrite

# Create Declarative Pipeline
databricks pipelines create --json '{
  "name": "floodsense_lakeflow_pipeline",
  "target": "floodsense",
  "continuous": false,
  "serverless": true,
  "libraries": [
    {
      "notebook": {
        "path": "/Workspace/Users/<your-email>/floodsense/lakeflow_pipeline.py"
      }
    }
  ],
  "configuration": {
    "pipelines.useServerlessCompute": "true"
  }
}'
```

### Triggering Micro-Batch Pipeline Runs:

To comply with Free Edition daily compute caps, trigger micro-batches on demand or via scheduled cron (e.g. every 5–10 mins during live demo):

```bash
# Trigger an update run
databricks pipelines start --pipeline-id <pipeline-id>
```

---

## 5. Deploy Databricks App (Streamlit Command Center)

Deploy the FloodSense interactive app directly to Databricks Apps:

```bash
# 1. Create Databricks App
databricks apps create floodsense-dashboard --spec '{
  "command": ["streamlit", "run", "src/app/streamlit_app.py", "--server.port", "8501"],
  "env": [
    {
      "name": "DATABRICKS_SQL_WAREHOUSE_ID",
      "value": "<your-2x-small-warehouse-id>"
    }
  ]
}'

# 2. Deploy app source code
databricks apps deploy floodsense-dashboard --source-code-path .
```

---

## 6. Local Testing & Verification

Run all unit and integration tests locally:

```bash
source .venv/bin/activate
pytest tests/test_pipeline.py -v --cov=src
```

Launch the local Streamlit application:

```bash
streamlit run src/app/streamlit_app.py
```

Simulate Live Polling:
```bash
python -m src.ingestion.poller
```

Re-train & Log ML Models with MLflow:
```bash
python -m src.models.train
mlflow ui --port 5000
```
