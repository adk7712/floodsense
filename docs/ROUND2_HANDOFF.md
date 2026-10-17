# FloodSense: Round 2 handoff (the one status file)

**Last updated:** 18 Oct 2026, by Akul's session (adk's 17 Oct blocks below).
- master merges adk's `app-radar-gap-live-alerts` (which contains `rerun-88-labels`), reviewed by
  Akul's side on 18 Oct: 88 verified labels, the re-run, the refit script, the cut-off comparison,
  the radar-gap fix and the PUB alert line. Merge fixes: the radar image cache is capped at 600
  images (`API_PNG_CACHE_MAX`), and doc corrections.
- **Open:** the Moderate cut-off (0.30% or the refit's 0.53%), for Akul and adk to decide (§0).
- The 16 Oct branches (`adk/labels-kriging-zone-features`, `adk/review-followups`) are merged into
  master. The 16 Oct session is kept below as history (§0).

**Read first in a new session:** this file; the Round 2 task list is in §4b.
- This is the only Round 2 status and plan file; `docs/ROUND2_WORK_PLAN.md` was merged into §4b on 16 Oct.
- Details live in `README.md` (data, training, evaluation honesty), `DEPLOYMENT.md` (Databricks, §1–14)
  and `docs/` (radar calibration, nowcast results, kriging vs IDW).
- This file replaces `docs/PROJECT_STATUS.md` and is **committed to git**, so both people's agents
  read the same record. The workspace IDs in it are already public in `DEPLOYMENT.md`. Never put
  secrets, tokens or personal emails here.
- **Update this file at the end of every work session:** what was done (with commits), what's
  waiting on the other person, the open decisions and the next steps.

---

## 0. Latest sessions: 17 Oct (adk, two blocks), then 16 Oct (history)

### 17 Oct (adk): done, waiting, next

**Labels: the file is now 88 verified, 0 unverified** (`docs/label_review_2026-10-17.md`, commit `33e9484`).
- adk second-checked the 11 new candidates drafted by Akul's session: **8 verified (as adk), 3 rejected** (the rejections are in `docs/flood_events_rejected.md`).
- Verified floods: 58 in 2017–2023 (28 in the 2020–2023 cross-validation folds) and 30 in 2024–2026.
- Against the 60-new target: 14 + 8 = 22 new verified rows. The shortfall is documented (label target, below).

**Model re-run on the 88 labels: keep `rule_rain60`; nothing beats it** (`docs/candidate_selection_2026-10.md`, "Re-run on 88 verified labels"; results in `models/candidates/*_2026-10-17.json`, commit `33e9484`). Cross-validation on 2020–2023, 28 floods:
- Event score: `rule_rain60` 0.500, `rule_rain30` 0.491, `lightgbm` 0.438, `rule_rain60_static` 0.411. `rule_rain60` catches High 14/28 (90% CI 36–64%) and Moderate 18/28 (50–79%).
- Static features: mixed, not adopted. High 8/28 against 14/28 (paired 90% CI −36 to −7 points); Moderate 19/28 against 18/28 (−11 to +18 points) at 9.04 against 4.52 false episodes per zone-year.
- Prior SD 0.1 (stated in advance on 16 Oct): 0.509 against 0.500; High 15/28, Moderate 20/28 at 9.85 false episodes. The results file has no paired interval for it, so it is not adopted.
- 2024–2026 were not scored.

**Open decision for the team: ship the refit or keep the 1 Oct champion.** The cut-off numbers are in the 17 Oct, later block (`cutoff_comparison_2026-10-17.json`). On the 88 labels, the budget rule now picks 0.53%, and 0.30% gives 11.29 false Moderate episodes per zone-year (budget 11). The refit of `rule_rain60` on the 58 verified 2017–2023 floods keeps High at 0.71% (≈43.2 mm) and moves Moderate from 0.30% (≈29.5 mm) to 0.53% (≈37.7 mm). In cross-validation the rule catches 18/28 at Moderate with 4.52 false episodes per zone-year at that threshold.
- Trade-off: the published test result (High 12/30, Moderate 21/30 at 0.30%, scored once) would no longer describe the live model, and re-scoring the refit on 2024–2026 would be a "second look".
- If the refit is chosen: Akul registers `models/candidates/rule_rain60_refit_2026-10-17.joblib` as `candidate` (`register_model.py` needs a path option and `--alias candidate`). The `.joblib` is gitignored, so regenerate it on `33e9484` with `python -m floodsense.models.refit_champion --date 2026-10-17`, or share the file. adk signs off before promotion to `champion`, before the freeze (23 Oct, 18:00 SGT).
- If not: nothing to register (B4.4).

**Telegram: bot and channel ready.** the bot **@fs_tele_alert_bot** and the private channel **"floodsense test"** are created (17 Oct); the bot is an admin with permission to post; one test message was sent through `TelegramSender` and accepted. The token and chat ID are in adk's local `.env` only. Next: adk sends the token and chat ID to Akul privately (never in the repo or in this file); Akul stores them as the secrets `floodsense/telegram_bot_token` and `floodsense/telegram_chat_id` and adds the bot as a third task in the poller job.

**Akul's three asks from 16 Oct evening:**

| Ask | Status |
|---|---|
| Re-run `select_candidates` and the prior-sensitivity script on the new labels | **Done** (88 labels; results above) |
| Second check of the 11 new candidates | **Done** (8 verified, 3 rejected) |
| Create the Telegram bot and chat, send the token and chat ID | **Bot and channel done, test message sent**; token and chat ID still to be sent to Akul privately (adk) |

**Waiting on Akul:** review and merge `rerun-88-labels`; the refit decision (with adk); Telegram deployment (secrets plus the poller task) once adk sends the token and chat ID (16 Oct block, "Waiting on Akul", item 5).

**Next for adk:** send Akul the Telegram token and chat ID privately; B6 deck and metrics write-up (use the 88-label numbers and the "mixed, not adopted" static-feature line); J2.3 Q&A answers; sign-off on the model decision once the team has chosen.

---

### 17 Oct, later (adk): radar gap, PUB alerts, cut-off comparison (committed: `20a484d`, `404c9bd`, `12d8ddd`)

Branch `app-radar-gap-live-alerts`, cut from `65d7387`. Committed and pushed (branch `app-radar-gap-live-alerts`); merged into master 18 Oct. Checks run in adk's session on this work: 321 unit and 33 integration tests pass; ruff, format and mypy are clean.

**1. Radar gap on 30 Sep 2026 (15:00 and 15:30): cause found and fixed.**
- The frames exist in the source: 150 frames from 11:20 to 23:55, and 14:30–16:00 is complete. Only 13:50 and 19:10 are missing.
- **Cause:** with no radar volume or local archive, the app reads frames live from data.gov.sg. Without an API key, those calls get HTTP 429 (rate limit). The code treated the 429 as "no radar", did not check the image downloads, and cached nothing (about 9 requests per view).
- **Fix** (`src/floodsense/radar/live.py`; 4 tests in `tests/test_radar_gap.py`): a 429 is retried twice, 10 seconds apart; image downloads are status-checked; downloaded images are kept for the life of the process, so a repeat view costs 1 request. A missing frame is never filled in or read as dry. There is no gap tolerance, because the nowcast assumes 5-minute spacing.
- **Still to do:**
  1. adk: add the data.gov.sg key to the local `.env` as `FLOODSENSE_DATA_GOV_API_KEY=<key>`. It has no key yet. Use the same key as the Databricks secret `floodsense/data_gov_api_key`. Never paste it into chat or the repo.
  2. Akul: check that the Databricks App has the key set, and that the radar volume holds 30 Sep, so the demo does not depend on live API calls.
  3. Optional, not done: cache `radar_view` per 5-minute step in the app (`st.cache_data(ttl=300)`).

**2. PUB alert for the selected area in Live mode: built (`404c9bd`).**
- A red banner shows the official PUB alert text, with the issue time (SGT), in the Zone Diagnostic panel and in the Resident view. The function is `show_pub_alerts` in `src/floodsense/app/resident_view.py`, called from `streamlit_app.py`.
- It reuses the alerts the app already fetches. An alert counts for an area only when its centre point falls inside that area, so a circle that overlaps a neighbour does not show there.
- Live mode only. Nothing shows when there is no alert, and nothing in Replay.
- Tests are in `tests/test_streamlit_app.py`. There is no screenshot, because a real alert cannot be produced on demand.

**3. Moderate cut-off comparison: done (`12d8ddd`).** Script `src/floodsense/models/compare_cutoffs.py` (tests in `tests/test_compare_cutoffs.py`); output `models/candidates/cutoff_comparison_2026-10-17.json`. Re-run on clean commit `12d8ddd` (dirty false); the numbers are unchanged. It uses out-of-fold 2020–2023 `rule_rain60` predictions on the 88 labels (28 validation floods). 2024–2026 is not scored. The 90% intervals are bootstrap intervals over floods, so they are wide.

| Moderate cut-off | Floods caught (90% CI) | False episodes per zone-year (budget 11) | Median warning |
|---|---|---|---|
| 0.30% (current champion) | 20/28 (57–86%) | **11.29** (over budget) | 31 min |
| 0.40% | 18/28 (50–79%) | 7.25 | 33 min |
| 0.53% (the budget rule's pick on 88 labels) | 18/28 (50–79%) | 4.52 | 33 min |
| 0.60% | 18/28 (50–79%) | 3.55 | 28 min |

- High, for reference (0.71%): 14/28 (36–64%), 2.49 false episodes per zone-year (budget 2.5).
- Going from 0.30% to 0.53% loses 2 floods: 2021-08-20 Tampines Ave 10 and 2021-08-24 Cashew Rd. Fifteen others are still caught, about 10 minutes later (range 5–15 minutes). The intervals overlap heavily, so the cut-offs are not statistically distinguishable.
- adk leans to 0.53%, because 0.30% now breaks the budget rule. The trade-off is the 2 lost floods, and the published test result was measured at 0.30%. **Team decision, not made** (see "Open decisions").

**Next for adk (17 Oct, later):**
1. Add the data.gov.sg key to `.env` (item 1 above).
2. Send Akul the Telegram token and chat ID privately (17 Oct block).
3. Take the Moderate cut-off to the team with the table above. Record the decision here and in B4.5.
4. Ask Akul to review `rerun-88-labels` (pushed). Decide with Akul when `app-radar-gap-live-alerts` goes up. It needs your "commit" first.
5. ~~Re-run `compare_cutoffs.py` on a clean commit~~: done on `12d8ddd`.

---

### 16 Oct (adk): done, waiting, next (history; the label and re-run items are superseded by the 17 Oct block above)

**Done**, on branch `adk/labels-kriging-zone-features`, merged into master by Akul (`0e315b3`):

| Commit | Plan item | What |
|---|---|---|
| `48b86ea` | B4.1 | Active-rain gate tolerance (`active_rain_mask()` in `common/config.py`, 1e-6 mm), used in training and scoring. Dropped the unused `pub_monitored` and `return_period_years`. 8 more training rows pass the gate (13,250 vs 13,242 in the train-CLI test). Parity test passes. The feature store was rebuilt in B4.2 (`6ce0431`). **The champion has not been retrained since this fix** (see "B4 status" in §0). |
| `1fc34b1` | B3 | Ordinary kriging (`spatial/kriging.py`) vs IDW, leave-one-gauge-out, on 4,000 storm steps from 2017–2023 (`docs/kriging_vs_idw.md`). Heavy rain: RMSE 26.40 vs 27.31 mm/h; difference −0.91 (90% CI −0.98 to −0.85), about 3%. Light rain: kriging is slightly worse. Both methods underestimate heavy cells by about 16 mm/h. **Recommendation: keep IDW, put the result on the mentor slide.** Team to confirm. |
| `b240b41` | A5, A5.4 | Telegram bot (`floodsense.alerts.telegram_bot`). Replay of 17 Apr 2021 with `--dry-run` gives 67 messages, 24 of them High. At most one message per zone per hour, **except** that a rise above the last tier sent gets through (adk approved 16 Oct; otherwise no High is ever sent). Live mode now reads gold (`2ec4bfb`; see "Review follow-ups" below). |
| `fef4165` | A7.2 | 14 new label candidates, unverified at the time and signed off by Akul on 16 Oct (11 from 2017–19, 3 from 2020–21, 0 from 2022–23) in `flood_events.csv`, plus 122 rejection entries in `docs/flood_events_rejected.md`. Training leaves them out until `verified_by` is filled. |
| `3fdc66c` | B2 | `data/reference/zone_static.csv`: 55 rows, sourced, with `built_up_pct`, `elevation_m_mean` and `low_lying_pct` (% of the zone's DEM pixels under 5 m) from Copernicus GLO-30 and ESA WorldCover 2021. `rasterio` is an optional `static` extra. Licences are in the README. Downtown Core is only 60% built-up because of Marina Bay water and trees. Bukit Panjang has the highest mean elevation (48.8 m). |

**Waiting on Akul:**
1. ~~Review and merge~~ `adk/review-followups`: **done, merged as `52c2651`** (the earlier branch `adk/labels-kriging-zone-features` was merged as `0e315b3`).
   - `uv.lock` was bumped to revision 5 by uv 0.13.1. Update uv if `uv sync --locked` fails.
   - The gold pipeline uses the package, so the gate tolerance reaches gold on the next deploy.
2. **Done by Akul on 16 Oct (80 verified); then 8 more verified by adk on 17 Oct (88 verified).** The original request, kept as history: verify the 14 new label rows (all 14 were unverified: `verified_by` empty). adk drafted them, and the verifier must be a different person, so Akul checked. For each row, open the source and fill `verified_by` with your initials, or move the row to `docs/flood_events_rejected.md` with the reason. Training ignores any row with an empty `verified_by`.
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
3. **Done by Akul:** A1 (radar archive and nowcast), A2 (`d484942`, Live mode reads gold), A6 (`f02fc15`, Databricks App deployed), the data.gov.sg fallback (`82f1cb6`) and A4.1–A4.3 (`772ad07`, gold scores with `flood_model@champion`; DEPLOYMENT.md §17). Nothing else is open on Akul's list for these.
4. **B4.4:** nothing to register unless the team chooses the refit (17 Oct block above). `rule_rain60` stays champion, pending adk's sign-off.
5. **Deploy the Telegram bot** (Akul's plan, 16 Oct evening; **not deployed**). Run it as a third task in the poller job, after `refresh_pipeline`: `live --once --state-file /Volumes/workspace/floodsense/artifacts/telegram_state.json`. This replaces the separate 5-minute draft job.
   1. Create two secrets in scope `floodsense`: `telegram_bot_token` and `telegram_chat_id`. Neither exists yet. adk sends the values privately (17 Oct block).
   2. Add the task to the poller job and deploy it.
   3. Notes:
      - Alerts come only while the poller and the gold pipeline run. The poller is paused until the demo run-up (A3), so there are no Telegram alerts until it is unpaused.
      - Confirm the in-code secrets loader works with the workspace auth.
      - The artifacts volume must be persistent. A lost state file (`/Volumes/workspace/floodsense/artifacts/telegram_state.json`) re-alerts zones that are already elevated.
      - Check the compute use with the poller (A3).
   4. Alternative, kept but not deployed and paused: the separate 5-minute draft job `databricks/jobs/telegram_alerts_job.json` (288 runs a day). Keep it only as a fallback.

**Open decisions:**
- **Model sign-off (adk):** keep `rule_rain60` as champion (see B4.2 above). On 17 Oct the re-run on 88 labels agreed; the Moderate cut-off (0.30% or 0.53%) is open (17 Oct, later block has the table).
- **PUB alerts in Live mode (adk, built 17 Oct, `404c9bd`):** the official PUB alert for the selected area shows in the Zone Diagnostic panel and the Resident view. Team to confirm it stays (17 Oct, later block).
- **Label target (team).** Web sources are nearly used up: 22 new verified rows (14 + 8) against a target of 60 (the Definition of Done says 100). Agree a realistic number and document the shortfall.
  - Leads not tried: the ERL paper's supplement (`erlad975csupp1.docx`, https://iopscience.iop.org/article/10.1088/1748-9326/ad975c). Its download page has a CAPTCHA, so a person has to download it in a normal browser; use only the 2022–23 rows.
  - The channel `t.me/sgflood`.
  - Wayback copies of PUB's X/Facebook posts from 2017–2021.
  - Hansard, through a browser.
- **Kriging (team).** Keep IDW (recommended) or add kriging as an option before the freeze.

**B4 status (16 Oct, history): was paused until the 14 labels were verified. Done on 17 Oct: re-run on 88 verified labels, `rule_rain60` kept; the refit decision is open (17 Oct block).**
- The plan was: (1) adk re-runs `select_candidates`; (2) retrain the champion only if the tier cut-offs change; (3) hand any candidate to Akul as soon as it is ready; (4) adk signs off before the freeze.
- **The champion `models/flood_model.joblib` was not retrained after the gate fix.** The CV thresholds were identical (0.003 / 0.00706), so no change is expected. This is an inference, not a re-run.
- Current tier cut-offs: Moderate from 0.30%, High from 0.71%.
- The labels are now verified, so this fallback is no longer needed.

**Done later on 16 Oct (adk), pushed in `1bfd0be` (A9) and `6ce0431` (B4.2/B2.4); this file and the README note in `c4339ce` and `49969c4`:**
- **B4.2 / B4.2b / B2.4** (`docs/candidate_selection_2026-10.md`, `models/candidates/`):
  - Feature store rebuilt with the gate fix: 7,587,282 rows (+10,153, all one-tip rows).
  - `rule_rain60` reproduces the model card: High 12/23 (90% CI 35–70%), Moderate 17/23 (57–87%), on 2020–23 cross-validation.
  - New candidate `rule_rain60_static`: the rule plus one shrunk coefficient per static feature.
  - **The static features hurt.** High 6/23 vs 12/23 (−26 pts, 90% CI −43 to −9); Moderate 12/23 vs 17/23 (−22 pts, −39 to −4). The fit learns "built-up zones flood more", which mostly reflects where floods get reported.
  - LR and LightGBM, with or without the static features, are still far behind.
  - **Recommendation: keep `rule_rain60`; nothing new to register** (adk to sign off).
  - A stronger shrinkage (prior SD 0.1) matched the rule post hoc. It is not adopted: it was chosen after seeing the folds. Re-checked on 17 Oct (88 labels): 0.509 against 0.500, not adopted.
  - If a candidate is ever registered, `databricks/register_model.py` needs a path option, and `--alias candidate`.
- **Review follow-ups from Akul's side, all done on `adk/review-followups`** (merged as `52c2651`):
- **Telegram live mode, `2ec4bfb`:** the provider is `gold_latest_risk()`, which wraps `latest_zone_risk()`.
  - Where and how often: `live` runs the loop. `live --once` is for a scheduled job (the draft job above runs it every 5 min). State is saved to `--state-file`: `data/processed/telegram_state.json` locally (gitignored), `/Volumes/workspace/floodsense/artifacts/telegram_state.json` on Databricks. Restarts don't re-send.
  - Gold read and send errors are logged (never the token), and the loop continues. A failed send is retried; it is not marked sent.
  - `--max-age-minutes` (default 30) ignores stale gold.
  - Not deployed. Deployment is Akul's (§0 "Waiting on Akul", item 5).
- **Kriging write-up, `dd82c50`:** the result is written plainly: about 3% better on heavy rain, about 5% worse on light rain. IDW stays in serving. The comparison output is committed at `docs/results/kriging_vs_idw.json`.
- **Candidate selection reproducibility, `60e2b19` plus a re-run:** `models/candidates/selection_2026-10-16.json` now records `git_sha` 60e2b19. The re-run on the clean commit reproduced every number. The sensitivity script is committed as `src/floodsense/models/prior_sensitivity.py`. Its output is `models/candidates/prior_sensitivity_2026-10-16.json`, with 90% CIs. Conclusions unchanged: keep `rule_rain60`.
- **`zone_static.csv` contract (`low_lying_pct`):** documented in §4b task 0.2 (committed in `afaf60e`).
- **Resident view, `1bd7ab6`:** folded into the Zone Diagnostic panel as a "What to do" line. **Reverted by `591f599` (16 Oct evening, team decision): the separate Resident view stays, with the Analyst/Resident switch, and the Zone panel has no "What to do" line.** The notes below describe the fold, which is no longer on master.
  - Advice text lives in `src/floodsense/alerts/messages.py` (no longer imported from the bot module).
  - No bare `except` on the forecast path. The "In 1 hour" line catches only the expected errors and logs the rest.
  - Low advice is "No action suggested. Carry on as normal, and check PUB's channels if the weather changes." (settled; replaces "No action needed").
  - Same data as the map (gold in Live).
  - The folded panel was screenshotted in light and dark (Replay 17 Apr 2021, 12:50, Bukit Timah) and reads well. It has no forecast line of its own; the map's "In 1 hour" layer is unchanged.

**Limitations and loose ends (be aware before relying on today's work):**
- **Labels (16 Oct, history):** only 4 of the 14 new candidate rows had their source quote re-checked by adk's session; the other 10 relied on the drafting agent's own check. All 14 were then checked and signed off by Akul on 16 Oct (`docs/label_review_2026-10-16.md`), and the 11 later candidates were checked by adk on 17 Oct (8 verified, 3 rejected; `docs/label_review_2026-10-17.md`). No row is unverified now.
- **Secondary sources:** several label sources are reposts (a wildsingaporenews blog copying CNA, ST or TODAY text) or secondary pages (ADInet, an archived The Online Citizen piece). The original articles were not fetched. Each row says so in `notes`.
- **Label target:** 22 new verified rows (14 + 8) against a plan target of 60 (the Definition of Done says 100). 2022–23 added none.
  - Not tried: the ERL paper supplement (CAPTCHA, needs a person in a browser), `t.me/sgflood`, Wayback copies of PUB's X/Facebook posts, and Hansard through a browser.
- **Model results (B4.2/B2.4):** the 16 Oct numbers used only the 66 verified labels (23 floods in the 2020–23 folds). **Superseded on 17 Oct** by the re-run on 88 labels (28 floods in the folds); the intervals are still wide.
- **Feature store gap:** the rebuilt feature store has 46 more 2017–23 rows than the model card counted under the old gate. Probably the 2 Oct rainfall gap-fill files. **UNVERIFIED.**
- **Static candidate:** the post-hoc "prior SD 0.1" static candidate matched the rule on the same folds. It is not adopted, because it was chosen after seeing the results. Re-checked on 17 Oct on 88 labels: 0.509 against 0.500, still not adopted.
- **Kriging:** kriging vs IDW was measured on gauge interpolation only, not on flood detection.
- **Resident view (A9, separate page; the "What to do" line is in it):**
  - Radar gap seen on 30 Sep 2026: no nowcast at 15:00 or 15:30 (14:30 had one). **Cause found 17 Oct:** a data.gov.sg rate limit without a key. Fixed on `app-radar-gap-live-alerts` (`20a484d`); the remaining actions are in §0 (17 Oct, later). Until they are done, the map's "In 1 hour" layer can still be missing if the API rate-limits the app.
  - The 17 Apr 2021 demo replay has no radar, so "In 1 hour" never shows there.
  - Re-checked after merging Akul's `82f1cb6` (no stale-data banner, fallback behind Advanced): the Resident view follows the same data source and has no data-age banner.
- **Telegram bot:** created 17 Oct (@fs_tele_alert_bot, channel "floodsense test"); one test message sent, no real alert yet. Live mode reads gold (`2ec4bfb`) but is not deployed: it waits on Akul's secrets and the poller task.
- **PUB alert line (17 Oct, `404c9bd`):** matched by the alert's centre point only; Live mode only. No screenshot, because a real alert cannot be produced on demand.
- **Candidate selection:** `select_candidates.py` doesn't itself check for a dirty tree. Run `git status --porcelain` first, and it must print nothing. The sensitivity script (`prior_sensitivity.py`) does record a dirty flag.
- **uv:** checked on 16 Oct. uv 0.13.1 (Homebrew, already the newest) passes `uv lock --check`, `uv sync --locked --all-extras --group dev` (what CI runs) and `uv pip check`; no pip-installed packages in `.venv`; `requirements.txt` matches a fresh export. `uv.lock` is lockfile revision 5; which older uv versions can read it is UNVERIFIED, so **Akul should run `brew upgrade uv` (0.13.1 or newer) before `uv sync --locked --all-extras --group dev`.** Regenerate `requirements.txt` only with the exact command in its header (it includes `--extra radar`, which keeps OpenCV for the Databricks App).
- **Rejected candidate model not uploaded:** `models/candidates/rule_rain60_static.joblib` is the saved copy of the zone-feature candidate. It is **not committed** (now in `.gitignore`) because it did worse than `rule_rain60` in cross-validation (High 6/23 vs 12/23). Its results are in `models/candidates/selection_2026-10-16.json`, `selection_2026-10-17.json` and `docs/candidate_selection_2026-10.md`, and `python -m floodsense.models.select_candidates` rebuilds it. The champion `models/flood_model.joblib` is unchanged.

**Before handing off to Akul (adk's checklist):**
1. ~~Commit and push the branch~~: done 16 Oct (`c4339ce` and earlier).
2. Sign off on the model decision once the team has chosen the Moderate cut-off (0.30% or 0.53%; 17 Oct, later block). Nothing needs registering unless the refit is chosen.
3. ~~Optional idea~~: PUB alerts for the chosen zone are **built on 17 Oct** (`404c9bd`; see the 17 Oct, later block). They show in the Zone panel and the Resident view.
4. Send Akul:
   - the branch link for review
   - (done) the 14 label rows were verified on 16 Oct
   - this file
5. Agree with Akul a realistic label target, and whether to keep drafting.
6. ~~Create the Telegram bot and demo channel~~: done 17 Oct (token and chat ID in `.env`). Send them to Akul privately.
7. Tell Akul to upgrade uv to 0.13.1 or newer (`brew upgrade uv`) before syncing (see the uv note).

**Next for adk (16 Oct list, history; see the 17 Oct block for what is current):**
0. **From Akul's side, 16 Oct evening** (after merging `adk/review-followups`):
   - **The separate Resident view stays** (team decision). `1bd7ab6`'s fold into the Zone panel was reverted; the Analyst/Resident switch now sits top right, left of the Light/Dark toggle. The Low wording is the shared "No action suggested" everywhere. Please don't re-fold it.
   - **14 labels are signed off (80 verified).** Your selection re-run (`60e2b19`) used the old 66: please re-run `select_candidates` and the prior-sensitivity script on the 80. **Done 17 Oct (on 88).**
   - **11 new candidates** (end of `flood_events.csv`, drafted by Akul's session) need your second check. **Done 17 Oct: 8 verified, 3 rejected.**
   - **Telegram job (open):** instead of its own 5-minute schedule (288 runs a day; gold only changes when the poller runs), Akul proposes adding the bot as a third task in the poller job, after `refresh_pipeline`. Akul deploys it; you create the bot and chat and give Akul the token and chat id to store as secrets.
1. **Done, `2ec4bfb`: Telegram live mode.** The `(timestamp, {zone: tier})` provider wraps `latest_zone_risk()` and is passed to `run_live()`. Akul's deployment notes are in "Waiting on Akul", item 5.
2. **B6 deck and metrics write-up** (start as soon as possible), with the kriging and static-feature results as the "mentor suggestions measured" slide.
3. **J2.3 Q&A answers** for likely judge questions.
4. ~~Investigate~~ **done 17 Oct:** the cause was a data.gov.sg rate limit without a key. Fixed on `app-radar-gap-live-alerts` (`20a484d`). The remaining actions are in the 17 Oct, later block.
5. ~~Re-run `select_candidates` once the labels are verified~~: done 17 Oct on 88 labels.
6. More label drafting, if the team wants it (leads above).
7. **Review follow-ups from Akul's side (16 Oct merge). All done and merged as `52c2651`, see "Review follow-ups" in the 16 Oct block above:**
   - Telegram bot state and error handling, `2ec4bfb`: done.
   - Kriging write-up, `dd82c50`: done.
   - Candidate selection reproducibility, `60e2b19` plus a re-run: done.
   - `zone_static.csv` `low_lying_pct` in the contract, §4b 0.2: done (committed in `afaf60e`).
   - Resident view, `1bd7ab6`: folded into the Zone Diagnostic panel, then reverted by `591f599` (the separate view stays), `alerts/messages.py`, no bare `except` on the forecast path, "No action suggested" wording: done.
8. **Optional — stakeholder feedback (B5):** if time allows, show the Resident view's "What to do" line to one real user (town council, residents' committee or volunteer) for 10–15 minutes and record their answers word for word with consent. Not required; nothing depends on it.

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
- **Sprint:** runs up to Demo Day. **Demo Day: Tue 27 Oct**, 4–7pm SGT, where the top 3 are selected.
- **Office hours** with the mentor are scheduled. Topics:
  - first working pipeline and blockers
  - progress, evaluation, next milestones
  - demo story, reproducibility, gaps
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
| Data | 60.8M NEA 5-min readings, 2017 to Sep 2026 (Parquet store). 88 verified flood events (58 in 2017–23 for training and cross-validation; 30 in 2024–26 as the test set), none unverified (on branch `rerun-88-labels`, pushed and waiting for Akul's review; master has 80 verified until it is merged). PUB Flood Prone Areas, URA MP2019 zones, LTA MRT station exits. |
| Model | A Platt-calibrated **60-min rainfall rule** (`models/flood_model.joblib`). Moderate from 0.30% (≈29.5 mm in 60 min), High from 0.71% (≈44 mm). Test (2024 to Sep 2026, 30 floods, scored once): High 12/30 (40%, CI 27–53%), Moderate 21/30 (70%, CI 57–83%), median warning 10 / 15 min. The Moderate cut-off is open (0.30% or 0.53%; 17 Oct, later block). |
| Databricks | Lakeflow pipeline (bronze → silver → gold, plus the PUB alert archive). Poller job (schedule **paused**). Model in UC as `workspace.floodsense.flood_model@champion`, which gold scores with (A4). AI/BI dashboard. UC tags. **Radar archive and daily job** (§3). **Daily gauge archive and 15-min PUB alert job** (§3b). See DEPLOYMENT.md. |
| Radar | **Done, all of A1** (§3). |
| App | Live mode (PUB live alerts, 5-min refresh) and Replay (two-step sidebar: choose a date, then simulate). Map switch **Risk of flood \| Rain radar**, with a joined timing row (**Now \| In 1 hour**, or **Now \| +30 \| +60 min**); one layer at a time; slim legend bars. Zone Diagnostic panel shows risk, a 2-line radar summary in radar view, and (Live mode, 17 Oct, `404c9bd`) the official PUB alert for the selected area. The separate Resident view (A9) has the "What to do" line; its switch sits top right. MRT stations at risk. Light/dark toggle, orange buttons. |
| Tests/CI | ruff, mypy, format, and pytest in CI. On `app-radar-gap-live-alerts` (17 Oct, adk's session): 321 unit and 33 integration tests pass; ruff, format and mypy clean. (Earlier count, 16 Oct: about 290.) GitHub Actions on push. |

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
  - **Decided: GO** (team, 17 Oct): "risk in 1 hour" (A1.6) stays in the app and the demo.
- **In the app:** `floodsense.radar.live`. Demo day for radar: **30 Sep 2026** (Woodlands flood);
  17 Apr 2021 predates the radar archive.
- **Rate limits (17 Oct, `20a484d`):** without a data.gov.sg key, the live fallback gets HTTP 429 and showed no radar at some times. Fixed on `app-radar-gap-live-alerts`; the remaining actions are in §0 (17 Oct, later).


## 3b. Ongoing data collection (16 Oct)

The poller is paused, so two jobs keep the data growing (DEPLOYMENT.md §14):
- **`floodsense-daily-archiver`** (03:00 SGT): radar (§3) plus the last 3 complete days of gauge
  readings, as raw API pages in `landing/rainfall/daily/`. The pipeline ingests them on its next
  update (the job doesn't run it). Gauges backfilled from 1 Oct, so there is no gap since 30 Sep.
- **`floodsense-flood-alerts`** (every 15 min): PUB alerts, which the API doesn't keep. Only pages
  with an alert are saved. **Pause it at the same time as the poller is unpaused** (A3); the poller saves alerts
  too. Check its compute use after the first day; fall back to every 30 min if heavy.
- The **local** gauge store (`data/raw/rainfall`) still ends 30 Sep, and local radar ends 14 Oct;
  neither updates by itself.

---

## 4. Round 2 plan and ownership

The task list is §4b (merged from the old work plan, v3.2). Status: A1.1–A1.6 are done, and Akul has finished A1, A2 and A6 (see §0). Done by adk (§0): B2 (zone_static), B3, B4.1, B4.2/B2.4 (re-run on 88 labels, 17 Oct), A5 (replay and Telegram bot; bot and channel created 17 Oct, not yet deployed), A7.2 (first pass), and A9 (its PUB alert line is `404c9bd`, 17 Oct).

**Ownership swap: proposed 15 Oct, accepted by adk 16 Oct** (the owners in §4b are updated):
- **Akul takes everything Databricks:** A2 (app reads gold; done), A3 (poller ops), A4 (serve from UC),
  A6 (Databricks App; done), A8 (governance), B4.4 (registering adk's candidates), J3.1 (final workspace).
- **adk takes:** A9 (resident advice, in the separate Resident view), A5 (Telegram bot plus replay mode), A7 (all the
  label drafting).
- **adk keeps:** B1–B4 and B6 (labels, static features, kriging, retrain, deck).
- **Verification:** Akul's side verifies everything adk pushes.
- **Label second check:** adk drafts, so Akul verifies (`verified_by` = Akul's initials).

**Akul's next items (in order):**
1. Day-1 checks: closed 16 Oct (no second user needed after the swap; the other B2 team's entry isn't visible).
2. **Done:** A2 (app reads gold, `d484942`) and A6 (Databricks App, `f02fc15`; OpenCV is in `requirements.txt` for radar). A1 is also done.
3. **Done:** A4.1–A4.3 (`772ad07`). Parity: the champion file is byte-identical to `models/flood_model.joblib` and scores the 17 Apr 2021 replay like the Databricks export. Left: A4.4, promote adk's candidate before the freeze if there is one.
4. A3 (unpause the poller only for the run-up to the demo, long enough to log 48 h of clean runs before Demo Day, because running it longer uses up the daily compute quota; pause `floodsense-flood-alerts` at the same time) and A8.
5. **Done:** reviewed and merged adk's branch (`0e315b3`). **14 labels checked against sources and signed off by Akul 16 Oct (`docs/label_review_2026-10-16.md`): 80 verified events. adk checked Akul's 11 new candidates on 17 Oct (8 verified, 3 rejected): 88 verified, and re-ran `select_candidates` on them.**
6. **Done:** A8 (descriptions and tags; DEPLOYMENT.md §18), with gold column comments live since the 16 Oct refresh.
7. **16 Oct evening refresh** (DEPLOYMENT.md §19): wheel rebuilt, gold refreshed to 18:20 SGT, Databricks App redeployed with the day's app and checked by Akul, then stopped. Telegram job proposed as a poller task, waiting on adk's bot token and chat id. The Telegram provider is done (`2ec4bfb`, §0); the Telegram job deployment is on Akul's list (§0, "Waiting on Akul", item 5).
8. Run `brew upgrade uv` before `uv sync --locked` (see the uv note in §0).

**Also:**
- Share the radar results with adk (§4b is the plan).
- ~~Settle the radar go/no-go~~: **GO**, decided 17 Oct.

---

## 4b. Round 2 plan (task list, merged from the old work plan on 16 Oct)

**Goal:** a live, Databricks-run flood risk system that is honest about its evidence, with at least one mentor suggestion implemented and measured.
- **Scope cuts, in order:** (1) radar as a *risk input* (the forecast-risk layer, A1.6) if the go/no-go fails; the archive and rain-nowcast overlay are not cut. (2) Kriging, unless it is clearly better. (3) The Telegram bot.
- **Never cut:** the app reading gold, the radar archive and nowcast overlay, the rehearsals, the fallback recordings, the limitations slide.
- **Rules:** the test-set rule and the freeze are in `CLAUDE.md`. Finish all model and feature changes before the freeze (hard limit 23 Oct 18:00 SGT); after it, bug fixes only. Neither person's work should block the other's for more than half a day.

**Order of work** (what depends on what; do each step as soon as its inputs are ready):
1. Radar archive and daily archiver: done.
2. Shared setup, label batch 1, second-user and `ai_query` tests: `ai_query` and Apps tested (§5); second user open; 14 label candidates drafted (all verified since).
3. Radar go/no-go, static features, app reads gold: A2 and A6 done; B2 and B3 done (on master); radar **GO** (decided 17 Oct).
4. Labels verified (88, done 17 Oct), then re-run candidate selection (done 17 Oct), then adk signs off. Nothing to register unless the team ships the refit (§0).
5. Radar integration if go, and retrain if needed. Finish before the freeze (hard limit 23 Oct 18:00 SGT).
6. Promote to `champion` after adk signs off and before the freeze.
7. App and bot read gold. Live mode reads gold (`2ec4bfb`); the bot is not deployed yet (§0, "Waiting on Akul", item 5).
8. Poller unpause for the run-up to the demo (48 h of clean runs, then pause), with `floodsense-flood-alerts` paused at the same time.
9. Slides and architecture visuals, then two rehearsals, fallback recordings and the final workspace check.
10. Demo Day: Tue 27 Oct.

**Tasks.** Status as of 17 Oct, from §0 and the repo. "Branch" means adk's 17 Oct work, merged into master on 18 Oct (`rerun-88-labels` and `app-radar-gap-live-alerts`).

| ID | Task | Owner | Status | Notes / links |
|---|---|---|---|---|
| 0.1 | Agree freeze date and test-set rule; record in README "Evaluation protocol" | both | Partly | Rules are in `CLAUDE.md`; README has no "Evaluation protocol" heading: open |
| 0.2 | `data/reference/zone_static.csv` contract (55 rows, sourced, no blanks). Columns: `ura_planning_area, built_up_pct, elevation_m_mean, low_lying_pct, source_name, source_url, retrieved_at`. `low_lying_pct` = % of the zone's DEM pixels under 5 m | adk | Done | B2 (`3fdc66c`); `low_lying_pct` contract line added on `adk/review-followups` (merged; committed in `afaf60e`) |
| 0.3 | Label-file schema | both | Done | Existing `flood_events.csv`; `verified_by` = verifier's initials, not the drafter |
| 0.4 | Registry rule | both | Done | Alias `candidate` for candidates; only `champion` serves; promote before the freeze, only after adk signs off |
| 0.5 | Shared task board | both | Open | This section is the list |
| 0.6 | Free Edition unknowns (second user, `ai_query`, Apps) | Akul | Partly | §5: `ai_query` and Apps answered; second user open |
| 0.7 | Look at the other B2 team | Akul | Open | Devpost gallery is behind Cloudflare, check by hand (§5) |
| A1 | Radar archive, calibration, nowcast, overlay, forecast risk (A1.1–A1.6) | Akul | Done | §3. A1.6 is built; **GO** decided 17 Oct. Radar is not a trained feature (3 months of data vs 2017–2023). Go test: the nowcast beats persistence at 30 min on gauge-interpolated storm steps. **17 Oct:** the 30 Sep 15:00/15:30 gap was a data.gov.sg rate limit (no key); fixed in `20a484d` on `app-radar-gap-live-alerts` (4 tests). Open: adk's local key; Akul to check the App key and that the volume holds 30 Sep (§0, 17 Oct, later) |
| A2 | App reads gold | Akul | Done | `d484942`. Spec asked for a data-age line, and a warning above 60 min. **`82f1cb6` removed the stale-data banner** (team decision, see Definition of Done). Credentials stay in secrets; cache queries to save warehouse compute |
| A3 | Poller operations | Akul | Open | A3.0 secret exists (§7). A3.1 48 h of clean runs before the demo. A3.2 job-failure email alert. A3.3 unpause the poller only for the run-up to the demo, long enough to log 48 h of clean runs before Demo Day, because running it longer uses up the daily compute quota; log daily usage and re-pause if the cap gets close; pause `floodsense-flood-alerts` at the same time |
| A4 | Serve from Unity Catalog | Akul | Done (A4.1–A4.3, `772ad07`); A4.4 open | A4.1 load `models:/workspace.floodsense.flood_model@champion` in `databricks/pipelines/lakeflow_pipeline.py` (v1 already loads back with identical tiers: code change only). A4.2 read `zone_static.csv` only if the model uses it (it does not). A4.3 re-run the parity test (`test_phase5_parity.py`); it must pass or the difference be documented. A4.4 promote before the freeze, after adk signs off |
| A5 | Telegram bot (A5.1–A5.4) | adk | Partly | On master (`b240b41`): tier-change logic, A5.3 one message per zone per hour (rise above last tier gets through), A5.4 replay mode (17 Apr 2021). Live mode reads gold (`2ec4bfb`, persistent state, error handling). **A5.1 done 17 Oct: bot @fs_tele_alert_bot and channel "floodsense test" created, test message sent; token and chat ID to go to Akul privately; deployment as a poller task is Akul's** (§0) |
| A6 | Databricks App | Akul | Done | `f02fc15`. A6.2 fresh-browser test: open. A6.3 Apps auto-stop after 24 h (§5): restart before rehearsals and the demo. Fallback if it fails: run Streamlit locally, show the App as roadmap |
| A7 | LLM-drafted flood candidates | adk | Partly | A7.1 `ai_query` works but invents facts (§5). A7.2 **2017–2023 only**, never 2024–2026 before the freeze: first pass done (14 candidates, all verified 16 Oct; 122 rejections, `fef4165`). A7.3 post batches with an ID, hand to Akul. A7.4 rejections in `docs/flood_events_rejected.md`. The "6 batches" target is not met (§0 label target) |
| A8 | Governance (A8.1) and Asset Bundles (A8.2) | Akul | A8.1 done 16 Oct (DEPLOYMENT.md §18); A8.2 stretch | A8.1 descriptions and tags on every table, volume and model. A8.2 Asset Bundles is a stretch, only once the freeze work is done |
| A9 | Resident / town-council view | adk | Done; PUB line built 17 Oct (`404c9bd`) | `1bfd0be`. Team decision 16 Oct: keep the separate Resident view (adk's fold into the Zone panel, `1bd7ab6`, was reverted); its switch sits top right, left of the Light/Dark toggle. Low wording: "No action suggested". **17 Oct:** PUB alert for the selected area in Live mode, in the Zone panel and the Resident view (`show_pub_alerts`; `404c9bd` on `app-radar-gap-live-alerts`). |
| B1 | Label verification (B1.1–B1.4) | Akul verifies, adk drafts | Done for now (shortfall) | 88 verified, 0 unverified (17 Oct). 14 rows signed off by Akul 16 Oct; 11 drafted by Akul's side, of which adk verified 8 and rejected 3 on 17 Oct. New verified rows: 14 + 8 = 22 against the target of **60**; shortfall documented, web sources nearly used up. Set `time_precision` to match the source; run `load_flood_events()` after each batch |
| B2 | Static zone features | adk | Done | `3fdc66c`. Sources: Copernicus GLO-30, ESA WorldCover; licences in README. PUB drainage capacity and catchments: not openly available, unverified |
| B3 | Kriging vs IDW | adk | Done | `1fc34b1`, `docs/kriging_vs_idw.md`. Decision rule: add kriging only if clearly better. Recommendation: keep IDW; team to confirm |
| B4.1 | Fix active-rain gate tolerance; drop unused columns | adk | Done | `48b86ea` |
| B4.2 | Re-run selection on 2020–2023 only | adk | Done; re-run on 88 labels done 17 Oct (on `rerun-88-labels`, pushed) | `docs/candidate_selection_2026-10.md`; `rule_rain60` still wins (0.500; next `rule_rain30` 0.491, `lightgbm` 0.438) |
| B4.2b | Candidate that uses static features; re-run LR and LightGBM | adk | Done; 88-label result on `rerun-88-labels` | On 88 labels the static features are mixed, not adopted (High 8/28 vs 14/28; Moderate 19/28 vs 18/28 at twice the false episodes); LR and LightGBM still behind |
| B4.3 | Radar via A1.6, without retraining | adk | Done | Radar GO (17 Oct): the forecast risk uses the nowcast with the current model, no retraining |
| B4.4 | Register the candidate with alias `candidate` | Akul | Nothing to register, unless the team ships the refit | If the refit is chosen: register `models/candidates/rule_rain60_refit_2026-10-17.joblib` (regenerate with `refit_champion` on `33e9484`, the `.joblib` is gitignored). Needs a `register_model.py` path option and `--alias candidate` |
| B4.5 | Freeze and set thresholds (before the freeze) | adk | Open: team decision on the Moderate cut-off (0.30% vs 0.53%); numbers in §0, 17 Oct, later | Thresholds come from the budget rule in `config.py` (2.5 false High, 11 false Moderate per zone-year; achieved on the 2024–26 test, scored once: 1.56 and 7.28). **Comparison (17 Oct, later):** on the 88 labels the budget rule picks 0.53%; 0.30% gives 11.29 false Moderate episodes per zone-year (over 11). Do not change the budgets to improve a result |
| B4.6 | Report CV results; label any 2024–2026 use a "second look" | adk | Done so far | No second look taken |
| B5 | Stakeholder feedback (optional) | adk | Open, optional | Show the Resident view's "What to do" line to one real user for 10–15 min; record answers word for word, with consent. Quote on a slide only if it happened |
| B6 | Deck and metrics write-up | adk (Akul: architecture slide) | Open | B6.1 rates with raw counts and 90% intervals, plus false alarms per zone-year. B6.2 limitations slide: label count, test-set status, radar scope; tide is not built although some labels carry a `rain_tide` tag. B6.3 credit all datasets, libraries, models. B6.4 draft as soon as possible, both review, **deck saved as PDF in the repo**. Include the "mentor suggestions measured" slide (§6) |
| J1 | Integration and demo flow | both | Open | J1.1 CI green, freeze and tag before the limit (23 Oct 18:00 SGT). J1.2 demo order: app live from gold, map with radar nowcast, Resident view's "What to do" line, replay 17 Apr 2021, click the High zone and show the warning time, Telegram alert (replay mode), Databricks (pipeline, model in UC, dashboard). J1.3 a fallback recording or screenshot for each live step. J1.4 two clean end-to-end runs from a fresh browser |
| J2 | Rehearsals and Q&A | both | Open | J2.1 rehearsal 1 (time it, note failures). J2.2 rehearsal 2 (every fallback works), once the freeze is done and the fallbacks exist. J2.3 Q&A answers (adk): why not full radar; false-alarm rate; how labels are verified; what happens during NEA downtime |
| J3 | Final workspace and submission | Akul (J3.1), adk (J3.2, J3.3) | Open | J3.1 pipeline, poller, app and model in the final workspace; check quota. J3.2 scan repo and deck for tokens, passwords, personal data. J3.3 submit deck and links before the deadline; keep a PDF copy; save the confirmation |

**Dependency map** (handoffs between tracks):

| Handoff | From → To | Risk | Mitigation |
|---|---|---|---|
| Verified label batches | A7 → B1 | Low | Small batches; verify each batch soon after it is posted |
| `zone_static.csv` | B2 → A4 | Medium | Delivered; matches the contract |
| Radar go/no-go | A1 → B4.3 | Low | Retrain does not wait for it |
| Candidate registered | B4.4 → A4 | Medium | A4 can use the current `champion` |
| Gold live | A2/A3 → B6 | Low | Gold is live; the data.gov.sg fallback exists |
| Feature freeze | both → J1 | Hard limit: 23 Oct 18:00 SGT | No changes after the freeze |

**Key design:** the app and the bot read gold, not the model directly, so adk can retrain without blocking Akul. Akul switches the `champion` alias only after adk signs off.

**Demo Day criteria → plan items:**

| Criterion (weight) | Plan items |
|---|---|
| Social impact (30%) | A5 Telegram alerts, A9 "What to do" line, B5 (optional) |
| Technical execution on Databricks (30%) | A1 radar archive, A2 app on gold, A3 live poller, A4 registry serving, A6 Databricks App, A7 `ai_query`, A8 governance and Asset Bundles |
| Solution and UX (20%) | A1.5 nowcast overlay, A1.6 forecast risk, A9 "What to do" line, per-zone explanations |
| Data rigour (10%) | B1 labels, B2 static features, B3 kriging, B4 fixes, the test-set protocol |
| Presentation (10%) | J1–J3, the fallback recordings |

**Definition of Done** (ticked only where §0 or the repo shows it):
- [x] The live app reads gold from Databricks (A2, `d484942`).
- [ ] The app shows a data-age warning. **Conflict:** Akul's `82f1cb6` removed the stale-data banner. Team decision needed: restore a warning, or change this item. Not resolved here.
- [x] The model is served from `champion` in Unity Catalog; the parity test passes or any difference is documented (A4).
- [ ] The poller runs on schedule with no failures in the 48 hours before the demo (A3).
- [ ] At least 100 new verified events (2017–2023 only, each with a source and a verifier other than the drafter), or the shortfall is documented. Today: 22 new verified (14 + 8), the shortfall is documented, not yet agreed as the final number.
- [x] At least one mentor suggestion is measured (B3 kriging, B2.4 static features).
- [ ] The measured mentor suggestion is on a slide (B6).
- [x] The radar archive is complete from 16 Jul, and the nowcast overlay is on the map. Akul to confirm 30 Sep is in the volume (§0, 17 Oct, later).
- [x] Forecast risk (A1.6) is shown (GO, 17 Oct); its rain-nowcast skill result goes on a slide. No "experimental" label (UI rule).
- [x] The A9 Resident view, with its "What to do" line, is on master (`1bfd0be`). The fold into the Zone panel (`1bd7ab6`) was reverted by `591f599`.
- [ ] Metrics are reported honestly, with intervals and a limitations slide (B6).
- [ ] Two rehearsals are done, and a fallback exists for each live step (J1, J2).

**Open questions still open:**
1. **Label target.** An early team goal was "a few hundred" new floods, the plan said 100, B1 says 60; 22 new verified so far (14 + 8). See §0.
2. **PUB X/Telegram history.** How far back PUB's posts go, and whether they can be archived legally and reliably.
3. **Radar role.** Answered 17 Oct: GO, radar as a nowcast layer evaluated on storm steps, not a trained feature (§3).
4. **Second user on Free Edition.** Open (§5).
5. **Daily compute cap.** Not published, and the usage table is not available, so we can only watch for failures. It affects A3 (poller schedule) and A2 (query caching).
- Answered, see §5: `ai_query` works; Databricks Apps auto-stop after 24 h.

**Version history:** v3 (Akul, 15 Oct: feedback points added, unverified claims marked open), v3.1 (16 Oct: ownership swap), v3.2 (16 Oct: B5 optional).

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
   WorldCover) and **tested** (B4.2b, B2.4). On the 88 labels they are mixed in
   cross-validation (High 8/28 vs 14/28; Moderate 19/28 vs 18/28 at twice the false episodes), so they are
   not adopted (§0). The 16 Oct run on 66 labels had found them worse at both tiers.
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
- **`.env` (adk, 17 Oct):** holds the Telegram token and chat ID. It has no data.gov.sg key yet (§0, 17 Oct, later). Never print any of them.

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
