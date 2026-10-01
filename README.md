# FloodSense

FloodSense estimates zone-level flash-flood risk for Singapore's 55 URA planning areas, one hour ahead. It ingests 5-minute NEA rainfall gauge readings from data.gov.sg, interpolates them to planning areas with inverse-distance weighting, derives rainfall-accumulation and storm-rarity features, and scores each zone with a classifier. A Streamlit dashboard shows the result as a risk map, with a replay of the 17 April 2021 western Singapore storm. Built for the DAISI Challenge 2026, Track B2.

> **Status: prototype.** The bundled model (`models/champion_model.joblib`) was trained on **synthetic** rainfall and labels, so its reported metrics are not meaningful and must not be read as real-world skill. Training on real historical data is in progress.

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
data/replay/    Cached 17 April 2021 storm observations
databricks/     Lakeflow pipeline for Databricks deployment
docs/           Supporting documents
```

Configuration (risk thresholds, feature columns, timing constants, API URLs, paths) lives in `src/floodsense/common/config.py` and can be overridden with `FLOODSENSE_*` environment variables.

## Development

```bash
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy                  # type check
uv run pre-commit install    # run ruff and file hygiene hooks on commit
```

CI (`.github/workflows/ci.yml`) runs lint, format check, mypy and both pytest selections on every push and pull request.

Deployment to Databricks is described in `DEPLOYMENT.md`. `requirements.txt` is generated from `uv.lock` for Databricks Apps; do not edit it by hand (`uv export --no-hashes --no-dev --format requirements-txt -o requirements.txt`).
