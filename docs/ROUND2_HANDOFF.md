# FloodSense: Round 2 handoff (the one status file)

**Last updated:** 16 Oct 2026, evening, by adk's session.
- master: `82f1cb6` (Akul: data.gov.sg fallback behind an Advanced button; no stale-data banner).
- adk's branch `adk/labels-kriging-zone-features` (pushed, head `49969c4`): B4.1, B3, A5, A7.2, B2, A9, B4.2/B2.4 and this file (`c4339ce`, `49969c4`), with master merged in up to `82f1cb6` (`32ca4fd`). **Waiting for Akul's review before it goes to master.**

**Read first in a new session:** this file, then `docs/ROUND2_WORK_PLAN.md` (the Round 2 plan).
- Details live in `README.md` (data, training, evaluation honesty), `DEPLOYMENT.md` (Databricks, §1–14)
  and `docs/` (radar calibration, nowcast results, kriging vs IDW).
- This file replaces `docs/PROJECT_STATUS.md` and is **committed to git**, so both people's agents
  read the same record. The workspace IDs in it are already public in `DEPLOYMENT.md`. Never put
  secrets, tokens or personal emails here.
- **Update this file at the end of every work session:** what was done (with commits), what's
  waiting on the other person, the open decisions and the next steps.

---

## 0. Latest session (16 Oct, adk): done, waiting, next

**Done**, on branch `adk/labels-kriging-zone-features`, not yet on master:

| Commit | Plan item | What |
|---|---|---|
| `48b86ea` | B4.1 | Active-rain gate tolerance (`active_rain_mask()` in `common/config.py`, 1e-6 mm), used in training and scoring. Dropped the unused `pub_monitored` and `return_period_years`. 8 more training rows pass the gate (13,250 vs 13,242 in the train-CLI test). Parity test passes. The feature store was rebuilt in B4.2 (`6ce0431`). **The champion has not been retrained since this fix** (see "B4 status" in §0). |
| `1fc34b1` | B3 | Ordinary kriging (`spatial/kriging.py`) vs IDW, leave-one-gauge-out, on 4,000 storm steps from 2017–2023 (`docs/kriging_vs_idw.md`). Heavy rain: RMSE 26.40 vs 27.31 mm/h; difference −0.91 (90% CI −0.98 to −0.85), about 3%. Light rain: kriging is slightly worse. Both methods underestimate heavy cells by about 16 mm/h. **Recommendation: keep IDW, put the result on the mentor slide.** Team to confirm. |
| `b240b41` | A5, A5.4 | Telegram bot (`floodsense.alerts.telegram_bot`). Replay of 17 Apr 2021 with `--dry-run` gives 67 messages, 24 of them High. At most one message per zone per hour, **except** that a rise above the last tier sent gets through (adk approved 16 Oct; otherwise no High is ever sent). The live provider is a stub until adk writes the gold wrapper (see "Next for adk", item 1). |
| `fef4165` | A7.2 | 14 new **unverified** label candidates (11 from 2017–19, 3 from 2020–21, 0 from 2022–23) in `flood_events.csv`, plus 122 rejection entries in `docs/flood_events_rejected.md`. Training leaves them out until `verified_by` is filled. |
| `3fdc66c` | B2 | `data/reference/zone_static.csv`: 55 rows, sourced, with `built_up_pct`, `elevation_m_mean` and `low_lying_pct` (under 5 m) from Copernicus GLO-30 and ESA WorldCover 2021. `rasterio` is an optional `static` extra. Licences are in the README. Downtown Core is only 60% built-up because of Marina Bay water and trees. Bukit Panjang has the highest mean elevation (48.8 m). |

**Waiting on Akul:**
1. **Review and merge** `adk/labels-kriging-zone-features` into master.
   - `uv.lock` was bumped to revision 5 by uv 0.13.1. Update uv if `uv sync --locked` fails.
   - The gold pipeline uses the package, so the gate tolerance reaches gold on the next deploy.
