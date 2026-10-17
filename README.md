# FloodSense

FloodSense estimates zone-level flash-flood risk for Singapore's 55 URA planning areas, one hour ahead. It ingests 5-minute NEA rainfall gauge readings from data.gov.sg, interpolates them to planning areas with inverse-distance weighting, derives rainfall-accumulation and storm-rarity features, and gives each zone a calibrated chance of a reported flood in the next hour. A Streamlit dashboard shows the result as a risk map, live or replayed for any day since 2017, with the major reported storms one click away. Built for the DAISI Challenge 2026, Track B2.

> **Status: working prototype on real data, running on Databricks.**
> - **Model:** a calibrated 60-minute-rainfall rule (`models/flood_model.joblib`, registered in Unity
>   Catalog as `workspace.floodsense.flood_model@champion`, version 3). It beat logistic regression,
>   LightGBM and a static-feature variant at matched false-alarm levels
>   (`docs/candidate_selection_2026-10.md`). Moderate from **0.53%** (about 38 mm in 60 min), High from
>   **0.71%** (about 43 mm), set by the false-alarm budget rule on 88 verified floods (18 Oct).
>   In 2020–2023 cross-validation (28 floods) it catches 14 at High (90% CI 36–64%) and 18 at
>   Moderate (50–79%), at 2.5 and 4.5 false alarms per zone-year.
> - **Test result** (the previous model, version 1: same rule, Moderate from 0.30%, trained on 66
>   labels): scored once on held-out floods from 2024 to Sep 2026 (30 floods):
>   - High alerts caught 12 (40%), median warning 10 min, 1.6 false alarms per zone-year
>   - Moderate alerts caught 21 (70%), median warning 15 min, 7.3 false alarms per zone-year
>
>   Details and 90% intervals are in `models/final_report.json` and `models/model_card.json`, which
>   describe version 1 (kept as `models/archive/flood_model_v1_2026-10-01.joblib`). The current
>   model hasn't been scored on the test years; doing so would be a "second look".
> - **Databricks** (Free Edition, `DEPLOYMENT.md`):
>   - a Lakeflow pipeline (bronze → silver → gold) scores every zone every 5 minutes with the model
>     loaded from Unity Catalog; it reproduces local scoring on the 17 April 2021 storm
>   - Lakeflow Jobs: a rainfall poller (lands NEA readings and PUB alerts, then runs the pipeline), a
>     daily archiver (radar frames and gauge readings) and a 15-minute PUB-alert saver
>   - the app runs as a Databricks App and reads gold through a SQL warehouse; an AI/BI dashboard
>     reads gold too
>   - every table, volume, model and job carries a description and tags (`DEPLOYMENT.md` §18)
>   - the architecture diagram is in `docs/architecture/`
> - **App:** a live risk map for 55 planning areas, replay of any day since 2017, live PUB flood
>   alerts, MRT stations at risk, and a Resident view (your area, the risk now and in an hour, what
>   to do). Light and dark themes.
> - **Radar** (data.gov.sg rain areas, archived daily from 16 Jul 2026): colours calibrated to mm/h
>   against NEA gauges (`docs/radar_calibration.md`), and an optical-flow nowcast that beats
>   persistence (`docs/radar_nowcast_result.md`). The app shows it as a map overlay (now, +30, +60
>   min) and as "risk in 1 hour".
> - **Telegram alerts:** a bot that posts when a zone's risk rises (`floodsense.alerts`), with a
>   replay mode; going live with the poller.
> - **Not built:** tide, and PUB drainage capacity (not openly available).

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
  app/          Streamlit app (Analyst and Resident views, Replay)
  alerts/       Telegram alert bot and the shared advice wording
  common/       Pydantic schemas and central config (config.py)
  data/         Rainfall store (build and load), sourced flood events, replay files
  features/     Rolling-rainfall features, storm rarity, the feature store
  ingestion/    NEA rainfall poller, PUB flood alerts
  labels/       How flood events become training labels
  models/       Training, evaluation, backtest, model artifact and registry loading,
                candidate selection
  radar/        Radar decoding, calibration, optical-flow nowcast, app radar layer
  serving/      Pandas core shared by the app and the Databricks pipeline; gold-table reader
  spatial/      Station and planning-area geometry, IDW and kriging, static zone features
tests/          pytest suite (unit, integration, e2e)
models/         Trained model, model card, final test-set report, candidate results
data/raw/rainfall/  NEA 5-minute rainfall store, 2017 onward (~18 MB Parquet, committed)
data/replay/    Real NEA readings for the 17 April 2021 storm (with 72 h warm-up)
data/reference/ Station snapshot, URA planning-area polygons, flood events, static zone
                features, MRT exits, radar colour scale, and the Databricks parity export
databricks/     Lakeflow pipeline and spec, jobs, Databricks App config, dashboard,
                model registration, parity export, local Spark runner
