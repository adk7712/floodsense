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
app (Live mode, section 15) and AI/BI dashboard reading the gold table
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
# (the pipeline is now managed by the asset bundle, databricks/bundle/pipeline.yml; section 24)
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

- **Poller on a live schedule**: the job exists and has run (section 11), but its schedule is paused
  to protect the daily compute cap.
- **Telegram alerts** run as the poller's third task (section 22), so they only post while the
  poller is unpaused.
- **Promoting a model to `champion`** is done for version 4 (promoted 23 Oct, adk signed off 23 Oct;
  section 25); version 3 was promoted on 18 Oct (section 21). Gold serves whatever the alias points
  to (section 17), so any later promotion is a matter of moving the alias.
- **Model cards:** version 4 (served) is `models/model_card_v4.json`; version 3 is
  `models/model_card_v3.json`, built from its refit
  summary. `models/model_card.json` still describes version 1 (Moderate 0.30%, git `cac91ca`); it
  is written by the training code, so it is kept as the record of the tested model.
- **A parity export on the served model** (section 17): the committed Databricks replay export was
  produced by version 1. Re-exporting needs a separate pipeline (a full refresh would wipe the live
  tables); planned after Demo Day.

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
  `0.2 - 1e-9` adds 9,982 rows (7,574,472 instead of 7,564,490; 0.13%). **Fixed 16 Oct 2026:**
  tolerance added in training and scoring; feature store rebuild and retrain still pending.
- The pandas/numpy versions on the serverless runtime were not recorded; local runs use pandas 3.
- **Unused legacy feature columns.** `pub_monitored` (hand-assigned per zone, unsourced) and
  `return_period_years` are still computed by `FeaturePipeline` and stored in the feature store,
  but the trained model doesn't use them (`MODEL_FEATURE_COLUMNS`). **Fixed 16 Oct 2026:** removed
  from the feature pipeline and schema.

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

## 10. Model registry (MLflow + Unity Catalog), as run on 6 Oct 2026

```bash
DATABRICKS_CONFIG_PROFILE=akul .venv/bin/python databricks/register_model.py   # --no-register: log only
```

- Logs the committed `FloodModel` as an `mlflow.pyfunc` model that scores through
  `floodsense.models.scoring.score_zone_features` (code shipped with `code_paths=src/floodsense`), with
  its signature, `model_card.json`, `final_report.json`, the thresholds and the test metrics.
- Experiment `/Users/<login>/floodsense-model` (the `floodsense` name is taken by the
  workspace folder), run `85df4e2d536f43d4a4df1663f1f5c14a`.
- *History (6 Oct):* registered as **`workspace.floodsense.flood_model` version 1, alias `champion`**
  (status READY); at that time each re-run added a version and moved `champion`. That is no longer
  so: `register_model.py` now defaults to the alias `candidate`, and only the team moves `champion`.
  The current champion is **version 4** (section 25).
- Check: `models:/workspace.floodsense.flood_model@champion`, loaded back with `mlflow.pyfunc`, scores
  the 17 Apr 2021 replay (4,675 rows) identically to the joblib model: max probability difference 0,
  tiers identical (4,113 Low, 367 Moderate, 195 High).

## 11. Live poller job, as run on 6 Oct 2026

Job `floodsense-rainfall-poller` (id `986423263843293`), defined in `databricks/jobs/poller_job.json`:

1. `poll_rainfall` (serverless Python task, `databricks/jobs/poll_rainfall.py`, uploaded to
   `/Workspace/Users/<login>/floodsense/poll_rainfall.py`, floodsense wheel as a dependency). It
   fetches the data.gov.sg `?date=` pages covering the last `--lookback-minutes` (default 40) and
   lands each page unchanged in `/Volumes/workspace/floodsense/landing/rainfall/live/`. Pages
   overlap between runs; silver deduplicates. The API key comes from the secret
   `floodsense/data_gov_api_key`. Without it, the anonymous API is used, with slower paging.
2. `refresh_pipeline`: a triggered (not full-refresh) update of pipeline `c32feb3d…`.

