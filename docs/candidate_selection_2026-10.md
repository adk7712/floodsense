# Candidate selection, October 2026 (B4.1, B4.2, B4.2b, B2.4)

> **Update, 17 Oct:** everything from the top down to the end of section 5 is the 16 Oct run on 66
> verified labels (history). The current run is on 88 verified labels; see "Re-run on 88 verified
> labels (17 Oct)" at the end. The recommendation is unchanged: keep `rule_rain60`.

**Run:** 16 Oct 2026, adk's session, on branch `adk/review-followups`. The results were reproduced
on clean commit `60e2b19`: every number matches the first run. **Results file:**
`models/candidates/selection_2026-10-16.json` (it records `git_sha` 60e2b19).

**Reproduce** (from the repo root, on a clean tree):
1. `git status --porcelain` must print nothing. `select_candidates.py` does not check for a dirty
   tree itself. Only `prior_sensitivity.py` records a `dirty` flag in its output.
2. `uv run python -m floodsense.models.select_candidates --date 2026-10-16`
3. `uv run python -m floodsense.models.prior_sensitivity --date 2026-10-16` (source:
   `src/floodsense/models/prior_sensitivity.py`; output: `models/candidates/prior_sensitivity_2026-10-16.json`)

**Recommendation: keep `rule_rain60`.** No new candidate beats it, and the static zone features
made the rain rule worse in cross-validation. There is nothing new for Akul to register as
`candidate` from this run. adk signs off; if signed off as is, the 21 Oct registration (B4.4) is
"no change". This 16 Oct run was repeated on 88 verified labels on 17 Oct (see the end).

## 1. Method and data

- **Feature store (B4.1).** Rebuilt from the local rainfall store with the README command
  (`python -m floodsense.features.build_features --start-year 2017 --end-year 2026`). It took about
  1.5 minutes. The active-rain gate now has a 1e-6 mm tolerance (`active_rain_mask` in
  `common/config.py`), and `pub_monitored` and `return_period_years` are gone.
  - The old store was not on this machine (`data/processed/features` was missing), so "before" is
    worked out from the rebuilt store: rows with `rain_120m >= 0.2` exactly, as the old gate kept.
  - All years: 7,587,282 rows now, against 7,577,129 under the old gate: **10,153 more rows (0.13%)**.
    Every extra row has `rain_120m` between 0.19999999999999996 and 0.19999999999999998 mm, which
    is one 0.2 mm tip after interpolation.
  - Development years 2017–2023: 5,480,033 rows now, against 5,472,584 under the old gate (7,449
    more). The model card's training run (1 Oct) had 5,472,630. The 46-row gap between the strict
    rebuild and the card is probably the rainfall store's gap-fill files, which are dated 2 Oct,
    after the card. **This is unverified.**
- **Labels.** Only verified rows: `load_flood_events()` leaves out the 14 unsigned rows. There are
  36 verified floods in 2017–2023, and 23 of them fall in the validation years 2020–2023 (8, 9, 4
  and 2). The training folds have 13, 21, 30 and 34 floods.
- **Procedure.** This is the existing one (`training.cross_validate`), with nothing changed:
  - forward-chaining CV with validation years 2020, 2021, 2022 and 2023, each trained on the
    earlier years only
  - cross-fitted Platt calibration
  - candidates ranked by the mean out-of-fold hit rate at 1, 2, 5 and 10 false episodes per
    zone-year, with PR-AUC as the tie-break
  - thresholds from the unchanged budget rule (High 2.5 and Moderate 11 false episodes per
    zone-year), over 55 zones × 4 years = 220 zone-years
- **Test years.** 2024–2026 were not scored in this task. The selection code only receives
  `LabelledData.development()`.
- **Intervals.** The 90% intervals are the repo's bootstrap (`evaluation.hit_rate_ci`: 2,000
  resamples, seed 0). The ablation's paired difference uses the same bootstrap, resampling events.
