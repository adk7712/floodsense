# FloodSense demo video: script

About 3:30. Numbers come from `submission/round1_pitch_deck.md` and `models/final_report.json`
(checked 6 Oct 2026). Recording at about 20:00 SGT on 6 Oct 2026.

**Before recording (do these in order)**
1. Start the app: `uv run streamlit run src/floodsense/app/streamlit_app.py`. Open
   http://localhost:8501 in a desktop browser, full width. Set browser zoom to 90%.
2. Hide the bookmarks bar, close other tabs, and make sure no tokens or passwords are visible
   (Databricks URL bar and any open notebooks included).
3. Do one full run-through of the whole video. This warms the cache so the sliders are instant
   on camera. Then go back to **Live Feed** and reload the page so you start clean.
4. Open the Databricks tabs, already signed in, in this order (left to right):
   - Tab 2: **Jobs & Pipelines → floodsense** (the pipeline graph with row counts)
   - Tab 3: **Jobs & Pipelines → floodsense-rainfall-poller** (the job and a run on the Runs page)
   - Tab 4: AI/BI dashboard **FloodSense – Live Flood Risk**
   - Tab 5: **Catalog → workspace → floodsense → flood_model** (alias `champion`, and the tags
     on the schema or a table)
5. Do NOT click Run on the poller during the video. Show the existing run. Its schedule is paused.
6. The ⋮ menu and Deploy button at the top right belong to Streamlit. Ignore them.
7. Start with tab 1 (the app) in front. Keep the sidebar open.

| # | Time | On screen | Say (roughly) |
|---|---|---|---|
| 1 | 0:00–0:15 | App header, Live Feed. | "17 April 2021: 161 mm of rain fell on western Singapore in three hours, and Dunearn Road flooded. The water was gone in about half an hour, so a warning only helps if it comes early. This is FloodSense: flash-flood risk for each of Singapore's 55 planning areas, for the next hour, built on open data." |
| 2 | 0:15–0:45 | **Live Feed** (opens by default). Point at the line under the title: "No active PUB flash-flood alerts in the last 3 hours…" (or the red alert box if one is active). Then the green "Live: N of 89 gauges" badge, the risk map, and the KPI cards. | "This is live NEA rain-gauge data, and it reloads every 5 minutes. Under the title is PUB's live flood-alert feed. Today there are none. If PUB issues one, it shows here, next to our own risk. Each zone's rain comes from the gauges that are reporting. A gauge that's offline is left out, never treated as dry. Every zone gets a calibrated chance of a reported flood in the next hour." |
| 3 | 0:45–1:00 | Scroll to **Public transport at risk** (live, probably empty), then the **Hydrological Context & Archive** panel. Open the expander **PUB flood-prone areas, 2022–2025**. | "Below the map we list MRT and LRT stations that sit in a Moderate or High area, or inside a PUB alert circle. That's exposure, not observed disruption. PUB's flood-prone land keeps shrinking, 27 down to 23.3 hectares from 2022 to 2025, yet flash floods keep recurring in a few zones. That's the gap we target." |
| 4 | 1:00–1:15 | Scroll back up. Click **"⟳ Replay the 17 Apr 2021 storm"** (under the rarity gauge). The sidebar switches to Replay Storm, step 2: "Simulating 17 Apr 2021", **Select Planning Area** = Bukit Timah, the time slider at 12:15. | "Now a real storm. The sidebar's Replay Storm mode works in two steps: choose a date, then simulate it. This button jumps straight to the second step, 17 April 2021, using the real 5-minute readings from that afternoon. Same model, same code." |
| 5 | 1:15–1:50 | Keep **Bukit Timah** selected. Drag the time slider slowly: 12:15 → **12:25** (Moderate) → **12:45** (High). Point at the status, the rolling-rain tiles and the rarity gauge. | "At 12:25 Bukit Timah goes Moderate. At 12:45 it's High: 47 millimetres in the last hour, at the top of this zone's 2017 to 2023 record. The flood on Dunearn Road was reported at about 1:44 pm, so that's roughly an hour of warning. That is our best case, not the typical one." |
| 6 | 1:50–2:10 | At 12:45, scroll to **Public transport at risk**. Point at "Stations in High areas: 25" and the table (Beauty World, King Albert Park, Sixth Avenue, Tan Kah Kee). | "At 12:45, 25 stations have an exit in a High area, including Beauty World, King Albert Park, Sixth Avenue and Tan Kah Kee. 117 including Moderate. This is exposure from the risk map, not a record of disruption." |
| 7 | 2:10–2:35 | Drag the slider to **13:35**. Point at the map's "High (11)" count, then scroll to the **Prototype Status & Verification** panel and the 90% intervals. Optionally, at the bottom of the sidebar, click **Choose another date** to show step 1 (date picker and major-storm buttons), then **Simulate this date**. | "To be straight about the cost: 24 zones went High that afternoon, at most 11 at once, and most had no flood report. On floods from 2024 to September 2026 that the model never saw, scored once, we caught 70% at Moderate (90% interval 57 to 83) and 40% at High (27 to 53), with 1.6 false High alerts per zone per year. Median warning is 10 to 15 minutes. Any day from 2017 can be replayed the same way." |
| 8 | 2:35–2:50 | Databricks tab 2: the **floodsense** pipeline graph, bronze → silver → gold, and the `pub_flood_alerts` tables. | "Behind this is a Lakeflow pipeline on Databricks Free Edition. Raw gauge files go to bronze, parsed readings in silver, zone risk in gold, with the same scoring code as the app. A second branch archives PUB's flood alerts, which the API doesn't keep. On 2 October it turned 949 files into 64,223 readings and 15,840 zone-risk rows, and every tier matched our local results." |
| 9 | 2:50–3:05 | Databricks tab 3: job **floodsense-rainfall-poller** and its last run. | "This job fetches the last 96 hours of live NEA readings and triggers the pipeline. It has a 30-minute schedule, paused to stay inside Free Edition's compute, so we run it on demand." |
| 10 | 3:05–3:15 | Databricks tab 4: AI/BI dashboard **FloodSense – Live Flood Risk**. | "The AI/BI dashboard on gold shows live risk by zone and pipeline health." |
| 11 | 3:15–3:30 | Databricks tab 5: Unity Catalog model `workspace.floodsense.flood_model`, alias `champion`, then the tags. Finish on the app, Live Feed map. | "The model is registered in Unity Catalog as flood_model, alias champion, with its model card and test report. Today the app and gold still use the committed model file. Next: radar nowcasting for more warning time, and live feeds for public transport. FloodSense: earlier, per-zone flood warnings that complement PUB." |

**Don't say:**
- that the app reads from Databricks (it doesn't)
- that gold or the app loads the model from the registry (it's only registered)
- that `ai_query`, radar or tide are in use
- that stations were "disrupted" (we show exposure only)
- that the poller runs continuously (its schedule is paused)
- anything about bus stops or LTA live disruption feeds (not built)

**If you run long:** cut shot 3 and shot 10 first.