Schedule: every 30 min, Asia/Singapore, **paused** (Free Edition's daily compute cap). Unpause it in
the Jobs UI for Demo Day.

```bash
databricks secrets create-scope floodsense                       # once
databricks secrets put-secret floodsense data_gov_api_key        # once; prompts for the key
databricks workspace import /Workspace/Users/<login>/floodsense/poll_rainfall.py \
    --file databricks/jobs/poll_rainfall.py --format AUTO --overwrite
databricks jobs run-now --json '{"job_id": <id>, "python_params": ["--lookback-minutes", "5760"]}'  # first run: 96 h warm-up
```

Runs:
- The first 96 h run was rate-limited (HTTP 429) on the anonymous API.
- The next run landed 48 pages but was marked failed: `SystemExit` under Databricks' IPython counts
  as a failure.
- Both were fixed. Run `1048810239233343` succeeded end to end, using the API key.

After it:
- gold holds live risk for 55 zones × 288 steps (15,840 rows), from 16:45 SGT on 5 Oct to 16:40 SGT
  on 6 Oct
- bronze 1,058 files (949 replay + 109 live, each ingested once), 0 quarantined
- silver 167,332 readings

Gold now shows live data instead of the 17 Apr 2021 replay, because it always scores the 24 h before
the newest reading. The replay proof stays in update `34cfa680…` and the committed parity export.
Re-checking parity on the workspace now needs a full refresh with only the replay files landed.

## 12. PUB flood alerts, dashboard and governance (6 Oct 2026)

**Flood alerts.** Each poller run also lands today's recent flood-alert pages, unchanged, in
`/Volumes/workspace/floodsense/landing/flood_alerts/`. The pipeline has two more tables, which gold
doesn't read:
- `pub_flood_alerts_bronze`: one row per file (Auto Loader, schema location
  `autoloader/flood_alerts`)
- `pub_flood_alerts_silver`: one row per alert or cancellation, with the location circle;
  deduplicated on (identifier, msg_type)

Run `87423630652188` succeeded: 1 alert file and 0 alerts (none issued that day). Gold was live to
17:35 SGT, 15,840 rows, 0 quarantined. The silver parsing was checked on the warehouse against
PUB's documented example alert, and time, identifier, type, description and circle all parse.
Before the upload, the deployed pipeline source differed from the committed one only in comments.

**AI/BI dashboard** "FloodSense – Live Flood Risk" (id `01f1c168d5ba1a44819705ab969bf3ea`). It
reads gold and the pipeline tables, with times in SGT. The definition is in
`databricks/dashboards/`.

**Governance.** These were set through the SQL warehouse:
- Descriptions on schema `workspace.floodsense`, the `landing` and `artifacts` volumes and the
  registered model.
- Tags: `project`, `track` and `data_classification` on the schema; `source` and `layer` on every
  table.


## 13. Radar archive (NEA rain-area radar), as run on 15 Oct 2026

data.gov.sg's `weather-radar-images` API (`/70km` and `/240km`) returns one rain-intensity PNG every
5 minutes, with a bounding box in EPSG:4326 and an azimuthal-equidistant projection centred on the
radar. It accepts `?date=`, but keeps only about **3 months** (on 15 Oct, 15 Jul returned 404). It is
not Doppler data: motion has to be estimated from consecutive frames.

`databricks/jobs/archive_radar.py` (self-contained: requests, pandas, pyarrow) writes one Parquet per
day and size, `<out-dir>/<size>/date=YYYY-MM-DD/frames.parquet`, holding every frame's PNG bytes and
georeference. Days already archived are skipped.

- **Backfill:** run locally (no Databricks compute) for 16 Jul to 14 Oct 2026, then uploaded to the
  volume `workspace.floodsense.radar`. That is 91 days per size, 51,665 frames and about 250 MB.
  89 days have 270 or more of 288 frames; 23 and 24 Jul are short on NEA's side.
- **Daily job** `floodsense-radar-archiver` (id `486214899373176`, `databricks/jobs/radar_archive_job.json`; renamed `floodsense-daily-archiver` on 16 Oct, when it also took on gauges, §14):
  03:00 SGT, archives the last 3 days, **schedule on**. The first run (`727559731420638`) succeeded
  and wrote 12–14 Oct.

```bash
databricks volumes create workspace floodsense radar MANAGED
databricks workspace import /Workspace/Users/<login>/floodsense/archive_radar.py \
    --file databricks/jobs/archive_radar.py --format AUTO --overwrite
python databricks/jobs/archive_radar.py --out-dir data/raw/radar --start <first available day>  # backfill
```

## 14. Ongoing data collection: one daily job and an alerts job (16 Oct 2026)

The rainfall poller is paused to save compute, which also stopped gauge readings and PUB alerts
reaching the workspace after 6 Oct. Two jobs now keep the data growing without it:

| Job | Schedule | What it saves |
|---|---|---|
| `floodsense-daily-archiver` (id `486214899373176`, was `floodsense-radar-archiver`; `databricks/jobs/radar_archive_job.json`) | 03:00 SGT daily | Task `archive_radar` (§13), and task `archive_rainfall`: the last 3 complete days of NEA gauge readings |
| `floodsense-flood-alerts` (id `477250824276803`, `databricks/jobs/flood_alerts_job.json`) | every 15 min | PUB flash-flood alerts from the last 20 min |

**Gauges** (`databricks/jobs/archive_rainfall.py`, self-contained):
- Each API page for a day is written unchanged to
  `/Volumes/workspace/floodsense/landing/rainfall/daily/YYYY-MM-DD/pNN.json`. That is under the
  pipeline's rainfall landing path, so Auto Loader takes the readings in on the next pipeline update;
  silver deduplicates any overlap with the poller. The job doesn't run the pipeline.
- Folders are `YYYY-MM-DD`, not `date=YYYY-MM-DD`, so Auto Loader can't read them as a partition
  column.
- Pages are written in `landing/archive_state/rainfall/` first and moved in whole; a finished day's
  marker (`YYYY-MM-DD.complete`, with its page and step counts) stays there. Marked days are skipped,
  so a missed night is caught up on the next run.
- Backfill: 1–12 Oct was fetched locally and uploaded (12 days, 288 of 288 steps each); the job's
  first run (`475904207880746`, 16 Oct) fetched 13–15 Oct, 288 of 288 steps each. Unlike radar, gauge history stays on data.gov.sg, so any gap can be
  refetched with `--start`.

**PUB alerts** (`databricks/jobs/archive_flood_alerts.py`, self-contained):
- The API keeps no history (past dates return empty records), so a once-a-day job would miss nearly
  every alert. It publishes a record about every 2 minutes, 25 to a page; the job reads 20 minutes
  back, so runs overlap.
- Only pages that hold an alert are landed, in `landing/flood_alerts/` (the folder
  `pub_flood_alerts_bronze` reads), named like the poller's. `pub_flood_alerts_silver` deduplicates.
  Quiet checks write nothing; the run history and log record them.
- First run `507862846912031` (16 Oct) succeeded; no alert was active.
- **Pause it while the poller runs** (from 24 Oct, A3): the poller lands alerts too.
- Compute: 96 short serverless runs a day. Free Edition doesn't publish its cap; check the run
  history after the first day, and move to every 30 min if it looks heavy.

```bash
databricks workspace import /Workspace/Users/<login>/floodsense/archive_rainfall.py \
    --file databricks/jobs/archive_rainfall.py --format AUTO --overwrite
databricks workspace import /Workspace/Users/<login>/floodsense/archive_flood_alerts.py \
    --file databricks/jobs/archive_flood_alerts.py --format AUTO --overwrite
# backfill locally, then upload pages and markers
python databricks/jobs/archive_rainfall.py --out-dir <dir>/daily --state-dir <dir>/state --start 2026-10-01
databricks fs cp -r <dir>/daily dbfs:/Volumes/workspace/floodsense/landing/rainfall/daily
databricks fs cp -r <dir>/state dbfs:/Volumes/workspace/floodsense/landing/archive_state/rainfall
```

## 15. The app reads the gold table (A2, 16 Oct 2026)

Live mode shows the risk the pipeline scored, from `flood_risk_predictions_gold`, by default. The
old path (the app scoring the API itself) stays as a fallback, behind the **Settings** button at the bottom of
the sidebar: it opens a single toggle,
**data.gov.sg**, to its right.

- **Reader:** `floodsense.serving.gold`. One SQL statement through the Databricks SDK's statement
  API takes the newest row per zone (`QUALIFY row_number() … = 1`, times as `unix_timestamp`, shown
  in SGT). The result is checked: the columns are `PREDICTION_COLUMNS`, every zone is a URA planning
  area, and the tiers are Low/Moderate/High. `latest_zone_risk()` is the same call for other code,
  e.g. a Telegram bot.
- **Settings** (in the gitignored `.env`; never commit values):
  - `FLOODSENSE_DATABRICKS_WAREHOUSE_ID`: the SQL warehouse. A Databricks App can supply
    `DATABRICKS_WAREHOUSE_ID` instead.
  - `FLOODSENSE_DATABRICKS_PROFILE`: the CLI profile used locally (`databricks auth login`). No
    token is stored in the repo.
  - `FLOODSENSE_GOLD_TABLE` (default `workspace.floodsense.flood_risk_predictions_gold`) and
    `FLOODSENSE_GOLD_STALE_MINUTES` (default 60).
  - With no warehouse set (CI, a fresh clone), Live mode starts on data.gov.sg without a warning.
- **Freshness:** the page shows the data's age next to the latest reading; when gold is older than
  60 minutes the age turns amber and a stale warning shows in both views (Analyst and Resident).
  If gold can't be read, the app falls back to data.gov.sg; the reason is in an Analyst-only
  "Technical detail" expander, and the Resident view shows no gold warning.
- **Cost:** results are cached for 15 minutes (half the poller's interval), so each viewer queries
  the warehouse at most 4 times an hour. After a failed read the app backs off for 60 seconds;
  auto-refresh no longer clears that, only the Retry button does. A cold warehouse takes about 25 s to answer.
- **Checked on 16 Oct:** 55 zones read from the workspace, last scored 6 Oct 17:35 SGT (the poller
  is paused), so the page showed "9 days old". Checked in light and dark mode.

## 16. Databricks App (A6, 16 Oct 2026)

The Streamlit app runs as the Databricks App **`floodsense`**. Only people signed in to the
workspace can open it; anyone else gets the Databricks sign-in page.

- **Config:** `app.yaml` (repo root) starts Streamlit on `$DATABRICKS_APP_PORT`. Its env comes from
  the app's resources, defined in `databricks/app/app.json`:
  - `sql-warehouse` (CAN_USE) → `DATABRICKS_WAREHOUSE_ID`, which `floodsense.serving.gold` reads
  - `data-gov-key`: secret `floodsense/data_gov_api_key` (READ) → `FLOODSENSE_DATA_GOV_API_KEY`
  - `gold`: table `workspace.floodsense.flood_risk_predictions_gold` (SELECT)
- **Identity:** the app runs as its own service principal (Databricks injects `DATABRICKS_HOST`,
  `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET`); the resources grant it what it needs, so no
  token or profile is involved.
- **Dependencies:** `requirements.txt`, exported with the `radar` extra so OpenCV is installed
  (`uv export --no-hashes --no-dev --extra radar --format requirements-txt -o requirements.txt`). It
  installs the `floodsense` package from the uploaded source (`-e .`). Runtime: Python 3.11.
- **What is uploaded:** the repo minus `databricks/app/sync_exclude.txt` (tests, docs, submission,
  local notes). `databricks sync` doesn't follow `.gitignore`'s re-include lines, so the rainfall
  store is named with `--include`. Every file must be under 10 MB (the largest is 3.9 MB).
- **Free Edition:** at most 3 apps; an app stops itself up to 24 h after it is started or
  deployed, and uses the daily compute quota while it runs. **Stop it when it isn't needed; start it
  before office hours, rehearsals and the demo.**

Steps, as run (fill in `<login>` and the warehouse ID in `app.json` first):

1. Create the app (once): `databricks apps create --json @databricks/app/app.json`
2. Upload the source:
   ```bash
   databricks sync . /Workspace/Users/<login>/floodsense-app \
       --exclude-from databricks/app/sync_exclude.txt \
       --include "data/raw/rainfall/readings/**" --include "data/raw/rainfall/stations.parquet"
   ```
3. Deploy: `databricks apps deploy floodsense --source-code-path /Workspace/Users/<login>/floodsense-app`
4. Check the logs: `databricks apps logs floodsense`
5. Stop it: `databricks apps stop floodsense`. Start it later: `databricks apps start floodsense`

First deployment `01f1c8c47306131f8da3bded252d3b6f` (16 Oct) installed and started cleanly.

## 17. Gold scores with the model registered in Unity Catalog (A4, 16 Oct 2026)

The pipeline's configuration `floodsense.model_uri` is
`models:/workspace.floodsense.flood_model@champion`, so gold scores with whatever version the
`champion` alias points to. Promoting a new model (A4.4) is a matter of moving the alias; the
pipeline doesn't change. With the key empty, gold falls back to `models/flood_model.joblib` under
`floodsense.root_dir`, which the local runner uses.

- **Loader:** `floodsense.models.artifact.load_registered_model()` downloads the registered model
  and loads only its `artifacts/flood_model.joblib`. It deliberately skips `mlflow.pyfunc`: the
  registered model bundles the floodsense code from registration day (`code_paths`), and pyfunc
  would put that older copy ahead of the pipeline's wheel. The driver log (not the event log) prints the version used.
- **No fallback:** if the registry can't be read, the update fails rather than scoring with
  something else.
- **Environment:** the pipeline's dependencies gained `mlflow-skinny` (also the `serving` extra in
  `pyproject.toml`). The wheel was rebuilt and re-uploaded, which also brought the
  newest-filled-step rule (gold stops at the newest step most gauges have reported).

**Parity (A4.3), 16 Oct:**
- *Parity is proven for version 1 only.* The committed export
  (`data/reference/phase5/databricks_replay_predictions.csv`) was produced by version 1; the
  champion is now version 3. A version 3 re-export is pending (Akul; it needs a Databricks run,
  paused to save credits). Locally, version 3 against that export gives 240 of 4,675 tier
  differences (as expected for a different model; 0 differences against archived version 1).
- At the time (16 Oct), the file behind `@champion` (version 1) had the same SHA-256 as the committed
  `models/flood_model.joblib`.
- Loaded through `load_registered_model`, it scores the 17 Apr 2021 replay exactly like the
  committed Databricks export (`data/reference/phase5/databricks_replay_predictions.csv`): 4,675
  rows, the largest probability difference off the rain gate 1e-16, tiers identical (4,113 Low,
  367 Moderate, 195 High), and identical to the local joblib.
- **Gate tolerance (B4.1, merged 16 Oct):** once the wheel is next rebuilt, rows whose 120-minute rain is
  within 1e-6 mm of the 0.2 mm gate count as active, so about 0.13% of rows score slightly differently
  from the champion's original behaviour. Rebuild the wheel and redeploy when promoting a new model.
- The original full-refresh parity run (section 2) wasn't repeated: the landing folder now holds
  live and daily data as well as the replay, so it would need a full rebuild that discards gold's
  history. Same file plus same scores is the same model.
- **In the pipeline:** update `2ca71abb…` (triggered, 16 Oct) completed with no warnings or
  errors, scoring through the registry (with `floodsense.model_uri` set there is no other path).
  It took in the 1–15 Oct daily gauge files: gold's newest step is 15 Oct 23:55 SGT, 55 zones,
  90 gauges reporting.

Steps, as run:

```bash
uv build --wheel --out-dir dist
databricks fs cp dist/floodsense-0.1.0-py3-none-any.whl \
    dbfs:/Volumes/workspace/floodsense/artifacts/wheels/ --overwrite
databricks workspace import /Workspace/Users/<login>/floodsense/lakeflow_pipeline.py \
    --file databricks/pipelines/lakeflow_pipeline.py --format AUTO --overwrite
databricks pipelines start-update <pipeline_id>
```

## 18. Governance: descriptions and tags (A8, 16 Oct 2026)

Audited every object in `workspace.floodsense`, then filled the gaps:

| Object | Description | Tags |
|---|---|---|
| Schema `floodsense` | yes (6 Oct) | `project`, `track`, `data_classification` (6 Oct) |
| 8 pipeline tables (bronze, silver, quarantine, gold, PUB alerts) | yes (from the pipeline code) | `source`, `layer` (6 Oct); **added** `project`, `data_classification` |
| Gold columns | **added** in the pipeline code (`GOLD_SCHEMA` column comments); live since the 16 Oct 18:20 refresh (11 of 11) | |
| Volumes `landing`, `radar`, `artifacts` | yes | **added** `project`, `data_classification`, `purpose`, and `source` on `landing` and `radar` |
| Volume `autoloader` | **added** (pipeline state, not data) | **added** as above |
| Model `flood_model` | yes | **added** `project`, `track`, `data_classification`, `model_type` |
| Model version 1 (was `@champion` on 6 Oct; champion is now version 3) | **added** (6 Oct): what it is, its tiers and its test result (history; not re-checked for version 3) | |
| Jobs (poller, daily archiver, flood alerts) | yes | **added** `project`, `track`, `role` (also in the job JSON files) |
| App `floodsense` | yes | Apps have no tags |

The pipeline's own event-log table (`event_log_…`) is created by Databricks and left as is. Asset
Bundles (A8.2) are a stretch goal for after the freeze.

## 19. Refresh and redeploy, 16 Oct 2026 evening

- **Wheel** rebuilt and uploaded with everything merged that day (B4.1 gate tolerance, the
  newest-filled-step rule, the Telegram bot module), plus the pipeline source with gold's column
  comments.
- **Poller run** `171572439054136`, run by hand with `--lookback-minutes 1200` to cover the day since
  the daily archive's last day. Both tasks succeeded, and the update had no warnings. Gold's newest
  step was 18:20 SGT, 55 zones, 90 of 90 gauges. All 11 gold columns now carry comments.
  To pass parameters to the poller task, use
  `databricks jobs run-now --json '{"job_id": <id>, "python_params": ["--lookback-minutes", "1200"]}'`.
- **Databricks App** redeployed (`01f1c94bf50f16b7a08f06a9438d1378`) with that day's app: the
  Resident view switch top right, the data.gov.sg fallback behind Settings. Akul checked it in a
  browser, then it was stopped again.
- **Telegram alerts (proposed here; deployed 18 Oct, section 22):** run the bot as a third task in the poller job, after
  `refresh_pipeline` (`live --once --state-file /Volumes/workspace/floodsense/artifacts/telegram_state.json`),
  rather than its own 5-minute schedule. It needs the secrets `floodsense/telegram_bot_token` and
  `floodsense/telegram_chat_id` from adk's bot.
- **Note:** the project's `.venv` no longer contains `uv` (a `uv sync` removed it); use the Homebrew
  `uv` (`/opt/homebrew/bin/uv`).

## 20. Faster radar in Replay (16 Oct 2026)

Radar frames come from, in order: the local archive (`data/raw/radar`), then the Databricks radar
volume (`settings.radar_volume`, read through the Files API and kept in memory per day), then
data.gov.sg. The live API is slow for replay: it pages newest-first through the whole day, so a
1am frame took about 2 s, against 0.1–0.4 s from an archive.

- **App:** a replayed time's radar (more than an hour old) is computed once and kept (up to 500
  times); recent times still refresh every 5 minutes.
