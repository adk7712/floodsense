# FloodSense on Databricks (Phase 5)

What runs on Databricks, how it was set up, and what the first workspace runs showed. Everything
below was run on a Databricks **Free Edition** workspace on 2 Oct 2026, from the Databricks CLI
(v1.19.0); nothing here is a plan that hasn't been tried, except where marked **not done**.

---

## 1. Architecture

```
landing volume (API-shaped JSON files: live poller, or the 17 Apr 2021 replay export)
        │  Auto Loader (text, whole file)
        ▼
ONE Lakeflow declarative pipeline (databricks/pipelines/lakeflow_pipeline.py)
  raw_rainfall_bronze        one row per landed file, payload kept verbatim
  parsed_payloads            each payload parsed once by pipeline_core.payloads_to_readings
  raw_payloads_quarantine    payloads that don't parse, with the reason
  rainfall_readings_silver   station readings, 0–100 mm expectations, deduplicated
  weather_stations_silver    latest location per station
  flood_risk_predictions_gold  zone features, calibrated probability, tier (pipeline_core.gold_from_silver)
        │
        ▼
app / dashboard reading the gold table  (not done: see section 6)
```

**Rule:** every step that turns readings into risk is the pandas code in
`src/floodsense/serving/pipeline_core.py`, the same code the app, training and backtest use. Spark
only moves data. Gold hands the last 24 h of readings plus the 72 h warm-up to one pandas call
through `applyInPandas` (about 96 h × 70 gauges, small).

## 2. Status: the replay ran through the real pipeline and matches local scoring

| | |
|---|---|
| Workspace | `https://dbc-91a4e71d-644e.cloud.databricks.com` (Free Edition, AWS us-east-2) |
| Pipeline | `floodsense`, id `c32feb3d-5e5f-4c78-8219-e3acf131e804`, serverless, triggered, development mode |
| Update (the proof) | `34cfa680-4735-4a58-9558-bb8e38ea21a0`, full refresh, started 11:36:50 UTC, COMPLETED |
| Rows | 949 landed files → 949 bronze, 0 quarantined, 64,223 silver readings, 70 stations, 15,840 gold rows (55 zones × 288 steps) |
| Parity | `tests/test_phase5_parity.py`: 7 passed. Same 4,675 rows for 11:00–18:00 SGT, rain features within 1e-14, identical tiers on every row, Bukit Timah High at 12:45 |
| Evidence | `data/reference/phase5/run_info.json` and `databricks_replay_predictions.csv`, exported with `databricks/export_parity.py` |

The CSV alone could have been made on a laptop, so the run is signed off by a person opening the
workspace and checking that update `34cfa680…` exists with these row counts. A change to the
pipeline is accepted only with a new export, a passing parity test and that check.

**Signed off** by Akul9 on 2 Oct 2026, in the workspace UI: update `34cfa680…` (started 19:36
SGT) exists, and its graph shows raw_rainfall_bronze 949, parsed_payloads 949,
raw_payloads_quarantine 0, rainfall_readings_silver 64,223, weather_stations_silver 70 and
flood_risk_predictions_gold 15,840 rows. The two earlier updates in its history (`2f37752a…`
failed, `cc127c71…` empty gold) are the ones described in section 5.

One row differs in probability (not in tier): see **Known issues**.

## 3. Setup, as run

Prerequisites: a Free Edition workspace, the CLI (`curl -fsSL
https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sudo sh`) and a login:

```bash
databricks auth login --host https://<workspace>.cloud.databricks.com   # profile name, e.g. akul
export DATABRICKS_CONFIG_PROFILE=akul
```

Free Edition has a `workspace` catalog; everything lives in schema `workspace.floodsense`.

```bash
# Schema and volumes
databricks schemas create floodsense workspace
for v in landing artifacts autoloader; do databricks volumes create workspace floodsense $v MANAGED; done
A=dbfs:/Volumes/workspace/floodsense/artifacts
for d in wheels root root/models root/data root/data/reference; do databricks fs mkdir $A/$d; done

# Package, model and reference files (the pipeline sets FLOODSENSE_ROOT_DIR to $A/root)
uv build --wheel --out-dir dist
databricks fs cp dist/floodsense-0.1.0-py3-none-any.whl $A/wheels/ --overwrite
databricks fs cp -r models $A/root/models --overwrite
databricks fs cp -r data/reference $A/root/data/reference --overwrite

# The replay, as 949 API-shaped files in the landing volume (about 2 min to upload)
uv run python -m floodsense.serving.pipeline_core export-replay --out replay_landing
databricks fs mkdir dbfs:/Volumes/workspace/floodsense/landing/rainfall
databricks fs cp -r replay_landing dbfs:/Volumes/workspace/floodsense/landing/rainfall --overwrite

# Pipeline source and pipeline (edit <your-login> in the spec first)
W=/Workspace/Users/<your-login>/floodsense
databricks workspace mkdirs $W
databricks workspace import $W/lakeflow_pipeline.py --file databricks/pipelines/lakeflow_pipeline.py --format RAW --overwrite
databricks pipelines create --json @databricks/pipelines/pipeline_spec.json
databricks pipelines start-update <pipeline_id> --full-refresh
```

`databricks/pipelines/pipeline_spec.json` is the exact spec that ran. Notes on it:

- `environment.dependencies` installs the wheel and pins **scikit-learn 1.9.1**, the version that
  saved `models/flood_model.joblib` (the model contains scikit-learn objects).
- `floodsense.root_dir` is read by the pipeline before `floodsense` is imported. Only the driver
  reads files (the model); the parse UDF on the workers reads none.
