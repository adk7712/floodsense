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

- **Poller on a live schedule**: the job exists and has run (section 11), but its schedule is paused
  to protect the daily compute cap.
- **Databricks App**: not deployed yet. The Streamlit app reads gold when run locally (section 15).
- **Serving from the registry**: gold still loads the committed `models/flood_model.joblib`, not the
  registered model (section 10).

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
- Experiment `/Users/akul.sharma009@gmail.com/floodsense-model` (the `floodsense` name is taken by the
  workspace folder), run `85df4e2d536f43d4a4df1663f1f5c14a`.
- Registered as **`workspace.floodsense.flood_model` version 1, alias `champion`** (status READY).
  Each re-run adds a version and moves `champion`.
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
databricks jobs create --json @databricks/jobs/poller_job.json   # after filling in <login>
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
databricks jobs create --json @databricks/jobs/radar_archive_job.json   # after filling in <login>
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
databricks jobs reset --json '{"job_id": 486214899373176, "new_settings": <radar_archive_job.json, login filled in>}'
databricks jobs create --json @databricks/jobs/flood_alerts_job.json   # after filling in <login>
# backfill locally, then upload pages and markers
python databricks/jobs/archive_rainfall.py --out-dir <dir>/daily --state-dir <dir>/state --start 2026-10-01
databricks fs cp -r <dir>/daily dbfs:/Volumes/workspace/floodsense/landing/rainfall/daily
databricks fs cp -r <dir>/state dbfs:/Volumes/workspace/floodsense/landing/archive_state/rainfall
```

## 15. The app reads the gold table (A2, 16 Oct 2026)

Live mode shows the risk the pipeline scored, from `flood_risk_predictions_gold`, by default. A
sidebar switch, **Data source: Databricks | data.gov.sg**, keeps the old path (the app scoring the
API itself) as the fallback.

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
- **Freshness:** the page shows the data's age next to the latest reading. If gold is older than
  60 minutes, an orange warning names its age, with a **Use data.gov.sg readings** button. If gold
  can't be read, a warning gives the reason and the app falls back to data.gov.sg.
- **Cost:** results are cached for 15 minutes (half the poller's interval), so each viewer queries
  the warehouse at most 4 times an hour. A cold warehouse takes about 25 s to answer.
- **Checked on 16 Oct:** 55 zones read from the workspace, last scored 6 Oct 17:35 SGT (the poller
  is paused), so the page showed "9 days old" with the warning. The switch and the button were
  checked in light and dark mode.

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