2. **Verify the 14 new label rows** (all 14 are unverified: `verified_by` is empty). adk drafted them, and the verifier must be a different person, so Akul checks. For each row, open the source and fill `verified_by` with your initials, or move the row to `docs/flood_events_rejected.md` with the reason. Training ignores any row with an empty `verified_by`.
   - The 14 rows (date, place, planning area):
     - 2017-10-30 Mount Elizabeth (Newton); 2017-10-30 Stamford Rd / Victoria St (Downtown Core)
     - 2018-01-09 Craig Rd (Outram); 2018-01-30 Seletar North Link (Seletar)
     - 2018-05-26 Kramat Lane (Orchard); 2018-06-26 Toh Tuck Ave (Clementi)
     - 2018-06-26 Lorong Kismis (Bukit Timah); 2018-06-29 Lorong Kismis / Dunearn Rd (Bukit Timah)
     - 2018-09-19 Ang Mo Kio Ave 6 (Ang Mo Kio); 2018-11-11 Geylang Lorong 23 (Geylang)
     - 2019-06-03 Rochor Rd (Downtown Core); 2020-11-02 Hougang Ave 3 (Hougang)
     - 2021-07-12 Dunearn Rd near Sime Darby Centre (Bukit Timah); 2021-07-12 TPE slip road to Punggol Rd (Sengkang)
   - Check these closely:
     - **Timing:** the Ang Mo Kio (2018-09-19) and Kramat Lane (2018-05-26) rows use PUB's alert or tweet time as an `exact` start. They may need `approx_15min`.
     - **Planning area near a boundary:** Rochor Rd (Downtown Core or Rochor), Toh Tuck Ave (Clementi or Bukit Timah) and the TPE slip road (Sengkang or Punggol).
     - **Not rain-driven:** Hougang Ave 3 (2020-11-02) was caused by construction runoff, so it may not belong in a rain model.
   - **Not one of the 14, but worth a look:** the 2023-07-20 Dunearn Rd row is already verified (`Akul9`). Its severity "Minor" conflicts with a later Mothership article, which says only one of three lanes was passable. Recheck the severity.
3. **Done by Akul:** A1 (radar archive and nowcast), A2 (`d484942`, Live mode reads gold), A6 (`f02fc15`, Databricks App deployed) and the data.gov.sg fallback (`82f1cb6`). Nothing else is open on Akul's list for these.
4. **B4.4:** nothing new to register on 21 Oct. `rule_rain60` stays champion, pending adk's sign-off. If the 14 labels change the picture, see "B4 status" below.

**Open decisions:**
- **Model sign-off (adk):** keep `rule_rain60` as champion (see B4.2 above).
- **A9 wording (adk):**
  - Low advice text: "No action needed. Carry on as normal, and check PUB's channels if the weather changes."
  - Forecast caption: A1.6 says to label it "experimental", which breaks the UI rule. Proposed caption: "Forecast from radar; flood accuracy not yet tested."
  - Whether Live mode shows active PUB alerts for the chosen area.
- **Label target (team).** Web sources are nearly used up: 14 new candidates against a target of 60 (the Definition of Done says 100). Agree a realistic number and document the shortfall.
  - Leads not tried: the ERL paper's supplement (`erlad975csupp1.docx`, https://iopscience.iop.org/article/10.1088/1748-9326/ad975c). Its download page has a CAPTCHA, so a person has to download it in a normal browser; use only the 2022–23 rows.
  - The channel `t.me/sgflood`.
  - Wayback copies of PUB's X/Facebook posts from 2017–2021.
  - Hansard, through a browser.
- **Kriging (team).** Keep IDW (recommended) or add kriging as an option before the freeze.

**B4 status: paused until the 14 labels are verified.**
- Once Akul has verified them: (1) adk re-runs `python -m floodsense.models.select_candidates`; (2) retrain the champion only if the tier cut-offs change; (3) hand any candidate to Akul, ideally by 21 Oct (a registration deadline, not a start date); (4) adk signs off at the 23 Oct freeze.
- **The champion `models/flood_model.joblib` was not retrained after the gate fix.** The CV thresholds were identical (0.003 / 0.00706), so no change is expected. This is an inference, not a re-run.
- Current tier cut-offs: Moderate from 0.30%, High from 0.71%.
- If Akul can't verify in time, keep the current model and document the shortfall.