- **Warning time.** It is measured from the start of the alert episode, over hits with a reported
  time. CV warning times can't be compared with the test report's.

## 2. B4.2: reproducing `rule_rain60`

`rule_rain60` reproduces the model card on the rebuilt store and current labels:

| | Model card (1 Oct) | This run |
|---|---|---|
| Event score | 0.4891 | 0.4891 |
| Thresholds (Moderate / High) | 0.003 / 0.00706 | 0.003 / 0.00706 |
| High at budget | 12/23, 2.318 false episodes per zone-year (510 episodes) | 12/23, 2.318 (510 episodes) |
| Moderate at budget | 17/23, 10.545 (2,320 episodes) | 17/23, 10.559 (2,323 episodes) |
| PR-AUC (out-of-fold) | 0.009687 | 0.009690 |
| Out-of-fold rows | 3,306,950 | 3,311,435 |

**The only change is 3 more false Moderate episodes in 220 zone-years.** They come from the
rows the gate now keeps, which are borderline: one gauge tip in two hours. The other three core
candidates also reproduce their card event scores exactly (0.457, 0.152 and 0.087).

## 3. B4.2b: candidates that use `zone_static.csv`

The candidate code is in `src/floodsense/models/static_candidates.py`. The static values are
looked up from `ura_planning_area` inside the saved artifact (`StaticFloodModel`), so callers pass
the same rain feature rows as today. Everything is fitted inside each CV fold only.

- **`rule_rain60_static`**: the 60-minute rule plus one shared coefficient per static feature
  (nothing per zone). It fits `logit p = b + a·log(rain_60m / 25) + Σ β_k z_k`:
  - Each `z_k` is the feature standardised across the 55 zones.
  - Each `β_k` has a Gaussian prior with SD 0.5 log-odds per SD of the feature. This is an L2
    penalty on the static terms only, fixed before any run.
  - The data's weight is scaled to the number of training floods, not rows, because rows from one
    flood are not independent.
  - The shipped score is the rule's own score with a per-zone shift, so in effect each zone's
    rainfall threshold is multiplied by `exp(-β·z / a)`.
- **`rule_rain60_nostatic`**: the same model without the static terms. Its score is exactly the
  rule's score, so this is the ablation baseline.
- **`logistic_static`** and **`lightgbm_static`**: the core models with the 3 static columns added.
  The settings are otherwise the same. LightGBM's static columns are not constrained to be
  monotone.

### Results (out-of-fold, 2020–2023, 23 floods, thresholds from the budget rule)

| Candidate | Event score | High: hits (90% CI) | High: median warning | High: false episodes per zone-year | Moderate: hits (90% CI) | Moderate: median warning | Moderate: false episodes per zone-year |
|---|---|---|---|---|---|---|---|
| **rule_rain60** (champion) | **0.489** | **12/23 (35–70%)** | 37.5 min | 2.32 | **17/23 (57–87%)** | 31 min | 10.56 |
| rule_rain30 | 0.457 | 10/23 (26–61%) | 33 min | 2.40 | 17/23 (57–87%) | 30.5 min | 8.38 |
| logistic | 0.152 | 4/23 (4–30%) | 36 min | 1.77 | 5/23 (9–35%) | 41 min | 5.11 |
| lightgbm | 0.087 | 2/23 (0–17%) | 17 min | 0.73 | 2/23 (0–17%) | 17 min | 0.73 |
| rule_rain60_static | 0.315 | 6/23 (13–43%) | 25 min | 1.40 | 12/23 (35–70%) | 31 min | 8.24 |
| rule_rain60_nostatic | 0.489 | 12/23 (35–70%) | 37.5 min | 2.32 | 17/23 (57–87%) | 31 min | 10.56 |
| logistic_static | 0.141 | 1/23 (0–13%) | 17 min | 0.05 | 8/23 (17–52%) | 36 min | 6.05 |
| lightgbm_static | 0.087 | 2/23 (0–17%) | 17 min | 0.15 | 2/23 (0–17%) | 17 min | 0.15 |

