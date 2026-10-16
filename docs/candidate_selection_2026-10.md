# Candidate selection, October 2026 (B4.1, B4.2, B4.2b, B2.4)

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
"no change". All of this is re-run once the 14 unverified label rows are verified (see the end).

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
- **Re-run after verification.** Once Akul verifies the 14 new label rows (11 from 2017–19 and 3
  from 2020–21), run the same command again. The 3 rows from 2020–21 add validation floods, and the
  others add training floods. All of these numbers will change, and the recommendation should be
  checked again before the 23 Oct freeze.
