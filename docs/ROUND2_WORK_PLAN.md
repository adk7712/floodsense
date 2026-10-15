# FloodSense Round 2: Work Plan

**Team:** adk (model and evidence) and Akul (platform and product)
**Window:** Thu 15 Oct to Tue 27 Oct (Demo Day)
**Version:** v3.2, 16 Oct: B5 stakeholder session made optional by adk. v3.1, 16 Oct: ownership swap accepted by adk (Akul: everything Databricks, incl. B4.4 registration; adk: A5, A7, A9). v3, edited by Akul on 15 Oct (adds the missing feedback points; marks unverified claims as open questions)
**Document structure:** Part 1 is the short plan. Part 2 has the task detail, the split of work, and a summary at the top of each section.

---

## Repo Baseline (checked 15 Oct against adk7712/floodsense, commit 58f1d83)

**Already built (do not re-build):**
- Gold table and live poller. The Lakeflow pipeline produces `flood_risk_predictions_gold` (55 zones × 288 steps). The poller has run end to end on live data (DEPLOYMENT.md §11). Its 30-minute schedule is PAUSED.
- Model registered as `workspace.floodsense.flood_model` v1, alias `champion`. Gold still scores from the local `models/flood_model.joblib` (DEPLOYMENT.md §6, §10).
- AI/BI dashboard reads gold. The Streamlit app does not; it computes risk from the NEA API directly (A2 is needed).
- PUB flood alerts are archived on every poll.
- Labels: 66 events in `data/reference/flood_events.csv`. 36 are from 2017–2023 (training) and 30 from 2024–2026 (test). All 66 were verified by one person (`Akul9`). 64 excluded candidates are logged in `docs/flood_events_rejected.md`.
- Test-set result (scored once): High 12 of 30 (40%, 90% CI 27–53%); Moderate 21 of 30 (70%, 90% CI 57–83%).
- Budgets in `config.py`: 2.5 false High and 11 false Moderate episodes per zone-year. Achieved: 1.56 and 7.28.
- IDW engine in `src/floodsense/spatial/idw_matrix.py`. The kriging comparison plugs in beside it.
- Model selection procedure in `models/model_card.json`: candidates are ranked by out-of-fold event hit rate over 2020–2023. Logistic regression and LightGBM already lost to the rule.
- Test scripts: `test_phase5_parity.py`, `replay_days.py`, `pipeline_core.py`. CI (`.github/workflows/ci.yml`) runs ruff, mypy and pytest on every push.

**Not in the repo (must be built or sourced):**
- Radar: archive done 15 Oct (A1.1). No nowcast code yet. Background: data.gov.sg keeps about 3 months of frames (verified 14 Oct: frames exist from 16 Jul 2026, earlier dates return 404), and each day the oldest day expires.
- Tide: not built, although some labels carry a `rain_tide` cause tag.
- Elevation (DEM) and built-up land use: no file and no code. A licensed source is needed.
- Telegram bot: none.
- Databricks App: not deployed.

---

# PART 1: Plan Ahead

**Goal:** Show a live, Databricks-run flood risk system that is honest about its evidence, with at least one mentor suggestion implemented and measured.

**Scope cuts, in order:**
1. Radar as a *risk input* (the forecast-risk layer, A1.6) goes first if the spike fails on 19 Oct. The radar archive and the **rain-nowcast map overlay** are not cut: they are our clearest answer to the mentor's radar point and our main difference from the other B2 team.
2. Kriging goes second, unless the comparison is trivially in favour of kriging.
3. The Telegram bot goes third.

**Never cut:** the app reading the gold table, the radar archive and nowcast overlay, the rehearsals, the fallback recordings, or the limitations slide.

**Rules:**
- The 2024–2026 test set was scored once. Any retraining with labels from those years must be reported as a second look.
- Feature freeze is 23 Oct at 18:00 SGT. After that, only bug fixes.
- Each person's work must not block the other's for more than half a day. Part 2 lists the handoffs.

**Timeline:**