- The thresholds for each candidate are in the JSON. Both LightGBM variants catch at most 2 floods
  at any threshold, so their High and Moderate thresholds coincide.
- LR and LightGBM still lose clearly, with or without the static features. That is expected while
  the training set is 13–34 floods. The plan's "might not with 100+" can't be tested yet.

## 4. B2.4: the ablation (same candidate with and without the static features)

Same folds, same events, same budget rule. "Difference" is the hit rate with the static features
minus without, with a paired 90% interval.

| Pair | Tier | Hits with / without | Difference (90% CI) | False episodes per zone-year, with / without |
|---|---|---|---|---|
| rule_rain60 ± static | High | 6/23 vs 12/23 | −26 pts (−43 to −9) | 1.40 vs 2.32 |
| rule_rain60 ± static | Moderate | 12/23 vs 17/23 | −22 pts (−39 to −4) | 8.24 vs 10.56 |
| logistic ± static | High | 1/23 vs 4/23 | −13 pts (−26 to −4) | 0.05 vs 1.77 |
| logistic ± static | Moderate | 8/23 vs 5/23 | +13 pts (0 to +30) | 6.05 vs 5.11 |
| lightgbm ± static | High and Moderate | 2/23 vs 2/23 (the same 2 floods) | 0 | 0.15 vs 0.73 |

**Plainly: on the 23 validation floods, the static features hurt the rain rule.** At the budget
thresholds it caught 6 fewer floods at High and 5 fewer at Moderate, and both intervals exclude
zero. That is not just a quieter model: it is also lower on every reference budget (4/6/7/12 against
7/8/15/15 at 1/2/5/10 false episodes per zone-year).
- For logistic regression, High is worse, and Moderate is indistinguishable (the interval touches
  zero).
- For LightGBM, there is no change in what it catches.

**Why the rule got worse.** Across all four folds the fit consistently learned:
- built-up share: +0.52 to +0.73 log-odds per SD (more built-up zones flood more)
- low-lying share: −0.15 to −0.34
- elevation: about −0.1

Relative to the rain slope (0.6–0.8), that moves each zone's effective rain threshold a long way.
In the model refitted on 2017–2023, the multiplier on the rain threshold runs from 0.15 (Serangoon)
to 19.5 (Straits View), with a median of 0.77.
- This mostly reflects where floods get **reported**: roads in built-up areas. It isn't drainage.
- The extra alerts in built-up zones use up the false-alarm budget. The global threshold then
  rises, and floods elsewhere are missed.
- So a prior of SD 0.5 per feature-SD was not "heavy" in threshold terms.

**Post-hoc sensitivity check (not used for selection).** After the main run, `rule_rain60_static`
was re-run with stronger priors. The script is `src/floodsense/models/prior_sensitivity.py`
(committed in `60e2b19`), with the same folds. Its output is
`models/candidates/prior_sensitivity_2026-10-16.json`. Cells show hits (90% CI), then false
episodes per zone-year:

| Prior SD | Event score | High at budget | Moderate at budget |
|---|---|---|---|
| 0.05 | 0.478 | 12/23 (35–70%), 2.32 | 17/23 (57–87%), 10.43 |
| 0.10 | 0.565 | 12/23 (35–70%), 2.00 | 17/23 (57–87%), 9.30 |
| 0.25 | 0.446 | 6/23 (13–43%), 0.66 | 16/23 (52–83%), 7.90 |
| 0.5 (main run) | 0.315 | 6/23 (13–43%), 1.40 | 12/23 (35–70%), 8.24 |

At SD 0.1, the floods caught at the actual budgets are the same as `rule_rain60` (no flood gained
or lost), with somewhat fewer false episodes (2.00 against 2.32 per zone-year at High, 9.30 against
10.56 at Moderate). Its higher event score comes from the 1 and 2 per zone-year reference levels
(8/23 and 12/23, against 7/23 and 8/23).