**Done later on 16 Oct (adk), pushed in `1bfd0be` (A9) and `6ce0431` (B4.2/B2.4); this file and the README note in `c4339ce` and `49969c4`:**
- **B4.2 / B4.2b / B2.4** (`docs/candidate_selection_2026-10.md`, `models/candidates/`):
  - Feature store rebuilt with the gate fix: 7,587,282 rows (+10,153, all one-tip rows).
  - `rule_rain60` reproduces the model card: High 12/23 (90% CI 35–70%), Moderate 17/23 (57–87%), on 2020–23 cross-validation.
  - New candidate `rule_rain60_static`: the rule plus one shrunk coefficient per static feature.
  - **The static features hurt.** High 6/23 vs 12/23 (−26 pts, 90% CI −43 to −9); Moderate 12/23 vs 17/23 (−22 pts, −39 to −4). The fit learns "built-up zones flood more", which mostly reflects where floods get reported.
  - LR and LightGBM, with or without the static features, are still far behind.
  - **Recommendation: keep `rule_rain60`; nothing new to register on 21 Oct** (adk to sign off).
  - A stronger shrinkage (prior SD 0.1) matched the rule post hoc. It is not adopted: it was chosen after seeing the folds. Re-check it after the 14 labels are verified.
  - If a candidate is ever registered, `databricks/register_model.py` needs a path option, and `--alias candidate`.
- **A9 resident view** (`src/floodsense/app/resident_view.py`):
  - An "Analyst view | Resident view" switch under the title. It shows your area, the risk now, the risk in 1 hour (when radar exists), what to do, the data time, and the PUB disclaimer.
  - Same data as the map (gold in Live).
  - Checked in light and dark, and with radar on 30 Sep 2026 at 14:30.
  - Open: the Low advice wording ("No action needed. Carry on as normal, and check PUB's channels if the weather changes."); whether to show active PUB alerts in Live; the forecast caption.