| Dates | Milestone |
|---|---|
| 15 Oct | **Start the radar backfill and daily archiver (frames are expiring daily)** |
| 15–16 Oct | Shared setup and interface contracts; label batch 1 drafted; test second-user access and `ai_query` on Free Edition |
| 17–19 Oct | Radar spike and go/no-go (19 Oct); static features delivered (18 Oct); app reads gold |
| 20–21 Oct | Candidate model v2 registered |
| 22 Oct | Radar integration if go; retrain |
| 23 Oct | Feature and model freeze (18:00) |
| 24 Oct | Slides and architecture visuals |
| 25–26 Oct | Two rehearsals; fallback recordings; final workspace check |
| 27 Oct | Demo Day |

---

# PART 2: Detailed Instructions

## Section 0: Shared Setup and Contracts

**Summary:** Both people do this on 15 Oct. It fixes the interfaces so that the two tracks can run in parallel.

**Owner:** both, about 2 hours.

**Tasks:**
- **0.1** Agree the freeze date and the test-set rule (in Part 1). Record it in the repo `README` under "Evaluation protocol".
- **0.2** Write the static-features contract as a file, `data/reference/zone_static.csv`. Columns: `ura_planning_area`, `built_up_pct`, `elevation_m_mean`, `source_name`, `source_url`, `retrieved_at`. Every zone must have a row, with no blanks. adk produces it. Akul reads it in the pipeline.
- **0.3** Agree the label-file schema. It is the existing `flood_events.csv`. New rows must set `verified_by` to the initials of the person who opened the source.
- **0.4** Agree the registry rule. Each candidate model is registered with `register_model.py` and gets the alias `candidate`. Only the alias `champion` is used for serving. Promoting a candidate to `champion` happens on 23 Oct only.
- **0.5** Create a shared task board with the IDs below.
- **0.6** Test the Free Edition unknowns on day 1 (see Open Questions 5–8): can a second user join the workspace, does `ai_query` work, and how do Databricks Apps behave (auto-stop?). Record the answers in the repo.
- **0.7** Spend 30 minutes on the other B2 team: is their Round 1 entry visible on Devpost, and where should we differentiate? Record the findings.

**Done when:** both people have the contract files in the repo, and the board lists every task in Part 2.

---

## Track A: platform and product (Akul, except A5, A7 and A9: adk, since the 16 Oct swap)

### Section A1: Radar Archive and Nowcast Spike

**Summary:** Start the radar archive today (it keeps only about 3 months). Build a rain nowcast and show it on the map. Test whether frame-to-frame motion gives useful skill at 30 and 60 minutes. Decide on 19 Oct whether radar also feeds the risk (A1.6).

**Owner:** Akul. **Depends on:** nothing. **Feeds:** the map overlay (always), and A1.6 only if go. The repo has no radar code yet.