This should not be adopted:
- The setting was picked after seeing the same 23 validation floods.
- The pattern across settings is not smooth (0.478 → 0.565 → 0.446 → 0.315), which looks like
  noise.
- At the budgets the team actually uses, it catches nothing more.

If the team wants to pursue it, fix SD 0.1 **in advance** and check it on the re-run with the
newly verified labels.

## 5. Recommendation and handover

- **Keep `rule_rain60` as champion. Register nothing new from this run.** Say in the deck that the
  static features (B2) were tested by cross-validation on 2020–2023 and made the rule worse (High
  6/23 against 12/23, paired difference −26 points, 90% CI −43 to −9). That is a sourced result, not
  a limitation to hide.
- **Saved artifact.** `models/candidates/rule_rain60_static.joblib` is saved as the task asked (the
  best static candidate, refitted on 2017–2023 with its calibrator and budget thresholds of Moderate
  0.00226 and High 0.00531). **It is not recommended.** It has not been scored on 2024–2026.
- **If a static candidate is ever registered, `databricks/register_model.py` needs changes first**
  (Akul's script; not changed here):
  1. It always loads `models/flood_model.joblib`. It has no option for another path.
  2. Its default alias is `champion`, so pass `--alias candidate`.
  3. It logs `final_report.json`'s test metrics, which belong to the champion. A candidate has no
     test report, by the test-set rule.

  The artifact itself fits the script's contract:
  - It is a `FloodModel` subclass, and `feature_columns` is still the rain list.
  - The static lookup lives inside the artifact.
  - Scoring goes through `score_zone_features` with the same input columns.
  - It needs `scipy`, which scikit-learn already installs.
- **Re-run after verification (done 17 Oct; see the end).** Once Akul verifies the 14 new label rows (11 from 2017–19 and 3
  from 2020–21), run the same command again. The 3 rows from 2020–21 add validation floods, and the
  others add training floods. All of these numbers will change, and the recommendation should be
  checked again before the 23 Oct freeze.

## Re-run on 88 verified labels (17 Oct)

An earlier 17 Oct run on 80 verified labels happened and is superseded by this one (not kept).
The 16 Oct results (66 verified labels) above stay as history.

**Run:** 17 Oct 2026, adk's session, branch `rerun-88-labels`, from commit `33e9484`. The labels
are the 88 rows in `data/reference/flood_events.csv`, all verified (adk signed off 8 of the 11 new
candidates on 17 Oct and rejected 3; see `docs/label_review_2026-10-17.md`). The results files
record `git_sha` `33e9484`, and the sensitivity and refit files record `"dirty": false`.
- `models/candidates/selection_2026-10-17.json`
- `models/candidates/prior_sensitivity_2026-10-17.json`
- `models/candidates/rule_rain60_refit_2026-10-17.json` (the refit; its `.joblib` is gitignored)

**Reproduce** (from the repo root; the order matters because each script writes into
`models/candidates/`):
1. `git status --porcelain` prints nothing.
2. `python -m floodsense.models.prior_sensitivity --date 2026-10-17` (refuses a dirty tree).
3. Move its JSON out of `models/candidates/`, so the tree is clean again.
4. `python -m floodsense.models.select_candidates --date 2026-10-17` (does not check the tree).
5. Move the sensitivity JSON back.
6. `python -m floodsense.models.refit_champion --date 2026-10-17` (its JSON records `dirty`
   honestly, and the numbers don't depend on it).

Same procedure, budgets (2.5 and 11) and prior SD (0.5) as the 16 Oct run. Nothing was tuned.
2024–2026 were not scored.

### Counts

| | 16 Oct (66 verified) | 17 Oct (88 verified) |
|---|---|---|
| Verified floods 2017–2023 (training and CV) | 36 | **58** |
| Validation floods 2020–2023 | 23 | **28** (2020: 9, 2021: 12, 2022: 5, 2023: 2) |
| Development rows | (see above) | 5,480,033 |
| Out-of-fold rows | (see above) | 3,311,435 |

2024–2026 holds the other 30 verified floods (88 − 58). They were not used here.

### Selection (out-of-fold 2020–2023, 28 floods, thresholds from the budget rule)

Cells are hits out of 28 floods (90% CI), from the repo's bootstrap. "fa" is false alert episodes
per zone-year, over 220 zone-years (55 zones, 4 years).

| Candidate | Event score | High: hits (90% CI) | High: median warning | High: fa | Moderate: hits (90% CI) | Moderate: median warning | Moderate: fa |
|---|---|---|---|---|---|---|---|
| rule_rain30 | 0.491 | 11/28 (25–54%) | 26 min | 1.41 | 21/28 (61–89%) | 30 min | 8.74 |
| **rule_rain60** (champion rule) | **0.500** | **14/28 (36–64%)** | 45 min | 2.49 | 18/28 (50–79%) | 33 min | 4.52 |
| logistic | 0.232 | 4/28 (4–25%) | 29 min | 1.70 | 12/28 (29–57%) | 35.5 min | 8.16 |
| lightgbm | 0.438 | 11/28 (25–54%) | 38 min | 1.53 | 19/28 (54–82%) | 36 min | 8.60 |
| rule_rain60_static | 0.411 | 8/28 (14–43%) | 30 min | 1.39 | 19/28 (54–82%) | 21 min | 9.04 |
| rule_rain60_nostatic | 0.500 | 14/28 (36–64%) | 45 min | 2.49 | 18/28 (50–79%) | 33 min | 4.52 |
| logistic_static | 0.295 | 5/28 (7–29%) | 36 min | 2.13 | 17/28 (46–75%) | 41 min | 9.20 |
| lightgbm_static | 0.152 | 4/28 (4–25%) | 35 min | 1.09 | 7/28 (11–39%) | 42.5 min | 7.13 |

Floods caught at the four reference budgets (1, 2, 5, 10 false episodes per zone-year):
rule_rain60 9, 11, 18, 18; rule_rain30 7, 11, 16, 21; lightgbm 3, 11, 16, 19; rule_rain60_static
7, 8, 12, 19.

- **No candidate beats `rule_rain60` under the pre-set rule.** It has the highest event score
  (0.500). The closest are `rule_rain30` (0.491) and `lightgbm` (0.438). This holds.
- **`lightgbm` moved a lot.** Its score was 0.087 on 16 Oct (66 labels) and 0.317 on the earlier
  80-label run; it is now 0.438, close to the rule. It still loses, but the gap is small, and
  with 28 floods the order of the top three is not firmly settled. Its High warning time (38 min)
  is shorter than the rule's (45 min), and it uses about twice the Moderate false episodes (8.60
  against 4.52).
- **At Moderate alone, `rule_rain30` catches 3 more floods than `rule_rain60` (21 against 18) and
  `lightgbm` 1 more (19)**, but at 8.74 and 8.60 false episodes per zone-year against 4.52. At
  High, `rule_rain60` catches 3 more (14 against 11). The event score averages over the four
  budgets, which is why the rule still comes first.
- `rule_rain60` Moderate stays at 18/28 at both 5 and 10 false episodes per zone-year. The budget
  rule then picks the quietest threshold that gets 18, which is why its Moderate false episodes are
  4.52 and not near 11.

### Static-feature ablation (paired over the same 28 floods)

"Difference" is the hit rate with the static features minus without, with a paired 90% interval
from the results file (percentage points).

| Pair (with vs without static) | Tier | Hits with / without | Difference (90% CI) | fa with / without |
|---|---|---|---|---|
| rule_rain60_static vs rule_rain60_nostatic | High | 8/28 vs 14/28 | −21 pts (−36 to −7) | 1.39 vs 2.49 |
| rule_rain60_static vs rule_rain60_nostatic | Moderate | 19/28 vs 18/28 | +4 pts (−11 to +18) | 9.04 vs 4.52 |
| logistic_static vs logistic | High | 5/28 vs 4/28 | +4 pts (0 to +11) | 2.13 vs 1.70 |
| logistic_static vs logistic | Moderate | 17/28 vs 12/28 | +18 pts (+7 to +32) | 9.20 vs 8.16 |
| lightgbm_static vs lightgbm | High | 4/28 vs 11/28 | −25 pts (−43 to −11) | 1.09 vs 1.53 |
| lightgbm_static vs lightgbm | Moderate | 7/28 vs 19/28 | −43 pts (−61 to −25) | 7.13 vs 8.60 |

**For the rain rule the static features are mixed, and not adopted.** At High they catch 6 fewer
floods (8 against 14; the interval excludes 0). At Moderate they catch 1 more (19 against 18; the
interval includes 0) at twice the false episodes (9.04 against 4.52 per zone-year). The event
score is 0.411 against 0.500. At Moderate the static rule gains Lorong Gambir (Serangoon, 2 Nov
2020), Mount Vernon Rd (Toa Payoh, 2 Nov 2020) and Tampines Ave 10 (20 Aug 2021), and loses the
unnamed road in Jurong East (17 Apr 2021) and Punggol Way (30 Aug 2021). At High it gains Lorong
Gambir (30 Apr 2020) and loses 7. This is a change from 16 Oct, when the static features hurt at
both tiers; "made the rule worse" no longer holds at Moderate, so say "mixed, not adopted". The
effect on the other two models is not consistent (logistic gains at Moderate, LightGBM loses at
both tiers).

### Prior-SD sensitivity of `rule_rain60_static` (post-hoc; SD 0.5 is the pre-set value)

Hits out of 28 (90% CI), then fa. The 0.5 row is the main run. These runs use a different
Moderate threshold from the rule's (0.003, or 0.00226 for SD 0.5, against 0.00531).

| Prior SD | Event score | High: hits (90% CI), fa | Moderate: hits (90% CI), fa |
|---|---|---|---|
| 0.05 | 0.482 | 14/28 (36–64%), 2.25 | 20/28 (57–86%), 10.22 |
| **0.1** | **0.509** | 15/28 (39–68%), 2.17 | 20/28 (57–86%), 9.85 |
| 0.25 | 0.429 | 10/28 (21–50%), 1.36 | 17/28 (46–75%), 7.82 |
| 0.5 (main run) | 0.411 | 8/28 (14–43%), 1.39 | 19/28 (54–82%), 9.04 |

**The SD 0.1 idea, stated in advance on 16 Oct.** On the 88 labels it scores 0.509 against 0.500
for the rule. Against `rule_rain60` it catches 1 more flood at High (15 against 14; it gains
Lorong Gambir in Serangoon on 30 Apr 2020 and Kings Rd in Bukit Timah on 9 Nov 2021, and loses
Punggol Way on 30 Aug 2021) and 2 more at Moderate (20 against 18; it gains Tampines Ave 10 and
Cashew Rd in Aug 2021 and loses none), at 9.85 against 4.52 Moderate false episodes per
zone-year.
- **The paired intervals are not in the results file.** The prior-sensitivity file stores only
  the gained and lost floods and each run's own (unpaired) interval, and no paired difference with
  a 90% interval, so I cannot say whether a paired interval excludes 0. The unpaired intervals
  overlap almost completely (High 39–68% against 36–64%; Moderate 57–86% against 50–79%).
- The event-score gap (0.009) is under one flood at one reference budget.
- **Not adopted.** It was not the pre-set value, the gains are 2 to 3 floods, and the 3 floods
  involved are in the same validation set that suggested the idea, so this is a re-check on mostly
  the same data, not a clean confirmation. The event score across SDs (0.482, 0.509, 0.429, 0.411)
  is not smooth, which again says a single-run result.

### Refit of the champion rule on all 58 verified 2017–2023 floods

`rule_rain60` refitted with the existing training path (CV, Platt on out-of-fold scores, budget
thresholds, refit on 2017–2023). It is saved only to
`models/candidates/rule_rain60_refit_2026-10-17.joblib` (not registered), with
`rule_rain60_refit_2026-10-17.json`. The mm values are the 60-minute rain at which the calibrated
probability first reaches each threshold. 2024–2026 were not scored.

| | Champion (`models/flood_model.joblib`, 1 Oct, 36 floods) | Refit (58 floods) |
|---|---|---|
| Moderate threshold | 0.003 (0.30%), about 29.5 mm in 60 min | **0.00531 (0.53%), about 37.7 mm** |
| High threshold | 0.00706 (0.71%), about 44 mm | **0.00706 (0.71%), about 43.2 mm** |
| Calibration | Platt | Platt |
| Training rows | 5,472,630 | 5,480,033 |
| Out-of-fold event score | 0.489 (model card, 36 floods) | 0.500 (58 floods; not the same labels, so not comparable) |

- **High is unchanged** (0.71%); the equivalent rainfall moves by under 1 mm (44.05 to 43.15 mm).
- **Moderate would rise from 0.30% to 0.53%** (about 29.5 mm to 37.7 mm), so the Moderate band
  would be narrower (37.7 to 43.2 mm, against 29.5 to 44 mm). The budget rule picks the quietest
  threshold that catches the most floods within the false-alarm budget; with the added floods, no
  lower threshold catches more of the 28 validation floods *within budget*. Lower cut-offs catch
  more but break it: 0.30% catches 20 (11.29 false episodes per zone-year) and 0.20% catches 22
  (18.22), against a budget of 11 (`models/candidates/cutoff_comparison_2026-10-17.json`).
- **In cross-validation, `rule_rain60` at the refit Moderate threshold catches 18/28 floods (90% CI
  50–79%) with 4.52 false episodes per zone-year.** The head-to-head comparison with the old 0.30%
  threshold on these 28 floods is in `models/candidates/cutoff_comparison_2026-10-17.json` (handoff,
  "17 Oct, later": 0.30% catches 20/28, 90% CI 57–86%, at 11.29 false episodes per zone-year).
- The champion's mm values come from the same helper on the saved artifact (29.45 and 44.05 mm).
- This is a team decision (the thresholds are signed-off numbers). The budgets (2.5 and 11) and
  the rule are unchanged; the thresholds come from the budget rule.

### Conclusion and recommendation

- **Keep `rule_rain60` as the model. Nothing beats it under the pre-set rule.**
- **Open decision for adk and Akul (not made here): ship the refit or keep the 1 Oct champion?**
  - *Ship the refit:* it is trained on all 58 verified floods (22 more than the champion's 36), the
    same rule, and High stays 0.71%. Moderate moves from 0.30% to 0.53%.
  - *Keep the 1 Oct champion:* the published test result (Moderate 21/30 at 0.30%, High 12/30;
    scored once on 2024–2026) keeps describing the live model. If the refit ships, that result no
    longer describes the live model, and re-scoring the refit on 2024–2026 would be a **"second
    look"** and must be labelled so.
  - If the refit is chosen: Akul registers `models/candidates/rule_rain60_refit_2026-10-17.joblib`
    as `candidate`. `register_model.py` needs a path option (it always loads
    `models/flood_model.joblib`) and `--alias candidate`; it also logs `final_report.json`, which
    belongs to the 1 Oct champion. The `.joblib` is gitignored, so regenerate it on commit
    `33e9484` with `python -m floodsense.models.refit_champion --date 2026-10-17`, or share the
    file. adk signs off before promotion to `champion`, which must be before the freeze on 23 Oct,
    18:00 SGT.
- **Deck line:** the static features (B2) were tested by cross-validation on 2020–2023. On 28
  floods they catch fewer floods at High (8 against 14, 90% CI −36 to −7 points) and about the
  same at Moderate (19 against 18, −11 to +18 points) at twice the false episodes; the event
  score is 0.411 against 0.500. Say "mixed, not adopted".
