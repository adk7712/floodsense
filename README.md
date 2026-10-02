# FloodSense

FloodSense estimates zone-level flash-flood risk for Singapore's 55 URA planning areas, one hour ahead. It ingests 5-minute NEA rainfall gauge readings from data.gov.sg, interpolates them to planning areas with inverse-distance weighting, derives rainfall-accumulation and storm-rarity features, and gives each zone a calibrated chance of a reported flood in the next hour. A Streamlit dashboard shows the result as a risk map, live or replayed for any day since 2017, with the major reported storms one click away. Built for the DAISI Challenge 2026, Track B2.

> **Status: working prototype on real data.**
> - **Model:** a calibrated 60-minute-rainfall rule (`models/flood_model.joblib`). It beat logistic regression and LightGBM at matched false-alarm levels.
> - **Data:** trained on NEA rainfall 2017–2023 and 66 sourced flood events. It was scored once on held-out floods from 2024 to Sep 2026 (30 floods):
>   - High alerts caught 12 (40%), median warning 7.5 min, 1.6 false alarms per zone-year
>   - Moderate alerts caught 21 (70%), median warning 15 min, 7.3 false alarms per zone-year
>
>   Details are in `models/final_report.json` and `models/model_card.json`.
> - **Databricks:** the pipeline runs on Databricks Free Edition and reproduces local scoring on the 17 April 2021 storm (`DEPLOYMENT.md`).
> - **Not built yet:** radar nowcasting, tide, and live scheduling on Databricks.

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras --group dev      # install runtime + dev deps (+ mlflow for training)
uv run streamlit run src/floodsense/app/streamlit_app.py
uv run pytest                         # unit tests, fully offline
uv run pytest -m "integration and not network"   # in-process Streamlit AppTest tests
```

End-to-end browser tests (Playwright) need the app running on `localhost:8501`:

```bash
uv run --group e2e playwright install chromium
uv run streamlit run src/floodsense/app/streamlit_app.py &    # in another terminal
uv run --group e2e pytest -m e2e
```

Tests that call the live data.gov.sg API are marked `network` and are excluded by default (`uv run pytest -m network` to run them).

On macOS, LightGBM needs OpenMP (`brew install libomp`).

## Repository layout

```
src/floodsense/
  app/          Streamlit dashboard
  common/       Pydantic schemas and central config (config.py)
  data/         Rainfall store (build and load), sourced flood events, replay files
  serving/      Pandas core shared by the app and the Databricks pipeline
  features/     Rolling-rainfall features, storm rarity, the feature store
  labels/       How flood events become training labels
  ingestion/    NEA rainfall poller
  models/       Training, evaluation, backtest, model artifact
  spatial/      Station and planning-area geometry, IDW interpolation
tests/          pytest suite (unit, integration, e2e)
models/         Trained model, model card and final test-set report
data/raw/rainfall/  NEA 5-minute rainfall store, 2017 onward (~18 MB Parquet, committed)
data/replay/    Real NEA readings for the 17 April 2021 storm (with 72 h warm-up)
data/reference/ Station snapshot, URA planning-area polygons, sourced flood events,
                and the Databricks parity export (phase5/)
