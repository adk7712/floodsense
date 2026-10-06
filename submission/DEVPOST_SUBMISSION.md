# FloodSense: Devpost submission drafts (Round 1)

Copy each field into the form. Character counts are checked at the bottom of this file.

---

## Project name (max 60)

FloodSense: Per-Zone Flash-Flood Warnings for Singapore

---

## Elevator pitch (max 200)

The chance of a flash flood in each of Singapore's 55 planning areas in the next hour, with the reason. Built on live NEA rain gauges, PUB alerts and a Databricks Lakeflow pipeline.

---

## About the project (Markdown)

### Inspiration

On 17 April 2021, 161.4 mm of rain fell on western Singapore in three hours, 91% of April's average for the whole month. Dunearn Road flooded, and the water was gone within about 30 minutes. In 2025, Singapore had its wettest March on record.

PUB's drainage system is world-class. Flood-prone land fell from about 3,200 ha in the 1970s to 23.3 ha in 2025. But public flood alerts are mostly regional, or fire once water in a drain is already rising. For a flash flood that clears in half an hour, that's late. We wanted to answer a simpler question for residents, drivers and town councils: **will my area flood in the next hour, how likely is it, and why?**

### What it does

FloodSense gives each of Singapore's 55 URA planning areas a **calibrated chance of a reported flash flood in the next hour**, every 5 minutes, from NEA's live rain gauges.

- **Risk map:** Low / Moderate / High for every zone, with the reason in plain words (e.g. *"Bukit Timah: HIGH, 47 mm in the last hour, its rarest on record"*).
- **PUB's live flood alerts** appear beside our own risk.
- **Public transport at risk:** MRT/LRT stations with an exit in a Moderate or High area.
- **Replay any day since 2017** using the real 5-minute readings. On 17 April 2021, Bukit Timah goes High at 12:45, about an hour before the Dunearn Road flood was reported.
- **PUB's flood-prone-area trend** for 2022–2025, for context.

### How we built it

- **Data (all open, from data.gov.sg):**
  - 60.8 million NEA 5-minute rainfall readings (2017 to Sep 2026), from the historical collection, the real-time API and a gap-fill for sparse days
  - URA Master Plan 2019 planning-area boundaries
  - PUB Flood Prone Areas (2022–2025)
  - PUB's real-time flood alerts
  - LTA MRT station exits
- **Labels:** we built 66 flood events by hand. Each has a source link, a quoted sentence naming the place and time, and a human sign-off. Imprecise report times become partial labels, not invented exact ones.
- **Features:** inverse-distance-weighted rain per zone. When a gauge drops out, it is left out and the rest are re-weighted, so missing data is never treated as dry. We also compute rolling 5- to 120-minute totals, a 72-hour wet-ground index and per-zone storm rarity.
- **Model:**
  - Rules, logistic regression and LightGBM competed at matched false-alarm levels, using year-by-year forward-chaining validation.
  - A Platt-calibrated 60-minute rainfall rule won.
  - Alert thresholds come from false-alarm budgets we fixed *before* touching the test set.
  - The 2024–2026 test years were scored once.
- **Databricks (Free Edition):**
  - A scheduled poller job lands live NEA readings and PUB alerts in a Unity Catalog volume.
  - One Lakeflow pipeline (Auto Loader) takes them through bronze → silver (quality checks and quarantine) → gold (zone risk), using the *same* scoring code as the app.
  - An AI/BI dashboard reads gold.
  - The model is versioned in MLflow and registered in Unity Catalog.
  - A parity test proves Databricks reproduces local scoring row for row.
- **App:** Streamlit with live and replay modes, plus light and dark themes.
- **Engineering:** CI with ruff, mypy and 190+ tests, including data-contract tests that check what the data actually contains.

### Results (held-out test, 2024 to Sep 2026, 30 floods, scored once)

| Alert level | Floods caught (90% CI) | Median warning | False alarms per zone-year |
|---|---|---|---|
| Moderate | 70% (57–83%) | 15 min | 7.3 |
| High | 40% (27–53%) | 10 min | 1.6 |

### Challenges we ran into

- **Floods are extremely rare.** We had only 36 floods to train on. LightGBM and logistic regression overfit, and the simplest model was the most robust. We ship it and say so.
- **Honest labels are hard.** Flood reports are scattered across PUB posts and news, often with only a date. We verified each by hand, and we rejected and documented the ones we couldn't verify.
- **Missing isn't dry.** NEA's record has gaps, including most of August 2018. We gap-filled from the API and never let a missing gauge read as zero rain.
- **Databricks runs differ from local runs.** Our first workspace runs caught two bugs the local run hadn't:
  - an Auto Loader option name
  - gold being computed before silver was ready
  We also hit a timezone shift risk from Spark's naive timestamps. A parity test now guards all three.
- **The live alert feed keeps no history.** PUB's alert API returns nothing for past dates, so we can't train on it yet. We archive every alert from now on.

### What we learned

- With rare events, **evaluation design matters more than model choice**: matched false-alarm budgets, forward-chaining validation, and a test set scored once.
- **Saying what's not built yet** is as important as what is. Gauges alone give about 10–15 minutes of warning. The most impactful next step is radar nowcasting, which would warn before the rain arrives.
- **Running the same scoring code** in the app, in training and on Databricks prevents silent drift.

### What's next

- Radar nowcasting, for longer warning times.
- Zone susceptibility (which zones flood more), tide for coastal zones.
- The app reading gold directly, and the model served from the Unity Catalog registry.
- LTA live disruption feeds and bus stops.
- An LLM-assisted pipeline that drafts flood events from news and PUB alerts, with human sign-off.

