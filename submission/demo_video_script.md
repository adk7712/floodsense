# FloodSense Demo Video Walkthrough Script

**Duration:** Exactly 3:00 minutes · **Track:** DAISI Challenge 2026 (Track B2: Climate Action & Resilience)
*All numbers, metrics, and timestamps match the FloodSense production prototype and Databricks Update `34cfa680…`.*

---

### Pre-Recording Setup Checklist
1. **Streamlit App:** Start with `uv run streamlit run src/floodsense/app/streamlit_app.py`.
2. **Browser Window:** Open `http://localhost:8501` in a dedicated full-width desktop browser window. Hide bookmarks bar. Set zoom to 100%.
3. **Warm the Cache:** Click "Replay the 17 Apr 2021 storm" once and drag the slider across 12:15–13:30 to pre-warm the cache for 60 FPS slider movement on camera.
4. **Databricks Tab:** Open a second browser tab at `https://dbc-91a4e71d-644e.cloud.databricks.com` → **Jobs & Pipelines → floodsense** (Update `34cfa680…`). Ensure the DAG shows row counts: 949 Bronze → 64,223 Silver → 15,840 Gold.
5. **Screen Recording:** Capture 1080p 60fps with clear microphone audio.

---

### 3-Minute Video Timeline & Script

| # | Time | Visual on Screen | Spoken Script (Word-for-Word Guide) |
|---|---|---|---|
| **1** | **0:00–0:15** | App header banner with FloodSense title and live status. | "This is FloodSense: Singapore's first zone-level flash flood early warning system, delivering 15 to 30 minutes of actionable lead time before drains overflow, powered by Databricks." |
| **2** | **0:15–0:35** | **Live Feed** view (default). Point cursor at green "Live: N of 89 gauges reporting", the URA boundary map, and the 5 summary KPI cards. | "We ingest live 5-minute NEA rainfall streaming from data.gov.sg. Readings are dynamically interpolated with inverse-distance weighting so offline gauges are re-weighted, never assumed dry. Every zone receives a calibrated flood probability and a Low, Moderate, or High tier." |
| **3** | **0:35–0:50** | Click **"⟳ Replay the 17 Apr 2021 storm"** button under the rarity gauge. Map reloads to 12:15. | "To prove it on real ground truth, let's replay 17 April 2021, when western Singapore received 161 mm of rain and Dunearn Road flooded waist-deep. We stream the actual 5-minute readings with a 72-hour soil moisture warm-up." |
| **4** | **0:50–1:35** | Keep **Bukit Timah** selected. Drag time slider: 12:15 → **12:25** (Moderate) → **12:45** (High) → 13:00. Point to rolling-rain tiles and the Rarity Percentile gauge. | "At 12:25 Bukit Timah triggers Moderate. By 12:45 it hits High: 47 mm of rain in one hour, reaching the 99.8th percentile of this zone's 7-year history. PUB's drain sensors and public reports only triggered at 1:44 pm—giving drivers, residents, and emergency crews a full hour of life-saving lead time." |
| **5** | **1:35–1:55** | Scroll down to **"Hydrological Context & Archive"**. Point at the Bukit Timah & Jurong East verified quotes and source links. | "Every single training label is human-verified with source links and quotes. Notice our 3-year historical trend analysis: between 2023 and 2026, flood events surged from 2 to 15 per year, with chronic hotspots in Bukit Timah with 8 floods, Yishun with 4, and Jurong East with 3." |
| **6** | **1:55–2:20** | Scrub slider to **13:35** (11 zones High). Highlight the **Prototype Status** panel. | "We benchmarked our model against LightGBM and Logistic Regression. In extreme rarity, complex ensembles overfit; our physics-grounded 60-minute window with Platt calibration delivered an optimal Neyman-Pearson classifier. On 30 unseen test floods from 2024 to 2026, we achieved 70% recall at Moderate and 40% at High (90% CI: 27–53%), with just 1.6 false alerts per zone-year." |
| **7** | **2:20–2:35** | Sidebar: Click **"22 Nov 2024 · Sembawang, Toa Payoh, Yishun"** or show the date picker. | "Any day from 2017 to 2026 can be replayed instantly. We've also designed extension layers for LTA road and public transit disruptions, as well as coastal storm-surge co-occurrence under climate sea-level rise scenarios." |
| **8** | **2:35–2:55** | Switch to Databricks tab: **Lakeflow Pipeline DAG** showing row counts (949 Bronze → 64,223 Silver → 15,840 Gold). | "And this runs natively on Databricks Free Edition serverless compute. One Lakeflow declarative pipeline ingests raw JSON via Auto Loader, enforces DLT quality expectations and quarantine tables, and scores gold risk tiers in 1 minute 45 seconds—with bit-identical parity to local inference." |
| **9** | **2:55–3:05** | Switch back to the Streamlit interactive map. | "With serverless Lakeflow pipelines and Unity Catalog data governance, FloodSense turns reactive drainage telemetry into proactive urban climate resilience. Thank you." |

---

### Important Guidelines & Anti-Patterns
- **Do Not Claim Unbuilt Features as Live:** Be crystal clear that radar nowcasting, tide sensors, and LTA transit APIs are in the roadmap/design phase. The live gauge feed, 10-year store, model benchmarking, and Databricks Lakeflow pipeline are fully built and verified today.
- **Maintain Composure on Cost/False Alarms:** Highlight that 1.6 false High alerts per zone-year is an intentional operational budget chosen before test evaluation, not a model bug.
