# Phase 3 handoff: real data for FloodSense

**For:** whoever picks up Phase 3, and any coding agent they use. Read all of it before writing code.

**Goal:** replace every synthetic or hand-typed input with real, sourced data, so that Phase 4 (labels, training, evaluation) can produce honest numbers. Phase 3 delivers **data and loaders only**.

---

## Why this matters

The model currently bundled was trained on rainfall from `generate_historical_dataset()`. That function gives known flood days heavier synthetic storms, so the label is effectively built into the inputs, and every reported metric is circular. In the 17 Apr 2021 replay, at the storm peak (12:15), the app rates Bukit Timah as *Low, 4.5%*, just before Dunearn Road flooded. Phase 3 supplies what is needed to fix that.

## Ground rules (non-negotiable)

1. **Never fabricate data.** That means no synthetic fallbacks, no random values when a download fails, and no placeholder rows. If data is missing, fail loudly or leave a gap that is clearly marked.
2. **Nothing hand-typed when a source exists.** Coordinates, boundaries and events must come from a file or API, and the code that produced them must be in the repo.
3. **Every flood event has a `source_url`.** If an event can't be sourced, drop it.
4. **Raw data stays out of git.** `data/raw/` is gitignored. Small reference files (`data/reference/`) are committed.
5. **Timestamps are timezone-aware Singapore time** (`Asia/Singapore`) everywhere. Use `floodsense.common.timeutil.to_sgt`.
6. **Don't change the interfaces** in `src/floodsense/data/rainfall_store.py` and `src/floodsense/data/flood_events.py`. Phase 4 calls them. Implement them as written.

## Getting started

```bash
git checkout remediation/phase-0-2 && git checkout -b phase3/real-data
uv sync --all-extras --group dev
uv run pytest                                  # all green, or skipped / xfail
uv run pytest tests/test_phase3_contract.py -rsx   # your scoreboard: 7 skipped, 9 xfail today
```

On macOS, LightGBM needs OpenMP: `brew install libomp`.

**Done means** every test in `tests/test_phase3_contract.py` passes on your machine with the data present, the rest of the suite still passes, and CI is green.

---

## Code you should reuse, not rewrite

| What | Where | Notes |
|---|---|---|
| API client with pagination, retries and an optional API key | `NEAPoller.fetch_range(start, end)` in `src/floodsense/ingestion/poller.py` | Returns `RainfallSnapshot`s. Anonymous calls are rate-limited to a few per 10 s; set `FLOODSENSE_DATA_GOV_API_KEY` for backfills. |
| Payload parser (v1 and v2 API shapes) | `parse_rainfall_payload` (same file) | |
| Snapshot schema | `RainfallSnapshot`, `StationMetadata`, `FloodEvent` in `src/floodsense/common/schemas.py` | |
| Zone features from snapshots | `compute_zone_feature_table` in `src/floodsense/features/zone_features.py` | Builds its own contiguous 5-minute grid |
| Verified real reference data | `data/replay/2021-04-17_western_storm.json` (`load_replay`) | Fetched from the API; the contract compares your store against it |
| Paths and settings | `settings` in `src/floodsense/common/config.py` | `rainfall_dir`, `rainfall_readings_dir`, `rainfall_stations_file`, `zone_polygons_file`, `flood_events_file` |

---

## Deliverable (a): historical rainfall store

**Input:** the 47M-row historical dataset you already have.

1. **Document the source** at the top of your converter module: where it came from (URL or dataset ID), its date range, its columns, and anything odd about it, such as gaps, units, timezone or duplicates.
2. **Write a converter**, for example `src/floodsense/data/build_rainfall_store.py`, runnable as `uv run python -m floodsense.data.build_rainfall_store --source <path>`. It writes:
   - `data/raw/rainfall/readings/year=YYYY/*.parquet` with columns `station_id` (str), `timestamp` (tz-aware `Asia/Singapore`, on 5-minute boundaries) and `rainfall_mm` (float, 0–100). No duplicate `(station_id, timestamp)` pairs. You may drop zero-rain rows to save space.
   - `data/raw/rainfall/stations.parquet` with columns `station_id, name, latitude, longitude, valid_from, valid_to`. Stations have moved over the years (for example S119 and S215 by about 1 km), so add a new row when a station's coordinates change. If your source has no station metadata, build it from the API's `stations` list, sampling at least one date per year. `data/reference/nea_rainfall_stations.json` shows how; its `method` field records what was done.
   - Process the data in chunks; don't load 47M rows into memory at once.
