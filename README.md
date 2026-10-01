# FloodSense

FloodSense estimates zone-level flash-flood risk for Singapore's 55 URA planning areas, one hour ahead. It ingests 5-minute NEA rainfall gauge readings from data.gov.sg, interpolates them to planning areas with inverse-distance weighting, derives rainfall-accumulation and storm-rarity features, and scores each zone with a classifier. A Streamlit dashboard shows the result as a risk map, with a replay of the 17 April 2021 western Singapore storm. Built for the DAISI Challenge 2026, Track B2.

> **Status: prototype.** The bundled model (`models/champion_model.joblib`) was trained on **synthetic** rainfall and labels, so its reported metrics are not meaningful and must not be read as real-world skill. Training on real historical data is in progress: the data work is specified in [docs/phase3-handoff.md](docs/phase3-handoff.md), with acceptance tests in `tests/test_phase3_contract.py`.

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
  data/         Historical replay fetcher and synthetic dataset generator
  features/     Rolling-rainfall features, storm rarity, ground-truth labels
  ingestion/    NEA rainfall poller
  models/       Training and evaluation
  spatial/      Station and planning-area geometry, IDW interpolation
tests/          pytest suite (unit, integration, e2e)
models/         Bundled demo model and metadata
data/replay/    Real NEA readings for the 17 April 2021 storm (with 72 h warm-up)
data/reference/ Snapshot of NEA rainfall-station metadata from data.gov.sg
databricks/     Lakeflow pipeline for Databricks deployment
docs/           Supporting documents
```

Configuration (risk thresholds, feature columns, timing constants, API URLs, paths) lives in `src/floodsense/common/config.py` and can be overridden with `FLOODSENSE_*` environment variables.

## Data

- **Live mode** calls the data.gov.sg real-time rainfall API (v2). Anonymous use is rate-limited to a
  few calls per ~10 s; set `FLOODSENSE_DATA_GOV_API_KEY` to use a key. If the API is unavailable the
  app says so and shows nothing. It never substitutes simulated rain.
- **Replay mode** uses `data/replay/2021-04-17_western_storm.json`: real 5-minute readings from
  11:00 to 18:00 SGT on 17 April 2021, plus the 72 hours before, so rolling and wet-ground features
  are fully formed. To rebuild it, or build another storm:

  ```bash
  uv run python -m floodsense.data.replay --event-name "17 April 2021 western Singapore flash floods" \
      --start 2021-04-17T11:00 --end 2021-04-17T18:00
  ```

- Station names and coordinates come with each API response. `data/reference/nea_rainfall_stations.json`
  is a fallback snapshot, used for example when exporting IDW weights.

## Training and evaluation

These commands need the Phase 3 data (the historical rainfall store and sourced flood events; see
[docs/phase3-handoff.md](docs/phase3-handoff.md)). Until it exists they stop with a pointer to that
document. They never fall back to synthetic data.

```bash
uv run python -m floodsense.features.build_features --start-year 2017 --end-year 2026
uv run python -m floodsense.models.train             # CV, selection, calibration; prints the trade-off table
uv run python -m floodsense.models.train --final-report   # scores 2024+ once (needs budgets, see below)
uv run python -m floodsense.models.backtest --replay data/replay/2021-04-17_western_storm.json \
    --model legacy --event "BUKIT TIMAH|2021-04-17T13:30|approx_hour"
```

How the numbers are kept honest:

- **Labels.** A row is positive if a flood *starts* within the next 60 minutes. Rows where the zone
  may already be flooding are excluded. Imprecise report times give partial labels instead of
  invented exact times (`src/floodsense/labels/policy.py`).
- **Validation.** Forward-chaining cross-validation by year, with rarity curves refitted inside each
  fold. 2024 onwards is the test set and is scored once, by `--final-report`.
- **Metrics.** The false-alarm *ratio* FP/(FP+TP), event hit rate and lead time, and false-alarm
  *episodes* per zone-year, alongside PR-AUC and Brier score. Calibration is cross-fitted, so it is
  never scored on the data it was fitted to.
- **Thresholds.** High and Moderate thresholds are chosen under a false-alarm budget
  (`FLOODSENSE_FALSE_ALARM_BUDGET_HIGH`, `FLOODSENSE_FALSE_ALARM_BUDGET_MODERATE`; false alert
  episodes per zone per year). The budgets are deliberately unset: pick them from the trade-off table
  that training prints.

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

Deployment to Databricks is described in `DEPLOYMENT.md`. `requirements.txt` is generated from `uv.lock` for Databricks Apps; do not edit it by hand (`uv export --no-hashes --no-dev --format requirements-txt -o requirements.txt`).
