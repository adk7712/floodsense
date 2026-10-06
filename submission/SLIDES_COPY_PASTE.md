# FloodSense: Round 1 slides, copy-paste version

Use the official DAISI 3-slide template (the guide says "keep it to three slides").
Images: `submission/slide_visuals/` (charts) and `submission/screenshots/` (app, dark mode).
Every number below is checked against the repo (6 Oct 2026).

Cover or footer (fill in): Team [name] · [Name, institution, course, year, email] × each member · Track B2 · github.com/adk7712/floodsense

---

## SLIDE 1: Problem & why it matters

**Title:**
Flash floods in Singapore are fast, local, and the warning comes late

**Block 1: The moment**
17 April 2021: 161.4 mm of rain fell on western Singapore in 3 hours, 91% of April's monthly average. Dunearn Road flooded, and the water was gone in about 30 minutes. A warning only helps if it arrives before the rain peaks.

**Block 2: The gap**
PUB has cut flood-prone land from ~3,200 ha (1970s) to 23.3 ha (2025), with 1,000+ water-level sensors and 500+ CCTVs. But public alerts are mostly regional, or fire once drain water rises. Few answer: *will my area flood in the next hour, and how likely?*

**Block 3: Why it's hard**
Floods are rare: we sourced 66 floods in 10 years, in 27 of 55 planning areas. A naive model either never alarms or alarms so often that people stop listening.

**Image (main):** `slide_visuals/floods_per_year.png`
**Image (optional, small):** `screenshots/06_flood_prone_trend_and_context.png` (PUB flood-prone land 27 → 23.3 ha, 2022–2025)

**Footnote:** Sources: Mothership / FloodList (17 Apr 2021); MSE oral reply, 4 Feb 2025; PUB Flood Forecasting & Monitoring; PUB Flood Prone Areas (data.gov.sg). Floods counted are those we could source, not all floods.

---

## SLIDE 2: Solution & data

**Title:**
FloodSense: the chance of a flash flood in each of Singapore's 55 planning areas, in the next hour, with the reason

**Block 1: How it works**
- See the rain: NEA's 5-minute gauges mapped to each zone. An offline gauge is left out, never treated as dry.
- Honest labels: 66 flood events, each with a source link, a quoted sentence and a human sign-off.
- Rare-event model: rules, logistic regression and LightGBM compete at equal false-alarm levels, with time-based validation. With only 36 training floods, ML overfit; a calibrated 60-minute rainfall rule was the most robust, so we ship it and say so.
- Explain and act: a risk map; plain reasons ("Bukit Timah: HIGH, 47 mm in the last hour, its rarest on record"); PUB's live flood alerts; MRT stations in risky areas.

**Block 2: Open data (all 4 Track B2 datasets, plus 2 more)**
NEA rainfall, real-time API and historical (60.8M readings, 2017 to Sep 2026) · PUB Flood Alerts, real-time · PUB Flood Prone Areas, 2022–2025 · URA Master Plan 2019 planning areas · LTA MRT station exits · 66 sourced flood events

**Block 3: Results (held-out test, 2024 to Sep 2026, 30 floods, scored once)**

| Alert level | Floods caught (90% CI) | Median warning | False alarms per zone-year |
|---|---|---|---|
| Moderate (≥0.30%) | 21/30 = 70% (57–83%) | 15 min | 7.3 |
| High (≥0.71%) | 12/30 = 40% (27–53%) | 10 min | 1.6 |

Honest limits: gauges alone give 10–15 min of warning (radar nowcasting is next). About 1 in 17 High alerts was followed by a *reported* flood.

**Image (main):** `screenshots/02_replay_17apr2021_1245.png` (17 Apr 2021, 12:45: Bukit Timah HIGH, 9 High and 19 Moderate zones)
**Image (optional, small):** `screenshots/04_zone_diagnostic_bukit_timah_high.png`

---

## SLIDE 3: Databricks architecture & impact

**Title:**
Running on Databricks Free Edition: one Lakeflow pipeline from live rain to zone risk

**Block 1: Architecture (built; see diagram)**
- A poller job pulls live NEA rain and PUB flood alerts into a Unity Catalog volume.
- A Lakeflow pipeline (Auto Loader) takes them through bronze → silver (quality checks, quarantine) → gold (zone risk and tier), using the same scoring code as the app.
- An AI/BI dashboard reads gold.
- The model is versioned in MLflow and registered in Unity Catalog (flood_model @champion).
- Tables carry source and layer tags.

Proof: 949 files → 64,223 readings → 15,840 zone-risk rows in about 1¾ min, and every risk tier matched local scoring.
Next (dashed in the diagram): radar nowcasting, serving the model from the registry, a Databricks App.

**Block 2: Impact (a complement to PUB, not a replacement)**
- Residents and drivers: earlier, per-zone warnings with a reason.
- Commuters / LTA: MRT stations in risky areas. At 12:45 on 17 Apr 2021, 25 stations were in High areas, including Beauty World, Sixth Avenue and Tan Kah Kee.
- Town councils: which zones' drains to clear first.
- Planners: where floods keep recurring, beside PUB's flood-prone trend.

**Block 3: How we'll measure success**
- Minutes of warning before PUB's own alert.
- Share of floods caught.
- False alarms kept within the budget we set before testing.

**Image (main):** `slide_visuals/architecture_diagram.png`
**Image (small):** your **Databricks pipeline graph** screenshot (Jobs & Pipelines → floodsense), or `screenshots/05_transport_at_risk_1245.png`

---

## Extra photos to submit separately

1. `screenshots/05_transport_at_risk_1245.png`: MRT stations at risk, 17 Apr 2021, 12:45
2. `screenshots/01_live_feed.png`: Live Feed with the PUB alerts line
3. `screenshots/03_risk_map_1245.png`: risk map at 12:45
4. `screenshots/07_prototype_status_and_test_results.png`: test results with 90% intervals
5. Your Databricks screenshots: pipeline graph, poller job run, AI/BI dashboard, registered model
