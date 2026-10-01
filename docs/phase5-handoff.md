# Phase 5 handoff: FloodSense on Databricks

**For:** whoever builds Phase 5, and any coding assistant they use. Read all of it before writing code, and give your assistant this whole file as context.

**Goal:** the architecture in `floodsense-project-plan.pdf` §6 (also deck slide 3), running for real in a Databricks Free Edition workspace:

```
poller job → UC volume (landing) → ONE Lakeflow pipeline: bronze → silver → gold (scored) → app / dashboard
```

**Already done, and not to be redone:** real data, labels, the model, thresholds and evaluation (Phases 0–4). Phase 5 *moves* the existing computation onto Databricks. It does not re-create it.

---

## Ground rules (non-negotiable)

1. **No feature or scoring logic in Spark.** Everything that turns readings into risk is already in `src/floodsense/serving/pipeline_core.py` (pandas). Call it. Don't rewrite rolling windows, IDW or thresholds in PySpark or SQL; two copies of the logic will drift. The existing `databricks/pipelines/lakeflow_pipeline.py` is an old, never-run sketch that does exactly that, so replace it.
2. **Never fabricate data.** That means:
   - no synthetic fallback when the API fails
   - no placeholder rows, and no hand-made CSVs standing in for a pipeline run
   - if a step can't be done on Free Edition, **stop and write down why**; that's a valid result
3. **Missing is not dry.** A gauge with no reading at a 5-minute step is absent, not 0.0. `payloads_to_readings` already does this, so don't "clean" it.
4. **No secrets in git.** Databricks tokens and the data.gov.sg API key go in workspace secrets or a local `.env` (gitignored).
5. **Keep the repo's checks green:** `uv run ruff check . && uv run mypy && uv run pytest`.

## Getting started

```bash
git pull origin master
uv sync --all-extras --group dev            # macOS: brew install libomp first
uv run pytest tests/test_phase5_core.py     # 7 pass: the core you will call
uv run pytest tests/test_phase5_parity.py -rs   # 6 skipped: your scoreboard
```

**Done means** `tests/test_phase5_parity.py` passes with a real export committed, plus the human sign-off below.

---

## What the core gives you (`floodsense.serving.pipeline_core`)

| Function | Layer | What it does |
|---|---|---|
| `payloads_to_readings(payloads)` | silver | Raw API JSON (v1 or v2) → `readings` (`station_id, timestamp, rainfall_mm`, SGT) + `stations`. Invalid values are dropped; a bad payload raises `ValueError` (quarantine it). |
| `score_window(readings, stations, model, emit_from, emit_to)` | gold | Zone features + calibrated probability + Low/Moderate/High tier for each 5-minute step, columns `PREDICTION_COLUMNS`. Needs **72 h of readings before `emit_from`** (`WARMUP`). |
| `load_model()` (in `floodsense.models.artifact`) | gold | The committed trained model (`models/flood_model.joblib`), with the team's thresholds. |
| `export_replay_payloads(dir)` / CLI `export-replay` | landing | The 17 Apr 2021 replay as 949 API-shaped JSON files, named like live poller files. |
| `replay_predictions()` / CLI `expected` | reference | What gold must contain for the replay. |

The data is small: 72 h × about 70 gauges is about 60k readings. Converting a window to pandas inside the pipeline (or `applyInPandas` per batch) is fine on Free Edition.

## Steps

1. **Package the code:** `uv build` → wheel. Install it in the pipeline environment (pipeline dependencies, or `%pip install` the wheel from a UC volume). Both the code and `models/flood_model.joblib` must be in the workspace. Set `FLOODSENSE_ROOT_DIR` to a folder (e.g. a UC volume) holding copies of the repo's `models/` and `data/reference/` folders. `load_model()` reads `<root>/models/`, and the package reads the station list in `<root>/data/reference/` when it is imported.
2. **Landing:** create a UC volume, e.g. `/Volumes/<catalog>/floodsense/landing/`.
   - Poller job: `NEAPoller(landing_dir=...).stage_payload_to_volume(payload)` every 5–10 minutes, as a scheduled Lakeflow job (triggered, not continuous, to protect the daily quota).
3. **Bronze:** a streaming table from Auto Loader (`cloudFiles`, JSON) on the landing volume. Keep the raw payload text and `_rescued_data`; never fail on a bad file.
4. **Silver:** parse bronze with `payloads_to_readings`. Lakeflow expectations then:
   - drop rows with a null timestamp or a value outside 0–100
   - send payloads that raise `ValueError` to a quarantine table, not the floor
5. **Gold:** for the latest step, take readings from `[t − 72 h, t]` and run `score_window(..., emit_from=<new steps>)`. Write `PREDICTION_COLUMNS` to a gold table, keyed on (`ura_planning_area`, `timestamp`).
6. **Replay through the same path, which is the proof:**
   1. `uv run python -m floodsense.serving.pipeline_core export-replay --out replay_landing/`, then upload the 949 files into the landing volume.
   2. Run the pipeline (one triggered update).
   3. Export the gold rows for 17 Apr 2021 11:00–18:00 SGT to `data/reference/phase5/databricks_replay_predictions.csv`. Use exactly the gold columns, with ISO timestamps.
   4. Write `data/reference/phase5/run_info.json` with these fields, copied from the pipeline's event log or UI:
      - `workspace_host`, `pipeline_id`, `update_id`, `update_started_at`
      - `bronze_rows`, `silver_rows`, `gold_rows`
      - `landing_files` (must be 949)
   5. Run `uv run pytest tests/test_phase5_parity.py`. It must pass:
      - same rows
      - features and probabilities within 1e-6
      - identical tiers
      - Bukit Timah High at 12:45
7. **Model registry:** log the same artifact to MLflow and register it in Unity Catalog. Then score the replay through the *registered* model and check the tiers still match.
8. **Front end:** a Databricks App (Streamlit, `src/floodsense/app/streamlit_app.py`) or an AI/BI dashboard reading the gold table. Apps auto-stop after 24 h; record whether a scheduled job can restart one.
9. **Update `DEPLOYMENT.md`** to describe what you actually ran, replacing the old copy-paste guide.

## Questions to answer in the PR (plan §9)
- Does `ai_query` work on our Free Edition workspace, and with which models?
- Does Auto Loader plus a streaming table fit within the one-pipeline limit?
- Can a scheduled job restart a Databricks App?
- How much of the daily compute quota does one triggered update use?

## Handing back
Open a PR into `master` containing:
- the pipeline code
- `DEPLOYMENT.md`
- the two `data/reference/phase5/` files
- the answers above
- output of `uv run pytest tests/test_phase5_parity.py -v`
- screenshots of the pipeline graph and the gold table

**Sign-off:** the PR is merged only after Akul opens the workspace and confirms that the `update_id` in `run_info.json` exists and shows those row counts. A passing test on a CSV alone is not enough, because a CSV can be produced on a laptop.
