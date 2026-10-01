# Phase 3: real data for FloodSense

**For:** anyone building, checking or extending FloodSense's training data, and any coding agent they use.

**Goal:** every input to training and evaluation is real and traceable to its source. Phase 3 delivers **data and loaders only**; Phase 4 (labels, training, evaluation) consumes them.

## Ground rules (non-negotiable)

1. **Never fabricate data.** No synthetic fallbacks, no random or zero values when a download fails, no placeholder rows, no copying one period into another. If data is missing, fail loudly or leave a gap.
2. **Nothing hand-typed when a source exists.** Coordinates, boundaries and events come from a file or API, and the code that produced them is in the repo.
3. **Every flood event has a `source_url` someone has opened**, plus a quoted sentence from it. If an event can't be sourced, drop it.
4. **Raw data stays out of git.** `data/raw/` is gitignored; small reference files in `data/reference/` are committed.
5. **Timestamps are timezone-aware Singapore time** (`Asia/Singapore`). Use `floodsense.common.timeutil.to_sgt`.

**Why the rules are this strict:** a first attempt at this phase passed an earlier, weaker version of the contract tests with a store that held one copied storm plus zero-rain filler, and an event list whose links mostly returned 404 or redirected to a news site's home page. The tests now check what the data *contains*, and the events need a human sign-off.

---

## (a) Historical rainfall store: built from NEA's own data

| Period | Source | How |
|---|---|---|
| 2017–2024 | data.gov.sg collection 2279, "Historical Rainfall across Singapore" (NEA): one CSV per year, ~0.8–1.3 GB, one row per station per 5 minutes | `download`, then `convert` |
| 2025 → yesterday | The data.gov.sg real-time rainfall API's `?date=` history (the same API the live app and the replay use) | `backfill` |
| Sparse days in the bulk CSVs (158 days with under 90% of 5-minute steps, 92 of them in 2018, including most of August 2018) | The same API | `gapfill`; bulk readings win wherever both exist |

```bash
uv run python -m floodsense.data.build_rainfall_store download   # ~9 GB of CSVs into data/raw/rainfall/bulk/
uv run python -m floodsense.data.build_rainfall_store convert    # -> readings/year=YYYY/part-bulk.parquet, stations.parquet
uv run python -m floodsense.data.build_rainfall_store backfill   # -> readings/year=YYYY/part-api-YYYY-MM.parquet
uv run python -m floodsense.data.build_rainfall_store gapfill    # -> readings/year=YYYY/part-api-gapfill.parquet
```

- **Resumable:** every step can be re-run. Downloads skip complete files, the backfill skips finished months, and `data/raw/rainfall/manifest.json` records sources, checksums and row counts.
- **Rate limits:** both data.gov.sg endpoints share them. The downloader waits out HTTP 429s. For the backfill, a free API key (`FLOODSENSE_DATA_GOV_API_KEY`) helps, or space calls out with `FLOODSENSE_API_PAGE_DELAY_SEC=2.5`.
- **What `convert` normalises:**
  - Early records are stamped one second before the 5-minute grid (`09:59:59`), so they are snapped to it.
  - Only the `TB1 Rainfall 5 Minute Total F` series in mm is kept; other series, values outside 0–100 mm and exact duplicates are dropped and counted.
- **Missing is not dry:** zero readings are stored like any other value. A station with no row at a step didn't report, and nothing fills it in.
- **Size:** the CSVs hold 48.5M readings for 2017–2024 (4.6–7.1M a year) from 54–80 stations a year.
- **Station metadata comes from the CSVs' own coordinates.** A station that moved gets one row per location, with `valid_from` / `valid_to`.
- **Agreement with the API:** the 17 Apr 2021 bulk data matches the API replay exactly in timestamps, stations and coordinates. Values differ only by the API rounding to 2 decimals while the CSV keeps the gauges' 3 (0.408 vs 0.41 mm).

The interface is `src/floodsense/data/rainfall_store.py`: `read_rainfall`, `load_station_table`, `stations_at`, `load_snapshots`.

## (b) Planning-area polygons

- **File:** `data/reference/ura_planning_areas_mp2019.geojson`, URA Master Plan 2019 Planning Area Boundary (No Sea), data.gov.sg `d_4765db0e87b9c86336792efe8a1f7a66`.
- **Produced by:** `uv run python -m floodsense.spatial.download_ura_polygons`.
- **Zone reference points** in `URA_PLANNING_AREAS` are each polygon's `representative_point()`.

## (c) Sourced flood events and the sign-off

`data/reference/flood_events.csv`. The columns are documented in `src/floodsense/data/flood_events.py`. Each row needs:
- a `source_url` and `source_name`
- an `evidence_quote`: a sentence copied from the source that names the place and the time
- a `time_precision` that says honestly how well the start time is known (`exact`, `approx_15min`, `approx_hour` or `day_only`)

**Sign-off:**
1. Candidates may be drafted by a person or an LLM.
2. A person then opens each `source_url`, checks that the quote, place, planning area and time are right, and writes their name in `verified_by`.
3. `load_flood_events()` only returns signed-off rows (pass `include_unverified=True` to see the rest), so an unchecked row can never become a training label.

---

## The contract: `tests/test_phase3_contract.py`

```bash
uv run pytest tests/test_phase3_contract.py -rsx              # offline checks
uv run pytest -m network tests/test_phase3_contract.py -rsx   # store vs live API, and every event link
```

| Check | Why it exists |
|---|---|
| Every year 2017 → now present; ≥40 stations report ≥80% of each year's 5-minute steps; data reaches within 31 days of today | Partitions that exist but are empty no longer pass |
| Median full-coverage station total 1,000–4,500 mm; 25–90% of days wet; a ≥10 mm 5-minute burst every year | Zero-filled or scaled data fails |
| Daily island-mean rainfall of any two years correlates < 0.9 | Copying a year into another fails |
| The store matches the 17 Apr 2021 API replay (to API rounding) | The converter's handling of times and stations is right |
| *(network)* 4 random evenings across the record match the live API | Proves the store *is* NEA's data, not something shaped like it |
| Events: columns, allowed values, SGT, ≥20-character evidence quote | Structure |
| ≥20 signed-off events, ≥5 of them from the test years (2024+) | Training and the one-off test score need real labels |
| *(network)* every `source_url` returns 200, isn't a home or search page, and mentions the place | The first event list failed exactly this |

The rainfall tests skip when `data/raw/rainfall/` is absent, which is always the case in CI. The event tests run in CI.