---

## Built with (max 25 tags)

python, databricks, lakeflow-declarative-pipelines, auto-loader, delta-lake, unity-catalog, mlflow, databricks-ai-bi-dashboards, databricks-jobs, databricks-sql, apache-spark, pandas, numpy, scikit-learn, lightgbm, streamlit, plotly, shapely, pydantic, pytest, playwright, github-actions, data.gov.sg, nea-rainfall-api, ura-master-plan-2019

---

## Datasets used / plan to use

**Used (all open data via data.gov.sg):**
1. **Rainfall across Singapore: real-time API** (NEA). Live 5-minute readings from about 60–90 gauges. They drive Live mode and the Databricks poller.
2. **Historical Rainfall across Singapore** (NEA, data.gov.sg collection 2279), 2017–2024, plus the real-time API's date history for 2025 to Sep 2026 and for sparse days. That gives 60.8M readings for training and replay.
3. **Flood Alerts across Singapore: real-time API** (PUB). Shown live in the app and archived in Databricks on every poll. The API keeps no past alerts.
4. **Flood Prone Areas** (PUB, annual hectares 2022–2025, dataset d_c4aed98f1533eb3a66f65dbb1a30da46): 27 → 24.1 → 23.6 → 23.3 ha, shown as a trend.
5. **Master Plan 2019 Planning Area Boundary (No Sea)** (URA, d_4765db0e87b9c86336792efe8a1f7a66). These are the 55 zones.
6. **LTA MRT Station Exit (GeoJSON)** (LTA, d_b39d3a0871985372d7e1637193335da5). It shows which stations each risky area exposes.
7. **66 sourced flood events (2017 to Sep 2026)**, built by us from PUB alerts and news. Each has a link, a quote and a human sign-off. These are the training labels.

**Plan to use:**
- NEA/MSS rain-area radar frames, for nowcasting. There is no public archive, so we will archive them from now on.
- Tide predictions, for coastal zones (e.g. Jalan Seaview, 10 Jan 2025).
- LTA DataMall: bus stops, train service alerts and traffic incidents.
- Historical rainfall for 2016.

---

## Intended Databricks architecture (built / plan to use)

**Built and running on Databricks Free Edition (Unity Catalog schema `workspace.floodsense`):**
1. **Ingestion:** a Databricks **Job** (`floodsense-rainfall-poller`) fetches NEA rainfall and PUB flood alerts from data.gov.sg. It reads the API key from a Databricks secret. Raw responses land unchanged in a **Unity Catalog volume**. The job then triggers the pipeline. Its schedule is every 30 minutes, kept paused within Free Edition's compute limits.
2. **Transformation:** one **Lakeflow Declarative Pipeline** with **Auto Loader**:
   - **Bronze:** raw payloads.
   - **Quarantine:** payloads that fail to parse.
   - **Silver:** gauge readings with data-quality expectations (0–100 mm, deduplicated), plus station locations and a PUB alert archive.
   - **Gold:** calibrated flood risk and tier for every zone and 5-minute step. It is computed by the same pandas scoring code as the app and training, through `applyInPandas`.
3. **Proof:** 949 files → 64,223 readings → 15,840 gold rows in about 1¾ minutes. A parity test confirmed identical risk tiers to local scoring.
4. **ML:** the model is logged to **MLflow** with its model card, test report and metrics, and registered in **Unity Catalog** as `flood_model`, alias `champion`.
5. **Analytics:** an **AI/BI dashboard** on gold shows live risk by zone, the last 24 hours and pipeline health.
6. **Governance:** Unity Catalog descriptions on the schema, volumes and model; source, layer and data-classification tags on every table.

**Plan (by Demo Day):**
- Serve the registered model from Unity Catalog inside the pipeline.
- A **Databricks App** reading gold.
- Run the poller on its live schedule.
- Radar nowcasting frames in the same volume → bronze path.
- `ai_query` to draft new flood events from archived PUB alerts and news, for human sign-off.

---

## Images (max 15), in upload order, with captions

1. `slide_visuals/architecture_diagram.png`: Databricks architecture. Solid = running on Free Edition, dashed = next.
2. `screenshots/02_replay_17apr2021_1245.png`: Replay of 17 Apr 2021 at 12:45. Bukit Timah is HIGH, about an hour before the Dunearn Road flood report.
3. `screenshots/04_zone_diagnostic_bukit_timah_high.png`: Zone diagnostic. 46.6 mm in the last hour, rarer than any half-hour in this zone's 2017–2023 record.
4. `screenshots/05_transport_at_risk_1245.png`: 25 MRT stations in High-risk areas at 12:45 (exposure, not observed disruption).
5. `screenshots/03_risk_map_1245.png`: Risk map. 9 High and 19 Moderate zones at the storm's peak.
6. `screenshots/01_live_feed.png`: Live Feed, with live NEA gauges and PUB's live flood-alert line.
7. `slide_visuals/floods_per_year.png`: 66 sourced flash floods, 2017 to Sep 2026.
8. `screenshots/06_flood_prone_trend_and_context.png`: PUB flood-prone land, 27 → 23.3 ha (2022–2025), with that day's reported floods.
9. `screenshots/07_prototype_status_and_test_results.png`: Held-out test results with 90% confidence intervals.
10. *(your screenshot)* Databricks Lakeflow pipeline graph: bronze → silver → gold, plus PUB alert tables.
11. *(your screenshot)* Poller job `floodsense-rainfall-poller`, successful run.
12. *(your screenshot)* AI/BI dashboard "FloodSense – Live Flood Risk".
13. *(your screenshot)* Unity Catalog model `flood_model`, alias `champion`.