- **Databricks App:** `FLOODSENSE_RADAR_VOLUME=/Volumes/workspace/floodsense/radar` in `app.yaml`, and
  a fourth app resource, `radar` (the volume, READ_VOLUME). Deployment
  `01f1c94f54381db19ff1416f99f8309e`, checked by Akul in a browser, then stopped.
- **Local archive** topped up from the volume to 15 Oct (both sizes). To top it up again, copy any
  newer `date=` folders from `dbfs:/Volumes/workspace/floodsense/radar/{70km,240km}/` into
  `data/raw/radar/`.
- A frame read from the volume, the local archive and data.gov.sg is byte-identical (checked for
  30 Sep 14:30).

## 21. Candidate model version 3: the refit (18 Oct 2026)

The team chose the refit of `rule_rain60` on 88 verified labels: the budget rule now gives Moderate
**0.53%** (about 37.7 mm in 60 min), with High unchanged at 0.71%
(`models/candidates/rule_rain60_refit_2026-10-17.json`, `docs/candidate_selection_2026-10.md`).

- Rebuilt the local feature store with the B4.1 gate tolerance (7,587,282 rows, as adk's), then
  regenerated the refit with `python -m floodsense.models.refit_champion`. Thresholds, millimetre
  equivalents, training rows and the cross-validation score were identical to adk's run on `33e9484`.
- `databricks/register_model.py` now takes `--model-path`, `--summary` and `--alias` (default
  `candidate`). A candidate is logged with its own summary, never the committed model's test-set
  report, and its file is always stored as `artifacts/flood_model.joblib`, the name the pipeline
  loads. `load_registered_model` also accepts a single differently named `.joblib`.
- Registered as **version 3, alias `candidate`** (version 2 is the same model, but its file kept its
  local name; marked superseded). At this point `champion` was still version 1 (Moderate 0.30%), so gold was unchanged
  until the promotion (history; see the next item).
- **Promoted 18 Oct** (by Akul, ahead of the planned 23 Oct; **adk signed off on 18 Oct**, B4.5 in `docs/ROUND2_HANDOFF.md`): `champion`
  → version 3, wheel rebuilt, pipeline update `5e1554ac…` completed with no warnings. The committed
  `models/flood_model.joblib` is now version 3 (byte-identical to the registry's file), so the app's
  Replay, data.gov.sg and forecast views use the same model as gold. Version 1 is kept as
  `models/archive/flood_model_v1_2026-10-01.joblib`; the parity test checks the 2 Oct export against it.
- Effect on the 17 Apr 2021 demo storm (11:00–18:00, all zones): Moderate zone-steps 367 → 127,
  High 195 → 211 (the refit's calibration puts High at about 43 mm instead of 44 mm); Bukit Timah
  still turns High at 12:45. At 12:15, 1 Moderate area instead of 6.
- To roll back: move `champion` to version 1, restore the archived file, rebuild the wheel.
- Previously planned for 23 Oct: move the alias
  (`MlflowClient(registry_uri="databricks-uc").set_registered_model_alias("workspace.floodsense.flood_model", "champion", 3)`),
  rebuild and upload the wheel, then run the pipeline once.

## 22. Telegram alerts as a poller task (18 Oct 2026)

The bot (`floodsense.alerts.telegram_bot`, @fs_tele_alert_bot, channel "floodsense test") is the
third task of `floodsense-rainfall-poller`, after `refresh_pipeline`, so it reads gold right after
each pipeline update. The job's schedule stays **paused** until A3.

- Task `telegram_alerts`: `databricks/jobs/telegram_bot.py`, uploaded to
  `/Workspace/Users/<login>/floodsense/telegram_bot.py`, with the floodsense wheel. Parameters:
  `--warehouse-id <id> live --once --state-file /Volumes/workspace/floodsense/artifacts/telegram_state.json`.
  The script sets `FLOODSENSE_DATABRICKS_WAREHOUSE_ID` (serverless tasks can't set environment
  variables) and exits only on an error: under IPython even `SystemExit(0)` fails the task.
- Secrets in scope `floodsense`: `telegram_bot_token` and `telegram_chat_id`, created by Akul on
  18 Oct. The bot reads them itself.
- The per-zone state file on the artifacts volume stops re-sends across runs. Deleting it makes the
  next run treat every zone as new.
- Test run on 18 Oct (one manual run of the paused job): `poll_rainfall` and `refresh_pipeline`
  succeeded. `telegram_alerts` first failed on the `SystemExit(0)` above, then succeeded after the
  fix (repair run). All 55 zones were Low, so no message was sent; the state file was written.
  A real post from the job has not been seen yet.
- The separate 5-minute job `databricks/jobs/telegram_alerts_job.json` is kept as a paused fallback.

```bash
databricks workspace import /Workspace/Users/<login>/floodsense/telegram_bot.py \
    --file databricks/jobs/telegram_bot.py --format AUTO --overwrite
# Job and pipeline settings are managed by the asset bundle since 22 Oct (section 24): edit
# databricks/bundle/*.yml and run `databricks bundle deploy`, not `jobs create/reset`.
```

## Asset Bundles (A8.2)

`databricks.yml` and `databricks/bundle/` describe the existing Databricks setup as code, read from
the live settings on 22 Oct 2026. **The bundle has NOT been deployed and nothing has been bound.**
`databricks bundle validate -p akul` passes. Deploying without binding first would create
duplicate jobs, a second pipeline and so on.

**What is covered**

| Bundle key | File | Live resource |
|---|---|---|
| `jobs.rainfall_poller` | `jobs.yml` | job `floodsense-rainfall-poller`, id `986423263843293` (PAUSED) |
| `jobs.daily_archiver` | `jobs.yml` | job `floodsense-daily-archiver`, id `486214899373176` (UNPAUSED) |
| `jobs.flood_alerts` | `jobs.yml` | job `floodsense-flood-alerts`, id `477250824276803` (UNPAUSED) |
| `pipelines.floodsense` | `pipeline.yml` | pipeline `floodsense`, id `c32feb3d-5e5f-4c78-8219-e3acf131e804` |
| `dashboards.live_risk` | `dashboard.yml` | AI/BI dashboard, id `01f1c168d5ba1a44819705ab969bf3ea` |
| `schemas.floodsense_schema` | `unity_catalog.yml` | schema `workspace.floodsense` |
| `volumes.artifacts`, `autoloader`, `landing`, `radar` | `unity_catalog.yml` | the four volumes |
| `registered_models.flood_model` | `unity_catalog.yml` | `workspace.floodsense.flood_model` |
| `apps.floodsense_app` | `app.yml` (**opt-in**, not in `include`) | app `floodsense`, id `933a4397-9418-4a08-a0af-3fe7b3acfffe` |

Your login is never stored: paths use `${workspace.current_user.userName}`. The warehouse id,
wheel path, catalog and schema are variables at the top of `databricks.yml`. The only secret
reference is the app's `floodsense/data_gov_api_key` (in `app.yml`); no secret value is in the repo.
Schema, volumes and model have `prevent_destroy`, so `bundle destroy` cannot drop them.

**What is not covered**

- Secret scope `floodsense` and its values (data.gov.sg key, Telegram token and chat id): create by hand.
- Model versions and the `candidate`/`champion` aliases (use `databricks/register_model.py`).
- Volume contents: the wheel, model file, reference data, landing files, radar frames, Telegram state file.
- Tables the pipeline writes (bronze, silver, gold).
- The uploaded scripts in `/Workspace/Users/<login>/floodsense/` (`poll_rainfall.py`, `telegram_bot.py`,
  `archive_*.py`, `lakeflow_pipeline.py`). The jobs and pipeline point at them; the bundle does not upload them.
- The SQL warehouse itself, and the app's code (still shipped with `databricks sync`, section 16).
  `app.yml` is opt-in because bundle sync would upload the whole repo and ignores `sync_exclude.txt`.
- `databricks/jobs/telegram_alerts_job.json`, the paused fallback job. It does not exist in the workspace.
- Binding of schemas, volumes and models was not tried against the workspace. If `bind` refuses one,
  delete that block from `unity_catalog.yml`; nothing else depends on it.

**Steps to bind and deploy (do these on your own, in order)**

1. **Check the schedules first.** Open Workflows in the workspace (or run
   `databricks jobs get <id> -p akul`). Today: poller **PAUSED** (it must stay paused until 24 Oct),
   archiver UNPAUSED, flood-alerts UNPAUSED. The bundle states exactly these. If any live job differs
   from that list, fix `pause_status` in `databricks/bundle/jobs.yml` before going on.
2. Open a terminal in the repo and run `databricks bundle validate -p akul`. It must say
   "Validation OK!".
3. Bind each resource to what already exists. Answer `y` when asked:
   ```
   databricks bundle deployment bind rainfall_poller 986423263843293 -p akul
   databricks bundle deployment bind daily_archiver 486214899373176 -p akul
   databricks bundle deployment bind flood_alerts 477250824276803 -p akul
   databricks bundle deployment bind floodsense c32feb3d-5e5f-4c78-8219-e3acf131e804 -p akul
   databricks bundle deployment bind live_risk 01f1c168d5ba1a44819705ab969bf3ea -p akul
   databricks bundle deployment bind floodsense_schema workspace.floodsense -p akul
   databricks bundle deployment bind artifacts workspace.floodsense.artifacts -p akul
   databricks bundle deployment bind autoloader workspace.floodsense.autoloader -p akul
   databricks bundle deployment bind landing workspace.floodsense.landing -p akul
   databricks bundle deployment bind radar workspace.floodsense.radar -p akul
   databricks bundle deployment bind flood_model workspace.floodsense.flood_model -p akul
   ```
   (Only if you include `app.yml`: `databricks bundle deployment bind floodsense_app floodsense -p akul`.)
4. Look at the plan before changing anything: `databricks bundle plan -p akul`. Every resource
   should say "update" or "skip", never "create". If you see a "create" for something that already
   exists, stop and ask adk or me; it means a bind was missed.
5. Run `databricks bundle deploy -p akul`. This overwrites the live settings with the files in the repo
   (and the dashboard draft with the committed `.lvdash.json`).
6. Check the result straight away: `databricks jobs get 986423263843293 -p akul` must still show
   `"pause_status": "PAUSED"`; the other two UNPAUSED. If the poller is unpaused by mistake, pause it
   in Workflows at once.
7. Do not run `databricks bundle destroy`. Do not run `bundle run` for the poller before 24 Oct.

## 23. Telegram subscriptions (22 Oct 2026)

People can message @fs_tele_alert_bot to get a private alert for the planning areas they pick:
`/subscribe Bukit Timah`, `/unsubscribe Bukit Timah`, `/unsubscribe all`, `/list`, `/start`,
`/help`. At most 5 areas per chat. Alerts use the same rules as the channel (only on a rise to
Moderate or High, one per area per hour, nothing on missing data, no all-clear). Email sign-up is
**not built** (it needs an email provider).

How it works: the bot is a batch job, so each run of the `telegram_alerts` task first reads new
messages (`getUpdates`), handles the commands, replies, then sends alerts to the channel and to
each area's subscribers. People therefore wait up to one poller interval (30 minutes) for a reply.
Everything is kept in one JSON file: chat id, area and subscribed-at time, plus the id of the last
message handled. No names or usernames. Without `--subscriptions-file` the bot behaves as before.

Where the file lives: on Databricks `/Volumes/workspace/floodsense/artifacts/telegram_subscriptions.json`
(next to `telegram_state.json`, where the bot's other state already is). Locally
`data/processed/telegram_subscriptions.json` (inside the gitignored `data/processed/`).

Deploy steps (not done yet; they change the workspace, so they need Akul's OK):

1. Merge this branch after adk has reviewed it, and build the wheel as in the earlier sections.
2. Upload the new wheel and the updated task script to the workspace, using the same two commands
   as section 22 (wheel upload, then `databricks workspace import ... telegram_bot.py --overwrite`).
3. In `databricks/jobs/poller_job.json`, add two more parameters to the `telegram_alerts` task,
   after the existing `--state-file` value:
   `"--subscriptions-file", "/Volumes/workspace/floodsense/artifacts/telegram_subscriptions.json"`.
4. Apply the job settings with `databricks jobs reset`, as in section 22.
5. Check that no webhook is set on the bot (a webhook blocks `getUpdates`). In a browser open
   `https://api.telegram.org/bot<token>/getWebhookInfo` with the token from the secrets scope and
   check that `url` is empty. Do not paste the token anywhere else.
6. In Telegram, message the bot `/start`, then `/subscribe Bukit Timah`.
7. Run the poller job once by hand. The `telegram_alerts` task should succeed and you should get a
   reply to `/start` and `/subscribe`. Then send `/list` and run it again to see the reply.
8. To test an alert without waiting for rain, run the bot locally against a replay (`replay --date
   2021-04-17 --dry-run`) to read the message text; a real private alert needs a real rise.
9. To remove your test data, send `/unsubscribe all`.

**Demo Day: fast replies (agreed 22 Oct).** On the poller's 30-minute schedule a `/subscribe` reply
can take up to 30 minutes, and none comes while the job is paused. For the demo, run the bot on the
demo laptop instead, checking every 60 seconds:

1. Make sure the laptop can read gold, as the app does when it runs locally against Databricks.
   Check that `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in the local `.env`. Don't print
   them.
2. Stop the Databricks side from handling the same messages: either keep the poller job paused, or
   remove `--subscriptions-file` from its `telegram_alerts` task for the demo. Two bots reading
   `getUpdates` take messages from each other, and both would post channel alerts.
3. Start the bot:
   `uv run python -m floodsense.alerts.telegram_bot live --interval-seconds 60 --subscriptions-file data/processed/telegram_subscriptions.json`
4. In Telegram send `/subscribe Bukit Timah`; the reply should arrive within about a minute.
5. Stop it with Ctrl+C after the demo. To delete the demo subscriptions, send `/unsubscribe all`
   first.

Not rehearsed yet: run through it once before Demo Day.

## 24. Deploy run, 22 Oct 2026 (Akul's session)

Run from branch `akul/features-22oct` (`9bf0210` plus the fixes below); master is `f0de215`.

- **Wheel** rebuilt and uploaded to `/Volumes/workspace/floodsense/artifacts/wheels/` (04:11 SGT).
  It now has `floodsense.monitoring` and `floodsense.alerts.subscriptions`. Nothing has run on it
  yet; the pipeline picks it up at the next poller run.
- **Telegram subscriptions on:** the poller job's `telegram_alerts` task now also gets
  `--subscriptions-file /Volumes/workspace/floodsense/artifacts/telegram_subscriptions.json`
  (`jobs reset`, schedule still **PAUSED**). `databricks/jobs/poller_job.json` and
  `databricks/bundle/jobs.yml` match. `telegram_bot.py` re-imported. Not checked: whether a webhook
  is set on the bot (it would block `getUpdates`); the first poller run will show it.
- **PUB scorecard job** `floodsense-pub-scorecard` created, id `94062619945493`, schedule
  **PAUSED**. Two manual runs **failed**:
  1. an import from the wrong module (`load_registered_model` lives in `models/artifact.py`); fixed
     in `databricks/jobs/pub_scorecard.py` and re-imported;
  2. then `MlflowException`: every model artifact download from Unity Catalog storage returned
     S3 `400 Bad Request` (HeadObject). The pipeline uses the same loader and worked on 18 Oct, so
     the cause is the job's environment. **Not investigated further; the table
     `workspace.floodsense.pub_scorecard` does not exist yet.**
- **Dashboard** text updated to Moderate 0.53% (the only difference from the committed file) and
  republished (04:13 SGT).
- **Genie space** "FloodSense risk (gold)" created, id `01f1cd8bf8de1d5fba454efa5909044a`, on the
  gold table and warehouse `dfa0305abf58b59e`, with instructions (thresholds, UTC timestamps,
  "not an official warning") and four sample questions. Not shared; not tried yet.
- **v3 parity re-export: not done.** The proof replays 17 Apr 2021 through the pipeline with a
  full refresh, which would wipe the live bronze, silver and gold tables (silver's history is what
  the scorecard re-scores). It needs a separate pipeline; team decision.
- **Asset Bundle:** all 11 resources **bound** (`bundle deployment bind`; this changes no
  resource). `bundle plan`: 0 to add, 5 to change, 0 to delete. **Not deployed**: a deploy would
  set the three jobs to `edit_mode: UI_LOCKED` (no edits or unpausing in the UI, only through the
  bundle or CLI), and it lists a properties update on the schema (the live schema carries system
  properties the bundle doesn't). **Decided (Akul, 22 Oct): not deployed before Demo Day; it stays
  bound.** Until a deploy, edit and unpause the jobs as before (UI or CLI).

**Later on 22 Oct: model download check and a manual poller run.**
- The champion (version 3) downloads and loads fine from the laptop (mlflow 3.16.1). Only the
  scorecard job's direct S3 download fails. Setting
  `MLFLOW_USE_DATABRICKS_SDK_MODEL_ARTIFACTS_REPO_FOR_UC=true` in the job made no difference
  (probably an older mlflow on the serverless image); undone. The model file on the artifacts
  volume is still **version 1** (2 Oct), so it can't stand in for the champion. Scorecard job
  still failing, still paused.
- Poller run `425831022323729` by hand, schedule kept **PAUSED**: `poll_rainfall` and
  `refresh_pipeline` succeeded, so **the pipeline still loads the champion from the registry**.
  Gold's newest step 15:25 SGT, 55 zones, 89 gauges, all Low.
- **Careful with `python_params`:** it replaces the arguments of *every* Python task in the job.
  The section 19 command (`"python_params": ["--lookback-minutes", "1200"]`) dated from before the
  Telegram task, so the bot got only those two arguments and failed (`--warehouse-id` missing).
  Repaired by rerunning `telegram_alerts` alone with its full argument list (succeeded; nothing
  posted, all zones Low). For a lookback refresh, pass both lists, or rerun the bot task as above.
- No subscriptions file yet (nobody has messaged the bot). Whether a webhook is set is still
  unconfirmed: test with `/start` in Telegram before the next run.
- Compute outage (21 Oct 01:00 to 22 Oct 03:30 SGT): cause not found. `system.billing` and
  `system.lakeflow` aren't enabled on this workspace (enabling is a settings change), so usage
  can't be read; check the account usage page or email by hand.

**Asset Bundle deployed, 22 Oct (Akul's decision, reversing "not before Demo Day").**
`databricks bundle deploy`: 0 created, 5 changed, 0 deleted; 198 files uploaded to
`/Workspace/Users/<login>/.bundle/floodsense/prod/files`. Live settings compared before and after:
- The 3 jobs only gained the bundle marker and `edit_mode: UI_LOCKED`. Tasks, parameters and
  schedules are unchanged: poller **PAUSED**, daily archiver and flood alerts **UNPAUSED**.
- The pipeline only gained the bundle marker; `edition: ADVANCED` is now explicit.
- The schema's properties are unchanged (`custom_max_retention_hours: 0` now shows). Every later
  `bundle plan` lists "update schemas.floodsense_schema", because Databricks sets system
  properties (owner, predictive optimisation) the bundle doesn't list. Harmless.
- Not in the bundle and unchanged: the Databricks App, the scorecard job (`94062619945493`), the
  Genie space, the secrets, model versions.

**Changing a job from now on (e.g. A3 on 24 Oct, unpausing the poller):** the UI won't allow edits.
1. In `databricks/bundle/jobs.yml`, change the job's `pause_status` (e.g. the poller's to `UNPAUSED`;
   the flood-alerts job's to `PAUSED`).
2. Run `databricks bundle plan -p akul` and check only that change (plus the schema line) shows.
3. Run `databricks bundle deploy -p akul`.
4. Commit the YAML change, so the repo matches what is live.
A CLI `jobs update` also works but the next `bundle deploy` would undo it, so prefer the steps above.

## 25. Model version 4: the refit on 92 labels (registered and promoted 23 Oct 2026)

adk decided on 22 Oct to register the refit of `rule_rain60` on all 92 verified labels (62 floods in
2017–2023). The budget rule now gives Moderate **0.40%** (about 32.9 mm in 60 min), with High
unchanged at 0.71% (about 43.4 mm) (`models/candidates/rule_rain60_refit_2026-10-22.json`,
`models/model_card_v4.json`). Cross-validation results and the reasons are in
`docs/ROUND2_HANDOFF.md` §0 (22 Oct, later).

**Done 23 Oct (Akul; adk's sign-off relayed by Akul):**
- Regenerated the refit on `e10a648` (same model, feature and label code as `f467db8`). Thresholds,
  millimetre equivalents, training rows and the cross-validation score were identical to adk's
  run; adk's clean summary JSON is the one committed.
- Registered as **version 4** (alias `candidate`); the registry's `flood_model.joblib` is
  byte-identical to the local file (sha256 `7753568b…`) and loads with Moderate 0.00399 and High
  0.00706.
- Moved `champion` from version 3 to **version 4**. `models/flood_model.joblib` is now version 4;
  version 3 is archived as `models/archive/flood_model_v3_2026-10-18.joblib`.
- Wheel rebuilt from `e10a648` and uploaded; one poller run by hand (schedule kept **PAUSED**, no
  `python_params`) to refresh gold with version 4: run `1032780050111629`, 23 Oct 12:55–13:00 SGT,
  SUCCESS. The schedule was checked afterwards and is still PAUSED.
- Dashboard text (Moderate from 0.40%) deployed through the bundle (published 23 Oct 13:01 SGT);
  Genie instructions updated (12:56 SGT: "Moderate starts at 0.399%, High at 0.706%").

How it was done (same path as section 21), for the record:
- To register:
  `python databricks/register_model.py --model-path models/candidates/rule_rain60_refit_2026-10-22.joblib --summary models/candidates/rule_rain60_refit_2026-10-22.json --alias candidate`.
  The `.joblib` is gitignored; regenerate it on `f467db8` with
  `python -m floodsense.models.refit_champion --date 2026-10-22`.
- To promote: move `champion` to the new version, rebuild and upload the wheel, run the pipeline
  once, replace `models/flood_model.joblib` and archive version 3 under `models/archive/`.
- To roll back: move `champion` to version 3, restore `models/archive/flood_model_v3_2026-10-18.joblib`
  as `models/flood_model.joblib`, and change the dashboard and Genie text back to 0.53%.

## 26. "Ask FloodSense": Genie in the app (23 Oct 2026, not deployed yet)

The app's top-right "Ask FloodSense" panel sends questions to the Genie space **FloodSense risk
(gold)** (`01f1cd8bf8de1d5fba454efa5909044a`) through `floodsense.serving.genie`. Genie reads the
gold table, so it answers about live data only, also in Replay (the panel says so).

- **Config:** a fifth app resource, `genie-space` (CAN_RUN), in `databricks/app/app.json` and its
  bundle mirror `databricks/bundle/app.yml`; `app.yaml` passes it as `GENIE_SPACE_ID`. Locally the
  space ID comes from `settings.genie_space_id` (`FLOODSENSE_GENIE_SPACE_ID` overrides it) and the
  CLI profile signs in.
- **Permissions:** the app's service principal also needs CAN_USE on the warehouse the Genie space
  runs on and SELECT on gold; both are already app resources if the space uses the same warehouse.
  Check this in the space's settings before deploying.
- **Checked locally:** a live question ("Top 5 areas by flood probability right now") returned a
  text answer, a table and the SQL, in about 40 s.

Steps to deploy (not run yet; needs Akul's go-ahead):

1. Add the resource to the existing app: in the workspace, open **Compute > Apps > floodsense >
   Edit**, add a **Genie space** resource named `genie-space`, pick "FloodSense risk (gold)",
   permission **Can run**, and save. (`apps create` in section 16 only applies to a new app.)
2. Upload the source and deploy, as in section 16 steps 2 and 3.
3. Open the app, click **Ask FloodSense** and ask an example question. If it says "Genie couldn't
   answer", run `databricks apps logs floodsense` and look for the `Genie unavailable` line.
4. Stop the app when done: `databricks apps stop floodsense`.

## 27. Telegram listener for fast replies and /status (25 Oct 2026, branch `feat/telegram-listener`, merged to master, NOT deployed)

**Built, not deployed.** Nothing on Databricks has changed for this yet. The code is merged to master
(25 Oct). Akul deploys it with the steps below.

What it is: a second bot process, `python -m floodsense.alerts.telegram_bot listen`, that keeps
asking Telegram for new messages (long polling) and answers `/subscribe`, `/unsubscribe`, `/list`,
`/help` and the new `/status` within seconds. `/status` shows the current tier for the areas you
follow, or `/status <area>` for one. "No data" is shown as no reading, and a reading older than 60
minutes shows as "No recent reading" (the poller updates it every 30 minutes).

What changes with it:

- **Who reads messages:** only the listener. The poller's `telegram_alerts` task gets
  `--alerts-only`, so it sends alerts to the channel and subscribers but never reads messages and
  never writes the subscriptions file. Chats that blocked the bot (HTTP 403) are written to
  `telegram_blocked.json` beside the subscriptions file, and the listener removes them.
- **Job:** `floodsense-telegram-listener` in `databricks/bundle/jobs.yml`. It is continuous and
  **PAUSED**. It runs on always-on serverless compute, so unpause it only around the demo.
- **Alerts are unchanged:** they still arrive only when the poller runs (every 30 minutes), because
  the risk data only changes then. The listener makes replies fast, not alerts.

**Warning:** do not run the listener **and** an old poller `telegram_alerts` task without
`--alerts-only` at the same time. Both would read the same messages, take each other's replies, and
the subscriptions file would be written from two places. When two readers run, Telegram answers the
second with HTTP 409, and the listener logs that as an error.

Steps for Akul to deploy (these change the workspace, so they need Akul's go-ahead):

1. Pull master (the branch is already merged). Build the wheel as in the earlier
   sections.
2. Upload the new wheel and the updated task script, as described in section 22 (the wheel, then
   the script):
   `databricks workspace import /Workspace/Users/<login>/floodsense/telegram_bot.py --file databricks/jobs/telegram_bot.py --format AUTO --overwrite`
3. Run `databricks bundle validate -p akul`, then `databricks bundle deploy -p akul`.
4. Check that the poller's `telegram_alerts` task now has `--alerts-only`: in Workflows open
   `floodsense-rainfall-poller`, then the `telegram_alerts` task, and look at the parameters.
5. Check that the listener job exists and is **PAUSED**: in Workflows open
   `floodsense-telegram-listener`.
6. Check that no webhook is set on the bot (a webhook blocks the listener), as in section 23 step 5.
7. Unpause the listener for the demo: in Workflows open `floodsense-telegram-listener` and choose
   **Unpause**. Wait for the run to show as running.
8. Test from a phone: in Telegram send `/subscribe Bukit Timah`, then `/status`, then `/status Bukit
   Timah`. Each reply should arrive within about a minute.
9. After the demo, pause the listener again in Workflows (**Pause**). Confirm it shows PAUSED.
   While it is paused nobody answers messages: the poller no longer reads them. Telegram keeps unread
   messages for about 24 hours, so messages sent in the meantime are answered when it is
   unpaused only if that is within 24 hours; older ones are lost.

Run it locally instead (for example on the demo laptop, with the local `.env` set up as in section
23). Stop it with Ctrl+C:

```bash
uv run python -m floodsense.alerts.telegram_bot listen --subscriptions-file data/processed/telegram_subscriptions.json --state-file data/processed/telegram_state.json
```

Do not run the local listener while the listener job is unpaused on Databricks, and do not run it
together with a poller that lacks `--alerts-only`.

**Optional: set the bot's command menu by hand (BotFather).** This only changes the menu that
Telegram shows when people type "/". It does not change what the bot does.

1. In Telegram, open the chat with @BotFather.
2. Send `/setcommands`.
3. Choose the bot (@fs_tele_alert_bot).
4. Send these lines as one message:

```
subscribe - get alerts for a planning area
unsubscribe - stop alerts for an area, or all
list - show the areas you follow
status - current flood risk for your areas
help - show what the bot does
```

Not rehearsed yet: not yet run against the real Telegram bot or on Databricks.