3. **Implement `src/floodsense/data/rainfall_store.py`** exactly as its docstrings specify. Two details matter:
   - **Missing is not dry.** If zero-rain rows were dropped, `load_snapshots` fills in 0.0 for stations that *were reporting* at that step. A station that wasn't reporting must stay absent. You'll need to record which stations reported, for example from the source's own zero rows before you drop them, or with a per-day reporting table.
   - `load_snapshots` must give the same snapshots as `NEAPoller.fetch_range` would for the same window. The contract test checks this across the whole 17 Apr 2021 replay (about 950 steps).
4. **Fill gaps** in your source with `NEAPoller.fetch_range` (2017 onward is available), not with estimates. Record in the module docstring which periods came from where.

## Deliverable (b): planning-area polygons

1. Download the URA Master Plan 2019 planning-area boundaries from data.gov.sg (search "Master Plan 2019 Planning Area Boundary").
2. Save them to `data/reference/ura_planning_areas_mp2019.geojson` as a FeatureCollection, one feature per planning area, with `properties.name` in upper case, exactly matching the 55 keys of `URA_PLANNING_AREAS`. Use WGS84 lon/lat coordinates. Commit a script that does the download and normalisation.
3. Add `shapely` as a dependency (`uv add shapely`).
4. **Derive zone reference points from the polygons** (`representative_point()`) and replace the hand-typed `lat`/`lon` in `URA_PLANNING_AREAS` (`src/floodsense/spatial/singapore_geo.py`), either loaded from the GeoJSON or generated by the script. Keep the `region` field. Leave `pub_monitored` alone; Phase 4 handles it.
5. Replace `create_singapore_geojson()`'s 12-point circles with the real polygons.

## Deliverable (c): sourced flood events

1. Move the 25 events from `HISTORICAL_FLOOD_EVENTS_BENCHMARK` (`src/floodsense/features/ground_truth_extractor.py`) into `data/reference/flood_events.csv`, using the columns documented in `src/floodsense/data/flood_events.py`. For each event, find the article or PUB post it came from and fill in `source_url`, `source_name`, `time_precision` and `cause`. **If you can't find a source, delete the event.** Please don't guess times: use `time_precision` (`exact`, `approx_15min`, `approx_hour` or `day_only`) to say how precise the start time is.
2. Then grow the set: PUB press releases, the PUB flood-alerts Telegram channel, and news reports (Straits Times, CNA, Mothership). Using an LLM to extract the fields from articles is fine, but a human checks every row before it is committed, and every row keeps its URL.
3. Extend `FloodEvent` with `event_id`, `source_url`, `cause` (`rain` / `rain_tide` / `other`) and `time_precision`. Set `geocoding_confidence` to 1.0 for rows a person has checked.
4. Implement `load_flood_events()`, then **delete `HISTORICAL_FLOOD_EVENTS_BENCHMARK`**. Point its two users (`GroundTruthExtractor._load_curated_benchmark` and `synthetic_or_historical_loader.py`) at `load_flood_events()`.

Two events already checked against sources, to use as examples:

| Event | Source | Note |
|---|---|---|
| 17 Apr 2021, Dunearn Rd / Bukit Timah Rd near Sime Darby Centre (BUKIT TIMAH) | https://mothership.sg/2021/04/singapore-floods-april-17/ | 161.4 mm fell 12:25–15:25; exact flood start not given (`approx_hour` at best) |
| 10 Jan 2025 evening, Jalan Seaview | https://mothership.sg/2025/01/more-rain-january/ | Heavy rain plus a 2.8 m high tide gives `cause = rain_tide`; confirm the planning area from the polygons |

---

## Out of scope (Phase 4 covers these, so please don't change them)

- Label logic (`attach_labels_to_feature_df`), `src/floodsense/models/train.py`, metrics and thresholds
- Deleting `generate_historical_dataset()`; that happens when Phase 4 moves training to your store
- The Streamlit app, the deck and the Databricks pipeline

## Handing back

Open a PR from `phase3/real-data` into `remediation/phase-0-2`. Include:
- the source description, date coverage and row counts per year
- which periods (if any) were backfilled from the API
- how many of the original 25 events kept a source, and how many were dropped
- the output of `uv run pytest tests/test_phase3_contract.py -rsx`

It will be reviewed against this document before merging.