databricks/     Lakeflow pipeline, its spec, the parity export script, a local Spark runner
docs/           Flood reports examined and excluded, with reasons
submission/     Round 1 slide brief and demo video script
```

Configuration (risk thresholds, false-alarm budgets, timing constants, API URLs, paths) lives in `src/floodsense/common/config.py` and can be overridden with `FLOODSENSE_*` environment variables.

## Data

Rules for every input:
- **Nothing fabricated:** no synthetic fallbacks or placeholder rows; missing data fails loudly or stays a gap.
- **Nothing hand-typed when a source exists.**
- **Times are timezone-aware Singapore time.**

- **Live mode** calls the data.gov.sg real-time rainfall API (v2). Anonymous use is rate-limited to a
  few calls per ~10 s; set `FLOODSENSE_DATA_GOV_API_KEY` to use a key. If the API is unavailable the
  app says so and shows nothing. It never substitutes simulated rain.
- **Replay mode** replays any day in the committed rainfall store. Pick a date, or one of the storms
  with the most reported floods. Days missing from NEA's record are flagged, never shown as dry.
  Without the store it falls back to `data/replay/2021-04-17_western_storm.json`: real 5-minute
  readings from 11:00 to 18:00 SGT on 17 April 2021, plus the 72 hours before, so rolling and
  wet-ground features are fully formed. To rebuild it, or build another storm:

  ```bash
  uv run python -m floodsense.data.replay --event-name "17 April 2021 western Singapore flash floods" \
      --start 2021-04-17T11:00 --end 2021-04-17T18:00
  ```

### Rainfall store (`data/raw/rainfall/`)

60.8M NEA 5-minute readings, 2017 to Sep 2026, built from NEA's own data:

| Period | Source | Step |
|---|---|---|
| 2017–2024 | data.gov.sg collection 2279, "Historical Rainfall across Singapore" (one CSV per year) | `download`, `convert` |
| 2025 onwards | the real-time API's `?date=` history (the same API the app uses) | `backfill` |
| Sparse days in the CSVs (158 days under 90% coverage, most of August 2018) | the same API; CSV readings win where both exist | `gapfill` |

```bash
uv run python -m floodsense.data.build_rainfall_store download   # ~9 GB of CSVs, kept local
uv run python -m floodsense.data.build_rainfall_store convert    # -> readings/year=YYYY/part-bulk.parquet, stations.parquet
uv run python -m floodsense.data.build_rainfall_store backfill   # -> readings/year=YYYY/part-api-YYYY-MM.parquet
uv run python -m floodsense.data.build_rainfall_store gapfill    # -> readings/year=YYYY/part-api-gapfill.parquet
```

- Every step is resumable, and `manifest.json` records sources, checksums and row counts.
- `convert`:
  - snaps early records stamped one second early (`09:59:59`) onto the 5-minute grid
  - keeps only the `TB1 Rainfall 5 Minute Total F` series in mm
  - drops values outside 0–100 mm and exact duplicates, counting each
- **Missing is not dry.** A station with no row at a step didn't report, and nothing fills it in;
  interpolation re-weights over the gauges that did.
- Station locations come from the data itself. A station that moved gets one row per location
  (`valid_from` / `valid_to`).
- The 17 Apr 2021 CSV data matches the API replay exactly, up to the API's 2-decimal rounding.
- Interface: `floodsense.data.rainfall_store` (`read_rainfall`, `load_station_table`,
  `stations_at`, `load_snapshots`). Raw CSVs, sightings and the manifest stay local (gitignored).

### Planning areas

`data/reference/ura_planning_areas_mp2019.geojson`: URA Master Plan 2019 Planning Area Boundary (No
Sea), data.gov.sg `d_4765db0e87b9c86336792efe8a1f7a66`, fetched by
`uv run python -m floodsense.spatial.download_ura_polygons`. Each zone's reference point is its
polygon's `representative_point()`.

### Flood events (`data/reference/flood_events.csv`)

66 events, 2017 to Sep 2026, across 27 planning areas. Each row has:
- a `source_url` and `source_name`
- an `evidence_quote` copied from the source, naming the place and time
- a `time_precision` (`exact`, `approx_15min`, `approx_hour` or `day_only`)

Candidates can be drafted by a person or an LLM. A person then opens each source, checks the quote, place and time, and signs the row in `verified_by`. `load_flood_events()` returns only signed rows, so an unchecked row never becomes a label. Reports examined and excluded are listed with reasons in `docs/flood_events_rejected.md`.

### Data contract (`tests/test_phase3_contract.py`)

Checks what the data *contains*, not just its shape:
- every year is present and well covered
- realistic annual totals and wet-day shares
- no year copied into another
- the store matches the 17 Apr 2021 API replay
- the event columns and sign-offs are valid

With `-m network` it also checks the store against the live API on random evenings, and that every event link opens and mentions the place. The rainfall tests skip where the store is absent; the event tests always run.

## Training and evaluation

```bash
uv run python -m floodsense.features.build_features --start-year 2017 --end-year 2026
uv run python -m floodsense.models.train             # CV, selection, calibration; prints the trade-off table
uv run python -m floodsense.models.train --final-report   # scores 2024+ once with the saved model
uv run python -m floodsense.models.backtest --replay data/replay/2021-04-17_western_storm.json
```

The feature store (`data/processed/features/`) is local and rebuilt from the rainfall store. The commands stop with a pointer here if the data is missing; they never fall back to synthetic data.

How the numbers are kept honest:

- **Labels.** A row is positive if a flood *starts* within the next 60 minutes. Rows where the zone
  may already be flooding are excluded. Imprecise report times give partial labels instead of
  invented exact times (`src/floodsense/labels/policy.py`).
- **Validation.** Forward-chaining cross-validation by year, with rarity curves refitted inside each
  fold. The candidate is chosen on 2020–2023. 2024 onwards is the test set and is scored once, by
  `--final-report`.
- **Metrics.** The false-alarm *ratio* FP/(FP+TP), event hit rate and lead time, and false-alarm
  *episodes* per zone-year, alongside PR-AUC and Brier score. Calibration is cross-fitted, so it is
  never scored on the data it was fitted to.
- **Thresholds.** High and Moderate thresholds are chosen under a false-alarm budget of 2.5 and 11
  false alert episodes per zone per year (`FLOODSENSE_FALSE_ALARM_BUDGET_HIGH`,
  `FLOODSENSE_FALSE_ALARM_BUDGET_MODERATE`). The team picked them from the validation trade-off
  table before the test set was scored. They give a Moderate threshold of 0.30% and a High
  threshold of 0.71%.

Runs are logged to MLflow (`sqlite:///mlflow.db` by default; `uv run mlflow ui --backend-store-uri
sqlite:///mlflow.db`).

## Development

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy                  # type check
uv run pre-commit install    # run ruff and file hygiene hooks on commit
```

CI (`.github/workflows/ci.yml`) runs lint, format check, mypy and both pytest selections on every push and pull request.

Running on Databricks is described in `DEPLOYMENT.md`. `requirements.txt` is generated from `uv.lock` for Databricks Apps; do not edit it by hand (`uv export --no-hashes --no-dev --format requirements-txt -o requirements.txt`).