**Limitations and loose ends (be aware before relying on today's work):**
- **Labels:** only 4 of the 14 new candidate rows had their source quote re-checked by adk's session (2021-07-12 Dunearn Rd, 2021-07-12 TPE slip road, 2018-09-19 Ang Mo Kio, 2018-06-26 Lorong Kismis/Toh Tuck). The other 10 rely on the drafting agent's own check that each quote appears on the fetched page. **None is verified yet.** That is Akul's job (fill `verified_by`).
- **Secondary sources:** several label sources are reposts (a wildsingaporenews blog copying CNA, ST or TODAY text) or secondary pages (ADInet, an archived The Online Citizen piece). The original articles were not fetched. Each row says so in `notes`.
- **Label target:** 14 new candidates against a plan target of 60 (the Definition of Done says 100). 2022–23 added none.
  - Not tried: the ERL paper supplement (CAPTCHA, needs a person in a browser), `t.me/sgflood`, Wayback copies of PUB's X/Facebook posts, and Hansard through a browser.
- **Model results (B4.2/B2.4):** they use only the 66 verified labels (23 floods in the 2020–23 folds), so the intervals are wide. Re-run after the 14 rows are verified.
- **Feature store gap:** the rebuilt feature store has 46 more 2017–23 rows than the model card counted under the old gate. Probably the 2 Oct rainfall gap-fill files. **UNVERIFIED.**
- **Static candidate:** the post-hoc "prior SD 0.1" static candidate matched the rule on the same folds. It is not adopted, because it was chosen after seeing the results.
- **Kriging:** kriging vs IDW was measured on gauge interpolation only, not on flood detection.
- **Resident view (A9):**
  - The "In 1 hour" line was checked on 30 Sep 2026 at 14:30 and shows. At 15:00 and 15:30 it did not appear (radar nowcast not available for those times; cause not investigated).
  - The 17 Apr 2021 demo replay has no radar, so the forecast line never shows there.
  - Re-checked after merging Akul's `82f1cb6` (no stale-data banner, fallback behind Advanced): the resident view follows the same data source and has no data-age banner.
- **Telegram bot:** it has never sent a real message (dry-run only). Live mode is a stub until a gold provider exists. The bot and the demo channel are not created yet.
- **uv:** checked on 16 Oct. uv 0.13.1 (Homebrew, already the newest) passes `uv lock --check`, `uv sync --locked --all-extras --group dev` (what CI runs) and `uv pip check`; no pip-installed packages in `.venv`; `requirements.txt` matches a fresh export. `uv.lock` is lockfile revision 5; which older uv versions can read it is UNVERIFIED, so **Akul should run `brew upgrade uv` (0.13.1 or newer) before `uv sync --locked --all-extras --group dev`.** Regenerate `requirements.txt` only with the exact command in its header (it includes `--extra radar`, which keeps OpenCV for the Databricks App).
- **Rejected candidate model not uploaded:** `models/candidates/rule_rain60_static.joblib` is the saved copy of the zone-feature candidate. It is **not committed** (now in `.gitignore`) because it did worse than `rule_rain60` in cross-validation (High 6/23 vs 12/23). Its results are in `models/candidates/selection_2026-10-16.json` and `docs/candidate_selection_2026-10.md`, and `python -m floodsense.models.select_candidates` rebuilds it. The champion `models/flood_model.joblib` is unchanged.
- **Drafts:** the forecast-risk caption and the A9 Low-advice wording are drafts, pending a team decision.

**Before handing off to Akul (adk's checklist):**
1. ~~Commit and push the branch~~: done 16 Oct (`c4339ce` and earlier).
2. Sign off on the model decision: keep `rule_rain60` as champion. Tell Akul nothing new needs registering on 21 Oct.
3. Decide the A9 wording:
   - the Low advice text ("No action needed. Carry on as normal, and check PUB's channels if the weather changes.")
   - the forecast caption ("Forecast from radar; flood accuracy not yet tested.")
   - whether Live mode should show PUB alerts for the chosen area
4. Send Akul:
   - the branch link for review
   - the list of 14 label rows to verify, with the 6 to look at closely (listed above)
   - this file
5. Agree with Akul a realistic label target, and whether to keep drafting.
6. Create the Telegram bot and demo channel if the bot is in the demo. Use BotFather. Put the token in `.env` as `TELEGRAM_BOT_TOKEN` and the chat id as `TELEGRAM_CHAT_ID`.
7. Tell Akul to upgrade uv to 0.13.1 or newer (`brew upgrade uv`) before syncing (see the uv note).

**Next for adk (can start now):**
1. **Telegram live mode:** write the `(timestamp, {zone: tier})` provider by wrapping `latest_zone_risk()` in `src/floodsense/serving/gold.py`, and pass it to `run_live()`. This removes the provider item from Akul's list.
2. **B6 deck and metrics write-up** (draft due 24 Oct), with the kriging and static-feature results as the "mentor suggestions measured" slide.
3. **J2.3 Q&A answers** for likely judge questions.
4. **Investigate** why the "In 1 hour" line did not show at 15:00 and 15:30 on 30 Sep 2026 (probably no radar nowcast for those times; not confirmed).
5. Re-run `python -m floodsense.models.select_candidates` once Akul has verified the 14 labels.
6. More label drafting, if the team wants it (leads above).

---

## 1. The project

FloodSense gives each of Singapore's 55 URA planning areas a calibrated chance of a *reported flash
flood in the next hour*, from NEA's 5-minute rain gauges, plus a radar nowcast. It is shown in a
Streamlit app, live or as a replay of any day since 2017.

- **Challenge:** DAISI Challenge 2026, Track B2 (Climate Action & Resilience).
- **Repo:** `adk7712/floodsense` (public). Team: Akul (git name Akul9 / "Akul Sharma") and Aditya
  Koli (adk, GitHub `adk7712`), both NTU.
- **Principle:** complement PUB, never replace it. Earlier, per-zone, with reasons.

**Status:** Round 1 submitted on 6 Oct, **qualified for the top 10** (of ~800).
- **Mentor:** Suneel Sunkara.
- **Sprint:** 12–26 Oct. **Demo Day: Tue 27 Oct**, 4–7pm SGT, where the top 3 are selected.
- **Office hours:**
  - 16 Oct: first working pipeline and blockers
  - 19 Oct: progress, evaluation, next milestones
  - 23 Oct: demo story, reproducibility, gaps
- **Another team is in Track B2.** Akul wants to do everything needed to win.

**Demo Day judging:**

| Criterion | Weight |
|---|---|
| Social impact | 30% |
| Technical execution on Databricks | 30% |
| Solution and UX | 20% |
| Data rigour | 10% |
| Presentation | 10% |

---

## 2. What is built (on master; see §0 for adk's branch)

| Area | State |
|---|---|
| Data | 60.8M NEA 5-min readings, 2017 to Sep 2026 (Parquet store). 66 verified flood events (36 train: 2017–23; 30 test: 2024–26), plus 14 unverified 2017–21 candidates on adk's branch. PUB Flood Prone Areas, URA MP2019 zones, LTA MRT station exits. |
| Model | A Platt-calibrated **60-min rainfall rule** (`models/flood_model.joblib`). Moderate from 0.30% (≈29.5 mm in 60 min), High from 0.71% (≈44.5 mm). Test (2024 to Sep 2026, 30 floods, scored once): High 12/30 (40%, CI 27–53%), Moderate 21/30 (70%, CI 57–83%), median warning 10 / 15 min. |
| Databricks | Lakeflow pipeline (bronze → silver → gold, plus the PUB alert archive). Poller job (schedule **paused**). Model in UC as `workspace.floodsense.flood_model@champion` (gold still loads the joblib). AI/BI dashboard. UC tags. **Radar archive and daily job** (§3). **Daily gauge archive and 15-min PUB alert job** (§3b). See DEPLOYMENT.md. |
| Radar | **Done, all of A1** (§3). |
| App | Live mode (PUB live alerts, 5-min refresh) and Replay (two-step sidebar: choose a date, then simulate). Map switch **Risk of flood \| Rain radar**, with a joined timing row (**Now \| In 1 hour**, or **Now \| +30 \| +60 min**); one layer at a time; slim legend bars. Zone panel shows risk, or a 2-line radar summary in radar view. MRT stations at risk. Light/dark toggle, orange buttons. |
| Tests/CI | ruff, mypy, about 290 tests (260 non-integration, 30 integration; counted by `pytest --collect-only` on 16 Oct, not re-run for this update) on adk's branch. GitHub Actions on push. |

---

## 3. Radar (A1): done on 15 Oct

- **Source:** data.gov.sg `v2/real-time/api/weather-radar-images/70km` and `/240km`.
  - One PNG every 5 minutes, with a bbox in EPSG:4326.
  - Keeps **about 3 months**: `?date=` works from 16 Jul 2026 (15 Jul already returned 404).
  - **Not Doppler:** motion is estimated from consecutive frames.
- **Archive:**
  - Backfill: 16 Jul to 14 Oct (91 days, 51,665 frames) in volume `workspace.floodsense.radar`, plus a local copy in `data/raw/radar` (gitignored).
  - Daily job `floodsense-daily-archiver` (id 486214899373176; was `floodsense-radar-archiver`) at 03:00 SGT, **schedule on**.
  - Script: `databricks/jobs/archive_radar.py`.
- **Colour scale:** 33 fixed colours, with the order taken from MSS's legend. Calibrated against the
  gauges as 0.68 dB per level (`data/reference/radar_colour_scale.csv`, `docs/radar_calibration.md`).
  - Held-out month: hourly corr 0.92, totals within 3%.
  - Heavy hours read about 20% low.
- **Nowcast:** OpenCV Farnebäck optical flow on 3 frames, then semi-Lagrangian extrapolation
  (`floodsense.radar.nowcast`; optional extra `radar` = opencv-python-headless; pysteps would not
  build on the Mac). Evaluation (`docs/radar_nowcast_result.md`, 43 storm days):
  - Beats both baselines. CSI for ≥29.5 mm in 60 min: 0.23, against 0.18 for radar persistence
    and 0.03 for gauge persistence.
  - The gain over gauges is significant; the gain from motion is modest.
  - **Recommendation: GO** for "risk in 1 hour" (team decides 19 Oct).
- **In the app:** `floodsense.radar.live`. Demo day for radar: **30 Sep 2026** (Woodlands flood);
  17 Apr 2021 predates the radar archive.


## 3b. Ongoing data collection (16 Oct)

The poller is paused, so two jobs keep the data growing (DEPLOYMENT.md §14):
- **`floodsense-daily-archiver`** (03:00 SGT): radar (§3) plus the last 3 complete days of gauge
  readings, as raw API pages in `landing/rainfall/daily/`. The pipeline ingests them on its next
  update (the job doesn't run it). Gauges backfilled from 1 Oct, so there is no gap since 30 Sep.
- **`floodsense-flood-alerts`** (every 15 min): PUB alerts, which the API doesn't keep. Only pages
  with an alert are saved. **Pause it when the poller is unpaused (24 Oct)**; the poller saves alerts
  too. Check its compute use after the first day; fall back to every 30 min if heavy.
- The **local** gauge store (`data/raw/rainfall`) still ends 30 Sep, and local radar ends 14 Oct;
  neither updates by itself.

---

## 4. Round 2 plan and ownership

The plan is `docs/ROUND2_WORK_PLAN.md` (local; v3.1). Status: A1.1–A1.6 are done, and Akul has finished A1, A2 and A6 (see §0). On adk's branch (§0): B2 (zone_static), B3, B4.1, B4.2/B2.4, A5 (replay and Telegram bot), A7.2 (first pass) and A9.

**Ownership swap: proposed 15 Oct, accepted by adk 16 Oct** (the plan's owners are updated):
- **Akul takes everything Databricks:** A2 (app reads gold; done), A3 (poller ops), A4 (serve from UC),
  A6 (Databricks App; done), A8 (governance), B4.4 (registering adk's candidates), J3.1 (final workspace).
- **adk takes:** A9 (resident / town-council view), A5 (Telegram bot plus replay mode), A7 (all the
  label drafting).
- **adk keeps:** B1–B4 and B6 (labels, static features, kriging, retrain, deck).
- **Verification:** Akul's side verifies everything adk pushes.
- **Label second check:** adk drafts, so Akul verifies (`verified_by` = Akul's initials).

**Akul's next items (in order):**
1. Finish the day-1 checks: second user, and a look at the competitor (see §5).
2. **Done:** A2 (app reads gold, `d484942`) and A6 (Databricks App, `f02fc15`; OpenCV is in `requirements.txt` for radar). A1 is also done.
3. A4 (pipeline loads `flood_model@champion`; re-run the parity test).
4. A3 (unpause the poller from 24 Oct, and pause `floodsense-flood-alerts` at the same time; watch the quota) and A8.
5. Review and merge adk's branch, and verify the 14 label rows (§0). The Telegram provider is adk's (§0, "Next for adk").
6. Run `brew upgrade uv` before `uv sync --locked` (see the uv note in §0).

**Also:**
- Share the plan and radar results with adk.
- Settle the 19 Oct radar go/no-go.

---

## 5. Day-1 check results (15 Oct)

| Check | Result |
|---|---|
| `ai_query` | **Works.** 12 endpoints ready, e.g. `databricks-meta-llama-3-3-70b-instruct`. In a test it **invented the year** (said 2023; the event was 2017) and duplicated an event, so always pass the source's publish date, require nulls for missing facts, and verify by hand. |
| Databricks Apps | Available, max 3, **auto-stop after 24 h** (documented); restart before rehearsals and the demo. |
| Quota | Exceeding it shuts down compute for the rest of the day. **LinkedIn "Verify identity" raises the limits.** |
| Other limits | One SQL warehouse (2X-Small), one active pipeline per type, 5 concurrent job tasks. |
| Second user | Undocumented. Akul to check Settings → Identity and access → Users. |
| Competitor (other B2 team) | The Devpost gallery is behind Cloudflare; Akul to check manually. |

---

## 6. Mentor feedback (Suneel, 14 Oct), and status

1. **Kriging or DEM instead of IDW** → **measured** (B3, `docs/kriging_vs_idw.md`): a small gain on heavy
   rain (−0.91 mm/h RMSE, about 3%). Recommendation: keep IDW. Elevation is in B2.
2. **Static catchment features** → **built** (B2, `zone_static.csv`: Copernicus GLO-30 and ESA
   WorldCover) and **tested** (B4.2b, B2.4). They hurt in cross-validation (High 6/23 vs 12/23), so
   they are not adopted (§0).
3. **Radar for foresight** → **done** (§3).

---

## 7. Databricks facts

- **Workspace:** https://dbc-91a4e71d-644e.cloud.databricks.com (Free Edition).
- **CLI:** profile `akul` (`export DATABRICKS_CONFIG_PROFILE=akul`). If the token expires, Akul runs
  `databricks auth login --profile akul`.
- **Schema `workspace.floodsense`:**
  - volumes `landing` (`rainfall/`, `rainfall/live/`, `flood_alerts/`), `artifacts`, `autoloader`, `radar`
  - pipeline `floodsense` (id `c32feb3d-…`)
  - jobs `floodsense-rainfall-poller` (986423263843293, paused), `floodsense-daily-archiver`
    (486214899373176, daily: radar and gauges) and `floodsense-flood-alerts` (477250824276803,
    every 15 min)
  - SQL warehouse `dfa0305abf58b59e`
  - AI/BI dashboard id `01f1c168d5ba1a44819705ab969bf3ea`
- **Secret** `floodsense/data_gov_api_key` (the data.gov.sg key). The key is also in the gitignored
  `.env`. Never print it. Creating secrets is Akul's job (the classifier blocks it for Claude).

---

## 8. Local environment (this Mac)

- **uv:** use `.venv/bin/uv`, or call the venv tools directly (`.venv/bin/pytest`, `ruff`, `mypy`,
  `streamlit`). Homebrew is installed; `libomp` and `poppler` were installed via brew.
- **Run the app:** `.venv/bin/streamlit run src/floodsense/app/streamlit_app.py --server.headless true --server.port 8501`.
  The preview launcher was unreliable, so start it via Bash. Playwright with Chromium is installed
  for headless screenshots.
- **MLflow:** local `mlflow.db`. Paths were fixed on 6 Oct to the College folder (no more
  `~/Desktop/NTU`). Artifacts go to `mlruns/` (gitignored).
- **Local data:**
  - the feature store `data/processed/features` (to Sep 2026)
  - radar `data/raw/radar`
  - `data/processed/nowcast_eval.parquet`
- **Gauge store coverage:** ends 30 Sep 2026 (nowcast evaluation window: 16 Jul to 30 Sep).

---

## 9. Working agreements (both of us)

- **Honesty first:** never fabricate data, labels or figures; mark anything unverified; don't claim
  what isn't built.
- **Ask before decisions that are the team's:** budgets, signed-off numbers, the submission, anything
  outward-facing. Explain trade-offs plainly and recommend one.
- **Plain, numbered step-by-step** for anything a person must do by hand (terminal, Databricks UI). Explain
  jargon.
- **Don't work around blockers with long methods.** Ask the teammate instead.
- **Checks:** don't run the full checks or commit after every small edit. Batch them, and commit when
  the person you're working for says "commit".
- **UI preferences:**
  - orange buttons
  - no ⓘ help icons
  - clean and uncluttered: one map layer at a time, no "(experimental)" labels
  - both themes must work; verify visually in dark mode
- **Teammate's pushes:** go to a branch; the other person reviews before merging to master.
- **Subagents:** Haiku for lookups and one-line edits, Sonnet for regular coding, and Opus only for the hardest planning or problem-solving. Give each agent its own files.