docs/           Architecture diagram, radar and kriging results, model selection,
                flood-label reviews and rejections, team handoff
app.yaml        Databricks App entry point
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

### Flood-prone areas (`data/reference/pub_flood_prone_areas.csv`)

PUB's annual flood-prone land in hectares, 2022–2025 (data.gov.sg `d_c4aed98f1533eb3a66f65dbb1a30da46`),
fetched by `uv run python -m floodsense.data.flood_prone`, with the source on every row. The app
shows it as a trend. A missing file is reported, never filled in.

### PUB flood alerts (real-time)

data.gov.sg `v2/real-time/api/weather/flood-alerts`, parsed by `floodsense.ingestion.flood_alerts`
(each alert is mapped to the URA planning area containing its point; a `Cancel` closes its alert).
Past dates come back empty: 30 Sep 2026, a day with a PUB-reported flood, returned 720 empty
records. So the alerts can't be training labels yet. The Databricks poller archives them from 6 Oct
2026 on.

### Static zone features (`data/reference/zone_static.csv`)

One row per planning area (55 rows): `built_up_pct`, `elevation_m_mean` and `low_lying_pct`, each row
carrying `source_name`, `source_url` and `retrieved_at`. Built by
`uv run --extra static python -m floodsense.spatial.build_zone_static --retrieved-at <date>` from two
rasters downloaded by hand into `data/raw/static/` (gitignored). A pixel counts for a zone when its
centre is inside the Master Plan 2019 polygon. A zone with no valid pixel stops the build.

- **Elevation:** Copernicus GLO-30 DEM (30 m). It is a **surface model (DSM)**, so buildings and
  trees are included; read it as a relative terrain index, not ground level. Heights are metres
  above the EGM2008 geoid. `low_lying_pct` is the share of pixels under 5 m (a descriptive cut, not
  tuned on flood labels).
  - Licence: Copernicus WorldDEM-30 licence (free and open; attribution and no-liability notice required).
  - Attribution (from the licence, Article 6): "produced using Copernicus WorldDEM-30 © DLR e.V.
    2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the
    European Union and ESA; all rights reserved."
  - "The organisations in charge of the Copernicus programme by law or by delegation do not incur
    any liability for any use of the Copernicus WorldDEM-30."
  - Also listed on the AWS Registry of Open Data: "Copernicus Digital Elevation Model (DEM) ...
    accessed on 2026-10-16 from https://registry.opendata.aws/copernicus-dem". The exact wording of
    the registry's own citation line is UNVERIFIED (not re-read at download time).
  - Tiles: `Copernicus_DSM_COG_10_N01_00_E103_00_DEM` and `..._E104_00_DEM`.
- **Built-up share:** ESA WorldCover 2021 v200 (10 m), class 50 (Built-up) as a share of valid pixels.
  - Licence: CC BY 4.0.
  - Attribution: "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021)
    processed by ESA WorldCover consortium".
  - Citation: Zanaga, D. et al. (2022). ESA WorldCover 10 m 2021 v200. https://doi.org/10.5281/zenodo.7254221
  - Tile: `ESA_WorldCover_10m_2021_v200_N00E102_Map`.

### Flood events (`data/reference/flood_events.csv`)

80 verified events, 2017 to Sep 2026, across 32 planning areas: 50 in 2017–2023 (training and
cross-validation) and 30 in 2024–2026 (the test set). 11 more candidates are drafted and waiting for
their second check. Each row has:
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
- **Warning time.** The median is over floods with a reported time. Date-only reports count as
  hits but carry no warning time, since the flood could have started any time that day.
- **Metrics.** The false-alarm *ratio* FP/(FP+TP), event hit rate and lead time, and false-alarm
  *episodes* per zone-year, alongside PR-AUC and Brier score. Calibration is cross-fitted, so it is
  never scored on the data it was fitted to.
- **Thresholds.** High and Moderate thresholds are chosen under a false-alarm budget of 2.5 and 11
  false alert episodes per zone per year (`FLOODSENSE_FALSE_ALARM_BUDGET_HIGH`,
  `FLOODSENSE_FALSE_ALARM_BUDGET_MODERATE`). The team picked them from the validation trade-off
  table before the test set was scored. On the first 66 labels they gave Moderate 0.30% and High
  0.71% (version 1, the tested model). Re-applied to 88 verified labels on 18 Oct, the same rule
  gives Moderate 0.53% and High 0.71% (version 3, live): at 0.30%, Moderate had gone over its budget
  (11.3 false episodes per zone-year against 11). The budgets themselves never changed.

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

Running on Databricks is described in `DEPLOYMENT.md`. `requirements.txt` is generated from `uv.lock` for Databricks Apps; do not edit it by hand. Regenerate it with the exact command in its header, which includes the required `radar` extra (it keeps OpenCV for the Databricks App's radar view): `uv export --no-hashes --no-dev --extra radar --format requirements-txt -o requirements.txt`.
