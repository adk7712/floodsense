# FloodSense demo video: script

About 3 minutes. Every time and number below comes from the app and data as of 2 Oct 2026.

**Before recording**
- Check whether the Round 1 form accepts a video link (the brief only asks for the PDF). If it
  doesn't, keep the video for later rounds.
- Start the app: `uv run streamlit run src/floodsense/app/streamlit_app.py`, then open
  http://localhost:8501 in a full-width desktop browser and hide the bookmarks bar.
- Do one full run-through first. It warms the cache, so slider moves are instant on camera.
- Open a second tab on the Databricks workspace: **Jobs & Pipelines → floodsense**, update
  `34cfa680…` (started 19:36 SGT, 2 Oct 2026), with the graph showing row counts.
- The ⋮ menu and **Deploy** button in the top right belong to Streamlit itself. Ignore them, or
  crop them out.

| # | Time | On screen | Say (roughly) |
|---|---|---|---|
| 1 | 0:00–0:15 | The app header | "This is FloodSense: flash-flood risk for each of Singapore's 55 planning areas, for the next hour, built entirely on open data." |
| 2 | 0:15–0:40 | **Live Feed** (opens by default). Point at the green "Live: N of 89 gauges reporting" badge, then the map (URA boundaries, grey gauge dots), then the five cards. | "This is live NEA rain-gauge data. Each zone's rain is interpolated from the gauges that are reporting; a gauge that's offline is left out, never treated as dry. Every zone gets a calibrated chance of a reported flood in the next hour." If it's raining, say so. |
| 3 | 0:40–0:55 | Click **"⟳ Replay the 17 Apr 2021 storm"** (under the rarity gauge). The app switches to Replay at 12:15. | "Now a real storm: 17 April 2021, when Dunearn Road flooded. Same model, the real 5-minute readings from that afternoon." |
| 4 | 0:55–1:40 | Keep **Bukit Timah** selected. Drag the time slider slowly: 12:15 → **12:25** (Moderate) → **12:45** (High) → 13:00. Point at the status, the rolling-rain tiles and the rarity gauge. | "At 12:25 Bukit Timah goes Moderate. At 12:45 it's High: 47 mm in the last hour, at the very top of this zone's 2017–2023 record. The flood on Dunearn Road was reported at about 1:44 pm, roughly an hour later." |
| 5 | 1:40–2:00 | Scroll to **"Reported floods this day"**: the 13:44 Bukit Timah entry and the Jurong East entry, with their source links. | "Every flood we learn from is sourced: a link, a quoted sentence, and a person who checked it. Jurong East also flooded that day; we had it at Moderate from 12:50 and High from 13:15." |
| 6 | 2:00–2:20 | Drag to **13:35** and point at the map's "High (11)" count, then the **Prototype Status** panel. | "To be straight about the cost: at 13:35, 11 zones are on High, and 24 went High at some point that afternoon. Most had no flood report. On floods from 2024 to 2026 that the model never saw, we caught 70% at Moderate and 40% at High, with about 1.6 false High alerts per zone per year." |
| 7 | 2:20–2:35 | In the sidebar, click another major storm, e.g. **"22 Nov 2024 · Sembawang, Toa Payoh, Yishun"**, or pick any date. | "Any day from 2017 to September 2026 can be replayed the same way." |
| 8 | 2:35–2:55 | Databricks tab: the pipeline graph with row counts (949 bronze → 64,223 silver readings → 15,840 gold rows). | "And this runs on Databricks Free Edition: one Lakeflow pipeline, raw files to readings to zone risk, using exactly the same scoring code. On this storm, every zone's risk tier matched our local results." |
| 9 | 2:55–3:05 | Back to the map | "Next: radar nowcasting for more warning time, and live scoring on Databricks. FloodSense: earlier, per-zone flood warnings that complement PUB." |

**Don't say:**
- that the app reads from Databricks
- that the model is registered
- that radar, tide or `ai_query` are working

None of these is built yet.