**Already researched (14 Oct, don't redo):** endpoints `https://api-open.data.gov.sg/v2/real-time/api/weather-radar-images/70km` and `/240km`. They work with our API key (`x-api-key` header). Each record has a timestamp, a rain-intensity **PNG** (an S3 link) and a **bounding box** with a projection. There is one frame every 5 minutes, 25 records per page, paginated. `?date=YYYY-MM-DD` works from **16 Jul 2026**; earlier dates return 404. It is **not Doppler data**: there is no velocity field, so motion must be estimated from consecutive frames.

**Tasks:**
- **A1.1** ✅ **Done 15 Oct:** 16 Jul to 14 Oct backfilled (91 days, 51,665 frames) in `workspace.floodsense.radar`; the daily job `floodsense-radar-archiver` runs at 03:00 SGT (see DEPLOYMENT.md §13). Original task: backfill every frame from 16 Jul to now into a Unity Catalog volume, and schedule a **daily** job that pulls the previous day. Polling every 5 minutes isn't needed, because the API keeps about 3 months. Check frames per day against the expected 288.
- **A1.1b** ✅ **Done 15 Oct:** see `docs/radar_calibration.md` and `floodsense.radar` (33-colour scale calibrated against gauges: hourly corr 0.92 on a held-out month, totals within 3%). Original task: Convert each PNG to rain rate (mm/h) using the radar colour legend, and georeference it to the URA planning areas using the record's bounding box. Every later step depends on this.
- **A1.2** ✅ Done 15 Oct (`floodsense.radar.nowcast`, OpenCV Farnebäck flow). Estimate motion between consecutive frames (for example with pysteps optical flow). Extrapolate to 30 and 60 minutes.
- **A1.3** ✅ Done 15 Oct (`floodsense.radar.evaluate_nowcast`). Only 4 of the 66 labelled events fall inside the 3-month radar window, and all 4 are in the 2024–2026 test set. They cannot be used for evaluation. Score nowcast skill instead on gauge-interpolated rainfall at all storm time steps in the window, against persistence at 30 and 60 min. Do not use the event labels. Use the existing `replay_days` and `pipeline_core` functions.
- **A1.4** ✅ Done 15 Oct: `docs/radar_nowcast_result.md`, recommending **GO** (team decides 19 Oct). Write a one-page result: skill at 30 min and 60 min compared with persistence (no motion).
- **A1.5** ✅ Done 15 Oct (Radar control above the map: Off / Now / +30 / +60 min). **Map overlay (always built, regardless of go/no-go):** show where rain is heading over the next 30–60 minutes on the app's map, labelled as a rain nowcast.
- **A1.6** ✅ Built 15 Oct ('Forecast risk (experimental)' toggle; keep or drop at the 19 Oct go/no-go). **Forecast-risk layer (only if go):** radar does not need to be a *trained* feature. The nowcast predicts upcoming rain, and the existing calibrated model already turns rain into risk, so we can show "forecast risk in 30–60 min" **without retraining**. Its flood skill can't be validated on labels yet (see the go criteria), so label it as experimental and show only its rain skill from A1.4.

**Go criteria:** the nowcast beats persistence at 30 min on gauge-interpolated storm steps in the radar window. If it does not, the decision is no-go for A1.6 (the overlay in A1.5 stays). Radar cannot be a trained model feature: the archive is 3 months long, against 2017–2023 for training.

**Done when:** a go/no-go note is in the repo by 19 Oct.

### Section A2: App Reads the Gold Table

**Summary:** The largest gap in the current build. The dashboard must read `flood_risk_predictions_gold` rather than calling the API.

**Owner:** Akul. **Depends on:** nothing. **Feeds:** A5, A6, J1.

**Tasks:**
- **A2.1** Add a data-source switch in the Streamlit app: "Databricks gold" (the default) or "live API" (fallback).
- **A2.2** Query the gold table through the Databricks SQL connector. Use the latest timestamp per zone. Keep the existing KPI and map code unchanged.
- **A2.3** Show the data's age in SGT on the page. If it is older than 60 minutes, show a visible warning.
- **A2.4** Test on the replay date, plus a live check.
- **A2.5** Credentials and cost: run locally, the app needs a Databricks token kept in a secret (never in the repo). Each query uses SQL warehouse compute, which counts against the daily cap, so cache results, e.g. for the poller's interval. As a Databricks App (A6), sign-in is built in.

**Done when:** the app shows current risk from gold, and the fallback switch works.

### Section A3: Poller and Pipeline Operations

**Summary:** Keep the poller job healthy. Unpause its schedule for the demo window only, and watch the quota.

**Owner:** Akul. **Depends on:** nothing. **Feeds:** A2, J1.

**Tasks:**
- **A3.0** Check that the secret `floodsense/data_gov_api_key` exists. Without it the anonymous API rate-limits the poller (DEPLOYMENT.md §11).
- **A3.1** Confirm the poller job runs every 30 minutes with no failures for 48 hours before the demo. Use the Jobs UI run history.
- **A3.2** Set up a simple alert on job failure (email).
- **A3.3** Set the schedule to run from 24 Oct 12:00 SGT onwards and keep it running through the demo. Free Edition has a daily compute cap. Record usage per day, and restore the paused state if the cap gets close.

**Done when:** 48 hours of clean runs are logged.

### Section A4: Serve the Model from Unity Catalog

**Summary:** The pipeline loads the registered model rather than the local file. This is a visible Databricks step for the judges.

**Owner:** Akul. **Depends on:** adk's trained candidate, which Akul registers (B4.4, by 21 Oct). **Feeds:** J1.

**Tasks:**
- **A4.1** Change the gold scoring code (`databricks/pipelines/lakeflow_pipeline.py`) to load `models:/workspace.floodsense.flood_model@champion` instead of `models/flood_model.joblib`. Version 1 is already registered and loads back with identical tiers (DEPLOYMENT.md §10), so this is a code change, not a new registration.
- **A4.2** If static features are in the model, read `zone_static.csv` from the artifacts volume in the pipeline. Confirm the row count is 55.
- **A4.3** Re-run the replay parity test (`test_phase5_parity.py`) on the new model. It must pass, or the difference must be documented.
- **A4.4** Promote the candidate to `champion` on 23 Oct after adk signs off.

**Done when:** gold is scored from `champion`, and the parity result is recorded.

### Section A5: Telegram Alert Bot

**Summary:** A simple alert bot that posts Moderate and High changes for each zone. It shows the impact in a form residents can use.

**Owner:** adk (was Akul; swapped 16 Oct). **Depends on:** A2 (gold is live). **Feeds:** J1.

**Tasks:**
- **A5.1** Create a bot and a demo channel.
- **A5.2** Write a job that reads gold each run and posts when a zone's tier changes. Include the zone, the tier, the SGT time, and "what to do" text (for example, avoid underpasses).
- **A5.3** Add a rate limit: at most one message per zone per hour.
- **A5.4** **Replay mode:** the bot reads live gold, so on a dry demo day it posts nothing. Add a mode that feeds a replayed storm (17 Apr 2021) through the same tier-change logic and posts to the demo channel. Record it as the fallback too.

**Done when:** the demo channel receives a message on a replayed storm.

### Section A6: Databricks App

**Summary:** Deploy the app as a Databricks App, straight after A2. It also solves A2's sign-in problem, and it's a visible Databricks step for the judges.

**Owner:** Akul. **Depends on:** A2. **Feeds:** J1.

**Tasks:**
- **A6.1** Add the app configuration and deploy it to the final workspace.
- **A6.2** Test that it opens from a fresh browser session.
- **A6.3** Check how Free Edition apps behave over time (whether they auto-stop is **unverified**; see Open Question 7). If they do, restart before each rehearsal and before the demo.

**Done when:** the app opens from its URL. If it does not work by 25 Oct, keep the Streamlit demo running locally and show the Databricks App as roadmap.

### Section A7: LLM-Drafted Flood Candidates

**Summary:** adk drafts candidate flood events so Akul can verify them (roles reversed by the 16 Oct swap). This feeds B1 and removes the main bottleneck. It's a large task: consider splitting the drafting by year range, with each person verifying the other's drafts.

**Sources, best first:**
- PUB's own flash-flood posts on X/Twitter and Telegram (official, with exact times and road names)
- news archives: Straits Times, CNA, TODAY, Mothership
- parliamentary replies that list each year's flood locations

**Owner:** adk (was Akul; swapped 16 Oct). **Depends on:** nothing. **Feeds:** B1 (verification).

**Tasks:**
- **A7.1** Test `ai_query` on Free Edition with one model. If it fails, use an external model through the API, and record which one.
- **A7.2** Draft candidates from PUB Telegram posts and news for 2017–2023 only, in batches of 20. Do not draft 2024–2026 candidates before the freeze, because those years are the test set. Each candidate needs: zone, date, time (or precision), source URL, and a quote naming the place and time.
- **A7.3** Post each batch to the shared board with its ID. Hand each batch to Akul for verification within 4 hours.
- **A7.4** Reject candidates without a usable source. Keep a list of rejections with reasons, in `docs/flood_events_rejected.md` (already in the repo, 64 lines).

**Done when:** at least 6 batches are delivered by 19 Oct.

### Section A8: Governance and Stretch Items

**Summary:** Make sure the tags and descriptions on tables are complete. Asset Bundles is a stretch and will only be done if everything else is finished.

**Owner:** Akul. **Depends on:** nothing. **Feeds:** J3.

**Tasks:**
- **A8.1** Check that every table, volume and model has a description and tags. Fill the gaps.
- **A8.2** (Stretch, only after 23 Oct) Package the pipeline and job with Asset Bundles.

**Done when:** A8.1 is complete. A8.2 is optional.

### Section A9: Resident / Town-Council View (UX)

**Summary:** Solution and UX is 20% of the Demo Day score, and the current app is built for analysts. Add a simple view for a non-technical user.

**Owner:** adk (was Akul; swapped 16 Oct). **Depends on:** A2. **Feeds:** J1.

**Tasks:**
- **A9.1** A simple view: "your area → the risk now (and forecast, if A1.6 is go) → what to do". Large text, no charts.

**Done when:** the view is in the app and used in the demo flow.

---

## Track B: adk (model and evidence)

### Section B1: Label Verification

**Summary:** Turn Akul's candidates into verified training labels. The target is 60 or more new verified events by 19 Oct.

**Owner:** adk. **Depends on:** A7 batches. **Feeds:** B4.

**Tasks:**
- **B1.1** For each candidate, open the source. Confirm the place, the time and the quote.
- **B1.2** Set `time_precision` (`exact`, `approx_15min`, `approx_hour` or `day_only`) to match what the source actually says.
- **B1.3** Record `verified_by`. Move rejections to `docs/flood_events_rejected.md` with a reason.
- **B1.4** Run `load_flood_events()` after each batch to confirm the file validates.

**Checkpoint:** 60 or more new verified events by 19 Oct, on top of the 36 training events already in the file. New rows are verified by someone other than the drafter, so each label has two people's checks. Since the 16 Oct swap adk drafts (A7), so the second check is presumably Akul's: **confirm with adk**. The existing 66 were all verified by one person.

**Done when:** the checkpoint is met, or the shortfall is documented.

### Section B2: Static Zone Features

**Summary:** Mentor suggestion. Add elevation and built-up share per zone as static features. Test whether they help in cross-validation.

**Owner:** adk. **Depends on:** nothing. **Feeds:** A4 (through the contract file), B4.

**Tasks:**
- **B2.1** Find and licence the elevation (DEM) and built-up land-use data. Neither is in the repo. **Researched sources:**
  - Elevation: **Copernicus GLO-30** (open, 30 m). The mentor linked NextGIS, which is a third-party product; prefer Copernicus to cite. There is no DEM on data.gov.sg.
  - Built-up: **ESA WorldCover** (open, 10 m, has a built-up class) or **URA Master Plan 2019 land use** (data.gov.sg).
  - PUB drainage capacity and catchment boundaries don't look openly available. **Unverified**: check before promising them.

  Record each licence. The repo's PUB Flood Prone Areas file (2022–2025) is a city-wide total per year, not per zone, so it cannot be a zone feature as it stands. The unsourced hand-assigned `pub_monitored` feature was dropped from the model.
- **B2.2** Compute mean elevation and built-up percentage per zone, using `ura_planning_areas_mp2019.geojson`. Also consider the **% of the zone that's low-lying**: zone averages blur the local low points where flash floods happen (e.g. Dunearn Road).
- **B2.3** Save `data/reference/zone_static.csv` with 55 rows, all present, following the contract in Section 0.2.
- **B2.4** Run a cross-validation ablation on 2020–2023 only. Do not use the test years.

**Done when:** `zone_static.csv` is in the repo by 18 Oct, and the ablation result is written up.

### Section B3: Kriging vs IDW Comparison

**Summary:** Mentor suggestion. Compare the current IDW interpolation with ordinary kriging, using held-out gauges. Expect a small gain: the gauges are a few km apart and storm cells are often smaller, so kriging can't recover rain no gauge measured. Radar is the real fix for spatial coverage.

**Owner:** adk. **Depends on:** nothing. **Feeds:** B6 (metrics slide).

**Tasks:**
- **B3.1** IDW lives in `src/floodsense/spatial/idw_matrix.py`; add ordinary kriging beside it. Use leave-one-gauge-out validation on storm time steps. For each held-out gauge, predict its rainfall from the others with IDW and with ordinary kriging.
- **B3.2** Report RMSE and bias separately for light and heavy rain.
- **B3.3** If kriging is clearly better, add it as a pipeline option. If not, report the result and keep IDW.

**Done when:** the comparison table is written up by 20 Oct, with the decision.

### Section B4: Retrain, Register and Evaluate

**Summary:** Build the candidate model using verified labels and static features (if they help), and register it for Akul to serve.

**Owner:** adk. **Depends on:** B1 (by 20 Oct) and B2 (by 18 Oct). **Feeds:** A4, B6.

**Tasks:**
- **B4.1** Rebuild the feature store with the verified events. In the same rebuild, **fix the known issues** instead of listing them as limitations: give the 0.2 mm active-rain gate a tolerance in both `build_features` and `scoring`, and drop the unused `pub_monitored` and `return_period_years` columns. Re-run the parity test afterwards.
- **B4.2** Rerun the existing selection procedure (`models/model_card.json`) on 2020–2023 only, with the new labels and static features. The current winner is `rule_rain60`, a rule rather than a learned model. Report the result against that winner.
- **B4.2b** **Add a candidate that can use the static features.** The rule shares one threshold across all zones, so B2's features can't change anything unless a candidate takes them. Suggested: the 60-minute rain rule plus a small, regularised per-zone adjustment driven by the static features. Also re-run LR and LightGBM with the larger label set: they lost with 36 training events but might not with 100+.
- **B4.3** Radar is not a trained feature (3 months of data). If the spike is a go, it is used through A1.6, the forecast-risk layer, without retraining.
- **B4.4** Register the candidate with alias `candidate` by 21 Oct. Since the 16 Oct swap, Akul does the registration once adk hands over the trained candidate.
- **B4.5** Freeze on 23 Oct. Set thresholds with the existing budget rule. Do not change the budgets to improve the result.
- **B4.6** Report the cross-validation results. If the 2024–2026 test years are used at all, label that as a "second look".

**Done when:** the candidate is registered and the report is written. Thresholds are set by the 23 Oct freeze.

### Section B5 (optional): Stakeholder Feedback

**Summary:** Optional. If time allows, show the app to one real person who would use it (a town council officer, a residents' committee member or a community volunteer) and note their feedback. It would strengthen the social-impact story (30% of the score), but nothing else depends on it.

**Owner:** adk, if pursued. **Depends on:** nothing (the app and resident view already work). **Feeds:** B6 (an optional quote on a slide).

**If pursued:**
- Run a 10–15 minute session by 22 Oct: show the resident view and ask what they would do with a High alert, and when.
- With consent, write down their answers word for word; never paraphrase into a quote.
- Note one finding and one suggested change, and put them on a slide only if the session happened.

**Done when:** optional; skipped is fine.

### Section B6: Deck and Metrics Write-up

**Summary:** Turn the results into the final deck. Report rates with uncertainty, not single numbers.

**Owner:** adk, with Akul contributing the architecture slide. **Depends on:** B4, A2. **Feeds:** J3.

**Tasks:**
- **B6.1** Report metrics as rates with 90% intervals and raw counts. For example: "12 of 30 floods caught, 90% interval". Include the false-alarm rate per zone-year.
- **B6.2** Add a limitations slide. Cover: the label count, the test-set status, and the radar scope. The gate rounding and the legacy columns are fixed in B4.1, so they don't belong here; state the fix instead.
- **B6.3** Credit all datasets, libraries and models.
- **B6.4** Draft ready 24 Oct. Both people review it.

**Done when:** the reviewed deck is saved as PDF in the repo.

---

## Joint Work

### Section J1: Integration and Demo Flow

**Summary:** Freeze the code on 23 Oct, then test the full demo path twice from a fresh browser.

**Owner:** both. **Depends on:** A2, A4, A5, B4.

**Tasks:**
- **J1.1** Before the freeze, make sure CI is green (ruff, mypy, pytest). Freeze and tag the commit on 23 Oct at 18:00 SGT.
- **J1.2** Use this demo order: open the app (live, from gold) → map with the radar nowcast overlay → resident view (A9) → replay 17 Apr 2021 → click the High zone → show the warning time → Telegram alert (replay mode, A5.4) → Databricks: pipeline, model in Unity Catalog, dashboard.
- **J1.3** Prepare a fallback (recording or screenshot) for each live step.
- **J1.4** Run the whole flow end to end from a fresh browser, twice.

**Done when:** two clean end-to-end runs are logged.

### Section J2: Rehearsals and Q&A

**Summary:** Two timed rehearsals, with notes on failures, and prepared answers for likely questions.

**Owner:** both.

**Tasks:**
- **J2.1** Rehearsal 1 on 25 Oct. Time it and note every failure.
- **J2.2** Rehearsal 2 on 26 Oct. Confirm every fallback works.
- **J2.3** Prepare answers to likely questions:
  - Why not full radar?
  - What is the false-alarm rate?
  - How are the labels verified?
  - What happens during NEA downtime?

**Done when:** both rehearsals are done and the Q&A answers are written down.

### Section J3: Final Workspace and Submission

**Summary:** Check the final workspace, scan for secrets, and submit before the deadline.

**Owner:** Akul for the workspace; adk for the submission.

**Tasks:**
- **J3.1** (Akul) Confirm the pipeline, poller, app and model are all in the final workspace. Check the quota. Second-user access is tested on day 1 (0.6), not here.
- **J3.2** (adk) Scan the repo and the deck for tokens, passwords and personal data. Remove anything found.
- **J3.3** (adk) Submit the deck and links before the deadline. Keep a PDF copy of what was submitted.

**Done when:** the submission confirmation is saved.

---

## Dependency Map

Critical handoffs between the two tracks:

| Handoff | From → To | Due | Risk | Mitigation |
|---|---|---|---|---|
| Verified label batches | A7 → B1 | 4 h after each batch | Low | Small batches |
| `zone_static.csv` | B2 → A4 | 18 Oct | Medium | Must match the contract in 0.2 |
| Radar go/no-go | A1 → B4.3 | 19 Oct | Low | Retrain does not wait for it |
| Candidate registered | B4.4 → A4 | 21 Oct | Medium | A4 can use the current `champion` |
| Gold live | A2/A3 → B6 | 20 Oct | Low | Fallback to live API switch |
| Feature freeze | both → J1 | 23 Oct 18:00 | Hard deadline | No changes after freeze |

**Key design:** the app and the bot read the gold table, not the model directly. This means adk can retrain without blocking Akul. Akul switches the `champion` alias only after adk signs off.

---

## Demo Day Criteria → Plan Items

| Criterion (weight) | Plan items |
|---|---|
| Social impact (30%) | A5 Telegram alerts, A9 resident view, B5 (optional) stakeholder feedback |
| Technical execution on Databricks (30%) | A1 radar archive, A2 app on gold, A3 live poller, A4 registry serving, A6 Databricks App, A7 `ai_query`, A8 governance and Asset Bundles |
| Solution and UX (20%) | A1.5 nowcast overlay, A1.6 forecast risk, A9 resident view, per-zone explanations |
| Data rigour (10%) | B1 labels, B2 static features, B3 kriging, B4 fixes, the test-set protocol |
| Presentation (10%) | J1–J3, the fallback recordings |

---

## Definition of Done

The project is ready for Demo Day when all of these are true:

- [ ] The live app reads gold from Databricks and shows a data-age warning.
- [ ] The model is served from `champion` in Unity Catalog. The parity test passes, or any difference is documented.
- [ ] The poller runs on schedule, with no failures in the 48 hours before the demo.
- [ ] At least 100 new verified events exist (2017–2023 only, on top of the 36 already in the file), each with a source and a verifier other than the drafter. If not, the shortfall is documented.
- [ ] At least one mentor suggestion is measured, and the result is on a slide.
- [ ] The radar archive is complete from 16 Jul. The nowcast overlay is on the map. Forecast risk (A1.6) is shown as experimental if go; otherwise the spike result is on a slide.
- [ ] The resident view (A9) is in the app.
- [ ] Metrics are reported honestly, with intervals and a limitations slide.
- [ ] Two rehearsals are done, and a fallback exists for each live step.

---

## Open Questions

1. **Label target.** The teammate's goal is "a few hundred" new floods. The plan says 100. The 2017–2023 window has 36 events now, and new candidates there must not touch the test years. Confirm the supply before promising a number.
2. **PUB X/Telegram history.** Confirm how far back PUB's posts are available and whether they can be archived legally and reliably.
3. **Radar role.** Confirm the team accepts radar as a nowcast layer evaluated on storm steps, not a retrained model feature.
4. **Second user on Free Edition.** Can both of us use one workspace? Test on day 1 (0.6).
5. **`ai_query` on Free Edition.** Does it work, and with which model? Test on day 1 (A7.1).
6. **Databricks Apps on Free Edition.** Do they auto-stop, and after how long? Unverified.
7. **Daily compute cap.** Not published, and the usage table isn't available, so we can only watch for failures. It affects A3 (poller schedule) and A2 (query caching).