- If the trained model isn't found the gold table fails loudly instead of quietly using the old
  rainfall heuristic (`load_model()`'s fallback).
- **Development mode**: a failed update stops instead of retrying, which protects the daily quota.

## 4. Exporting the parity files

Uses the workspace's *Serverless Starter Warehouse* (`databricks warehouses list` for its id):

```bash
python databricks/export_parity.py <pipeline_id> <update_id> <warehouse_id> data/reference/phase5
uv run pytest tests/test_phase5_parity.py -v
```

It writes the gold rows for 17 Apr 2021 11:00–18:00 SGT (ISO timestamps, `+08:00`) and
`run_info.json` with the ids, the update's start time and `SELECT count(*)` of every table.

## 5. What the workspace runs caught (all fixed)

The pipeline had passed the local Spark run (section 9) before any of these:

1. **Update `2f37752a…` failed** at bronze: Auto Loader rejects `cloudFiles.wholetext`. The
   text-format option is plain `wholetext`.
2. **Update `cc127c71…` completed with an empty gold table.** The gold function called
   `collect()`/`toPandas()` on silver. Lakeflow evaluates table functions while it builds the
   graph, so gold was computed from silver as it was then (empty) and finished *before* silver
   loaded; the event log warned that `DataFrame.collect` is unsupported. Gold now passes the
   window to `applyInPandas`, so Spark computes it after silver. A rerun would not have fixed it:
   gold would always have been one update behind.
3. Before the first run: `floodsense.spatial.singapore_geo` read the station file when imported,
   which would have broken the parse UDF on the workers (no files there). It now reads it on first
   use.

## 6. Not done yet

- **Live poller job** (a scheduled job calling `NEAPoller.stage_payload_to_volume` every 5–10
  minutes) has not been created. The landing volume and pipeline are ready for it.
- **Databricks App / dashboard** reading the gold table has not been deployed. The app still
  computes risk itself from the API, with the same feature and scoring functions.
- **Model registry**: the model has not been logged to MLflow or registered in Unity Catalog.

## 7. Answers to plan §9

- **Does Auto Loader plus a streaming table fit within the one-pipeline limit?** Yes. Bronze
  (Auto Loader), parse, quarantine, both silver tables and gold are one pipeline; update
  `34cfa680…` ran all six.
- **How much of the daily compute quota does one triggered update use?** Measured time, not DBUs:
  the full-refresh update took about 1 min 45 s from request to COMPLETED (about 55 s waiting for
  and starting compute, 49 s running; the six tables themselves took 38 s). DBUs are **unknown**:
  `system.billing.usage` is not available on this workspace (TABLE_OR_VIEW_NOT_FOUND).
- **Does `ai_query` work on Free Edition, and with which models?** Not tested. We don't depend on
  it: flood events were extracted once outside Databricks and signed off by hand
  (`data/reference/flood_events.csv`).
- **Can a scheduled job restart a Databricks App?** Not tested (no app deployed, section 6).

## 8. Known issues

- **Rows exactly on the active-rain gate can score differently across platforms.** The model scores
  zero unless `rain_120m >= 0.2` mm (one gauge tip). Floating-point sums of the same readings land
  a hair either side of 0.2 depending on the library build. In the replay, 1 of 4,675 rows
  (Geylang 11:25) scored 7.1e-8 locally and 0 on Databricks; tier Low on both. The parity test
  allows a probability difference only on rows within 1e-9 mm of the gate, requires identical
  tiers on every row, and checks gate rows stay far below the Moderate threshold.
  The same effect reaches training: rebuilding the feature store with the gate at
  `0.2 - 1e-9` adds 9,982 rows (7,574,472 instead of 7,564,490; 0.13%). **Fix after submission:**
  give the gate a tolerance in both `build_features` and `scoring`, rebuild features, retrain and
  re-evaluate (the selected model's margin over `rule_rain30` is small, so check the selection).
- The pandas/numpy versions on the serverless runtime were not recorded; local runs use pandas 3.
- **Unused legacy feature columns.** `pub_monitored` (hand-assigned per zone, unsourced) and
  `return_period_years` are still computed by `FeaturePipeline` and stored in the feature store,
  but the trained model doesn't use them (`MODEL_FEATURE_COLUMNS`). Removing them needs a feature
  store rebuild; after submission.

## 9. Running the pipeline locally first

`databricks/local/run_pipeline_local.py` runs **this exact pipeline file** in local Spark (PySpark
plus Java 17+), with a stand-in for the `dlt` module and a batch reader in place of Auto Loader. It
lands the 17 Apr 2021 replay plus one deliberately broken file, runs every table, and compares the
gold rows with local scoring. Not part of CI (it needs Java).

```bash
uv venv /tmp/spark-venv -p 3.12 && VIRTUAL_ENV=/tmp/spark-venv uv pip install pyspark pyarrow "pandas<3.1" -e .
PYSPARK_PYTHON=/tmp/spark-venv/bin/python PYSPARK_DRIVER_PYTHON=/tmp/spark-venv/bin/python \
  /tmp/spark-venv/bin/python databricks/local/run_pipeline_local.py UTC
```

Last result (2 Oct 2026, session timezone UTC and Asia/Singapore, applyInPandas gold): 950 files
to bronze, the broken file quarantined with its JSON error, 64,223 readings in silver, and gold
identical to local scoring for the replay window (4,675 rows, same tiers, largest probability
difference 2.6e-17, Bukit Timah High at 12:45). It did **not** catch the two workspace failures in
section 5: Auto Loader options, Lakeflow's graph evaluation and serverless are only exercised in
the workspace.
