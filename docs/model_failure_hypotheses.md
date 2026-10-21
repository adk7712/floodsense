# Why the other models did not beat `rule_rain60`: hypotheses and how to test them

**Status: discussion document, not a result.** It adds no new runs and no new model comparisons. Every
number below is copied from a file already in the repo (named under each heading) or is a simple count
or sum made from those files, and says so. Where a source is missing, this page says that.

`docs/model_comparison.md` lists what each model scored and its pros and cons. This page asks a
different question: **why did each one fall short of the champion, and how sure are we?**

## How to read this page

Every explanation is tagged with how well it is backed:

- **EVIDENCE:** a number or statement in a named file supports it directly.
- **INFERENCE:** a reasonable reading of the evidence, but something else could also explain it.
- **UNTESTED HYPOTHESIS:** plausible, but nothing we have run checks it.

Each hypothesis ends with **"Would settle it"**: a test that uses **2017-2023 data only**. If a test
would touch 2024-2026, it is marked **SECOND LOOK** and is not proposed. Any new test on the 28
cross-validation floods should be pre-registered first (as T1-T3 were in `docs/mentor_tests_2026-10.md`),
because each extra look at the same 28 floods raises the chance of finding a "win" by luck.

Terms (same as `docs/model_comparison.md`): *fa* is false alert episodes per zone-year (220 zone-years
= 55 zones x 4 years); *Moderate* and *High* are the two warning tiers, with budgets of 11 and 2.5 fa;
*event score* is the average share of floods caught at 1, 2, 5 and 10 fa; "90% CI" is a bootstrap
interval over floods.

## 1. Results at a glance

*(Sources: `models/candidates/selection_2026-10-17.json`, `models/candidates/mentor_tests_2026-10-18.json`,
`docs/candidate_selection_2026-10.md`, `docs/mentor_tests_2026-10.md`. Cross-validation on 2020-2023,
28 floods. 2024-2026 not used.)*

| Model | Event score | High: caught (90% CI), fa | Moderate: caught (90% CI), fa | Floods caught at 1 / 2 / 5 / 10 fa |
|---|---|---|---|---|
| **rule_rain60 (champion)** | **0.500** | 14/28 (36-64%), 2.49 | 18/28 (50-79%), 4.52 | 9 / 11 / 18 / 18 |
| rule_rain30 | 0.491 | 11/28 (25-54%), 1.41 | 21/28 (61-89%), 8.74 | 7 / 11 / 16 / 21 |
| Logistic | 0.232 | 4/28 (4-25%), 1.70 | 12/28 (29-57%), 8.16 | 3 / 4 / 7 / 12 |
| LightGBM | 0.438 | 11/28 (25-54%), 1.53 | 19/28 (54-82%), 8.60 | 3 / 11 / 16 / 19 |
| rule_rain60_static | 0.411 | 8/28 (14-43%), 1.39 | 19/28 (54-82%), 9.04 | 7 / 8 / 12 / 19 |
| Logistic + static | 0.295 | 5/28 (7-29%), 2.13 | 17/28 (46-75%), 9.20 | 2 / 2 / 12 / 17 |
| LightGBM + static | 0.152 | 4/28 (4-25%), 1.09 | 7/28 (11-39%), 7.13 | 2 / 4 / 4 / 7 |
| T1 runoff-weighted rain | 0.518 | 15/28 (39-68%), 2.15 | 21/28 (61-89%), 10.03 | 9 / 13 / 18 / 18 |
| T2 kriged mean + 1 SD | 0.446 | 14/28 (36-64%), 2.07 | 21/28 (61-89%), 8.50 | 6 / 6 / 17 / 21 |
| T3 rain30 for Moderate | not defined | 14/28 (champion's), 2.49 | 21/28 (61-89%), 8.74 | not defined |

Two things to notice before reading any explanation:

1. **The champion's Moderate result sits unusually low on false alerts (4.52 of an allowed 11).** Every
   candidate that reaches 21/28 or 19/28 does so at 8.5-10.0 fa. (See theme B.)
2. **The champion's lead is at the strict budgets.** At 10 fa it catches 18; rain30 catches 21,
   LightGBM 19 and rule_rain60_static 19 (`hits_at_reference_budgets` in `selection_2026-10-17.json`).
   At 5 fa it catches 18 against 16, 16 and 12. So "the champion is better" depends on which budget
   you look at.

## 2. Themes that affect every model

### A. Very few floods (28 in the comparison, 58 in all)

- **EVIDENCE:** the folds hold 28 floods (2020: 9, 2021: 12, 2022: 5, 2023: 2;
  `docs/candidate_selection_2026-10.md`). All 2017-2023 verified floods number 58. Typical 90% intervals
  are 25 to 29 points wide (for example 14/28, 36-64%).
- **EVIDENCE:** training folds hold 30, 39, 51 and 56 floods (`n_events` in `fold_coefficients`,
  `selection_2026-10-17.json`). LightGBM's score went 0.087 (66 labels), 0.317 (80 labels; the results
  file was not kept) to 0.438 (88 labels) as floods were added
  (`docs/candidate_selection_2026-10.md`).
- **INFERENCE:** most gaps between models are 1-3 floods, which is inside the noise. Several "shortfalls"
  below may be luck, not method.
- **Would settle it:** only more verified floods from 2017-2023. The handoff says web sources are nearly
  used up (B1: 22 new verified rows against a target of 60). So this cannot be settled before Demo Day.
  Until then, report differences as "not distinguishable".

### B. The budget rule turns a threshold choice into a false-alarm number

- **EVIDENCE:** the rule picks the threshold with the highest catch rate that fits the budget, with ties
  going to the higher threshold (`select_threshold`, `src/floodsense/models/evaluation.py`). Thresholds
  are searched on a log-spaced grid of 41 values from 1e-5 to 0.9 (`default_threshold_grid`, same file),
  so each step up or down changes fa in jumps.
- **EVIDENCE:** the champion catches 18 floods at every Moderate threshold from 0.40% to 0.60%, with fa
  from 7.25 down to 3.55 (`cutoff_comparison_2026-10-17.json`, in `docs/model_comparison.md`). The rule
  picks the quietest, 0.53% at 4.52 fa. The next looser threshold, 0.30%, gives 11.29 fa, just over the
  11 budget.
- **EVIDENCE:** rule_rain30 (8.74), LightGBM (8.60), T2 (8.50), T1 (10.03) and T3 (8.74) all land at
  8.5-10.0 fa at Moderate. Only the champion is at 4.52.
- **INFERENCE:** a difference in fa between two models is partly where each model's catch-rate
  "plateau" happens to end, and partly the model. A model that catches "3 more floods at twice the fa"
  has not necessarily found a better signal. It may just have been allowed to spend more of the 11.
- **Do not read the fa gap as a method effect.** In particular, the claim that the +1 SD term in T2
  caused 8.50 versus 4.52 false Moderate episodes **is not established**. The cause is untested, and is
  being checked separately. One thing counts against it being only the SD term: rain30 and LightGBM
  have no SD term and also land at 8.74 and 8.60.
- **UNTESTED HYPOTHESIS:** at matched fa, the champion's Moderate lead shrinks. Between 0.30% (20 caught,
  11.29 fa) and 0.40% (18 caught, 7.25 fa) a champion threshold would give about 8-9 fa and an unknown
  catch of 18-20. Rain30 catches 21 at 8.74. So the real gap at matched fa is somewhere between 1 and 3.
- **Would settle it:** re-score every model on a fine threshold grid and compare catch rates at the
  same fa (for example 5, 8 and 10), with 2017-2023 out-of-fold predictions. The full curves are not
  saved in the results files, so this needs a re-run of the existing scripts.

### C. Floods cluster on a few days

- **Count made for this page from `data/reference/flood_events.csv` (verified rows, 2017-2023 only):**
  the 28 cross-validation floods fall on 17 distinct days, and three days carry 3 each (2020-04-30,
  2020-06-23, 2020-11-02), which is 9 of the 28. All 58 floods fall on 35 distinct days.
- **EVIDENCE:** the same two 2 Nov 2020 floods (Lorong Gambir, Mount Vernon Rd) are gained in T1, T2 and
  T3, so "the evidence is closer to 2 storms than 3 floods" (`docs/mentor_tests_2026-10.md`).
- **EVIDENCE:** the flood intervals resample floods, not days (`hit_rate_ci` and
  `paired_difference_ci` bootstrap events). The kriging comparison, in contrast, also resampled whole
  days for rain error (`docs/kriging_vs_idw.md`).
- **INFERENCE:** floods on one day share one storm, so they are not independent. Intervals that treat
  them as independent are probably too narrow, and a "+3 floods" gain may be one or two storms.
- **Would settle it:** a cluster bootstrap that resamples storm days instead of floods, on the saved
  2020-2023 out-of-fold results.

### D. Labels record where floods get reported, not only where they happen

- **EVIDENCE:** a label is a flood that PUB, the news or another public source reported
  (`README.md`). 57 of the 58 floods in 2017-2023 are tagged cause "rain" and 1 "rain_tide"; 29 are
  "Minor" (count from `flood_events.csv`).
- **EVIDENCE:** with the static features, the fitted effect of "built-up share" was positive in every
  fold: +0.47, +0.56, +0.54, +0.58 log-odds per SD (`fold_coefficients`, `rule_rain60_static`, 88
  labels). The 66-label run gave +0.52 to +0.73 (`docs/candidate_selection_2026-10.md`).
- **EVIDENCE:** floods appear in only 25 of the 55 zones across 2017-2023, and in 14 zones in the
  2020-2023 folds (count from `flood_events.csv`).
- **INFERENCE:** the team's reading is that busy areas get more floods reported
  (`docs/candidate_selection_2026-10.md`). That fits the signs above. But **no reporting rate has been
  measured.** "Built-up zones really flood more" is not ruled out.
- **Would settle it:** fit the same rule with a single extra feature that has nothing to do with land
  cover: each zone's count of floods reported in the training years. If that feature learns the same
  kind of effect and hurts the same way, the static features were acting as a reporting proxy. Forward
  chaining, 2017-2023 only.

### E. How precisely each flood's time is known

- **Count from `flood_events.csv`:** of the 28 cross-validation floods, 11 are "day_only", 10
  "approx_15min", 5 "exact" and 2 "approx_hour". A date-only report counts as a hit if the zone was
  warned, but carries no warning time (`README.md`).
- **EVIDENCE:** of the 10 floods the champion misses at Moderate, 3 are day_only, 4 approx_15min, 2 exact
  and 1 approx_hour (cross-tab of `selection_2026-10-17.json` outcomes with `flood_events.csv`). So the
  champion catches 8 of the 11 day_only floods and 10 of the 17 others. These counts are too small to
  read, and no interval was computed.
- **UNTESTED HYPOTHESIS:** vague times make hits easier (a warning at any time that day may count) and
  make warning times unreliable. Results could differ between precise and vague floods.
- **Would settle it:** report catches by `time_precision` on the saved out-of-fold results, with
  intervals; also re-score using only floods with exact or approx_15min times.

### F. No drainage, terrain or tide information

- **EVIDENCE:** "Not built: tide, and PUB drainage capacity (not openly available)" (`README.md`).
  Bare-earth elevation and a wetness index were deferred (`docs/mentor_tests_2026-10.md`). The elevation
  data used (GLO-30) is a surface model that includes buildings and trees (`README.md`).
- **EVIDENCE:** 7 of the 28 floods are missed at their Moderate threshold by every one of the eight
  models in the 17 Oct selection run (list in section 3.1). Six of them are missed by T1-T3 as well;
  only Cashew Rd is caught, by T2.
- **INFERENCE:** a rain-only rule cannot see a blocked drain, a low road or a high tide. Some misses are
  probably of this kind. This is not shown: we have not looked at the rain recorded for the missed floods.
- **Would settle it:** for the seven shared misses, pull the 60-minute rain around each report time from
  the 2017-2023 gauges and compare it with the Moderate threshold (about 38 mm). Rain far below the
  threshold points to non-rain causes. Rain near or above it points to threshold or gauge problems.

### G. All models share the same rain estimate

- **EVIDENCE:** every model above uses gauge rain interpolated to zones, by IDW (T2 by kriging). Both
  methods under-predict heavy cells by about 16-17 mm/h (`docs/kriging_vs_idw.md`: bias -17.35 for IDW,
  -15.98 for kriging, on gauges with 10 mm/h or more).
- **INFERENCE:** if the input misses the heaviest cells, every model built on it misses the same floods.
  This fits the shared misses in F. It would also explain why several quite different models end up in
  the same place.
- **Would settle it:** for each missed flood, measure the distance to the nearest gauge that was
  reporting, and compare it with the same distance for caught floods. 2017-2023.

## 3. Model by model

### 3.1 The champion, `rule_rain60` (the yardstick)

*(Sources: `selection_2026-10-17.json`, `cutoff_comparison_2026-10-17.json`, `docs/model_comparison.md`.)*

- **Result:** High 14/28 (36-64%), 2.49 fa. Moderate 18/28 (50-79%), 4.52 fa. Event score 0.500.
- **Where it falls short.** It misses 14 floods at High and 10 at Moderate. The 7 missed by all eight
  selection-run models at Moderate are: 2020-06-23 Jurong Town Hall Rd; 2021-01-01 Lorong Halus;
  2021-07-12 Dunearn Rd; 2021-08-24 Dunearn Rd; 2021-08-24 Cashew Rd; 2022-02-27 Ubi Ave 3;
  2022-11-13 Stirling Rd.
- **Cost of the threshold move.** Moving Moderate from 0.30% to 0.53% lost 2 floods (20 to 18): 2021-08-20
  Tampines Ave 10 and 2021-08-24 Cashew Rd (`docs/model_comparison.md`).

| Hypothesis | Tag | Basis |
|---|---|---|
| The rule cannot see non-rain causes of flooding | INFERENCE | Theme F. The seven shared misses are consistent with it, not proof |
| Under-read heavy rain hides short, intense cells | INFERENCE | Theme G; 16-17 mm/h bias |
| Two of the seven shared misses are Dunearn Rd, Bukit Timah. This zone has the most floods (7 of 58) | UNTESTED HYPOTHESIS | A repeat site may flood at lower rain than the zone average, or the gauges near it may not represent it. Nothing checks it |
| The champion sits right at the edge of the High budget (2.49 of 2.5) and so gets the benefit of the budget | INFERENCE | Others sit lower at High (1.09-2.15). Their next threshold may be over budget. The curves are not saved, so this is unchecked |

- **Would settle it:** the rain-at-the-report-time check in theme F, with one extra column, the distance to
  the nearest gauge (theme G). For Dunearn Rd, also compare rain on its three flood days with other
  heavy days (2017-2023).

### 3.2 `rule_rain30` (the same rule on 30-minute rain)

*(Sources: `selection_2026-10-17.json`; `docs/mentor_tests_2026-10.md` T3 gate.)*

- **Result:** event score 0.491. High 11/28 (25-54%), 1.41 fa, median warning 26 min. Moderate 21/28
  (61-89%), 8.74 fa. Floods caught at 1/2/5/10 fa: 7/11/16/21, against 9/11/18/18 for the champion.
- **Why it falls short of the champion overall:** it is worse at the strict end (7 against 9 at 1 fa, 16
  against 18 at 5 fa) and at High (11 against 14).

| Hypothesis | Tag | Basis |
|---|---|---|
| A 30-minute total only crosses the threshold once a burst is well under way, so the warning comes later | INFERENCE | Median High warning 26 min against 45 for the champion. Moderate is 30 against 33. A later warning does not by itself show that fewer floods are caught |
| Short bursts that trigger it often fade, so strict budgets are spent on false alerts | UNTESTED HYPOTHESIS | The lower catch at 1 and 5 fa (7, 16) is consistent with it, but the false alerts themselves have not been studied |
| Its Moderate gain (+3) is mostly the budget the champion leaves unused | EVIDENCE | The three extra floods are both 2 Nov 2020 floods and Tampines Ave 10; the gate found none caught by rain60 only (`mentor_tests_2026-10-18.json`). 8.74 against 4.52 fa |
| Its High shortfall (11 against 14) is real | UNTESTED HYPOTHESIS | The paired interval for rain30 against rain60 at High was not recorded. The results file shows 3 fewer catches on 28 floods |
| High fa of 1.41 is well under the 2.5 budget, so a looser threshold may simply have been over budget | UNTESTED HYPOTHESIS | The fa for the next grid step is not in the saved results |

- **Would settle it:** compute the paired High difference (rain60 minus rain30) with a day-clustered
  interval, and the High curve at fine grid steps (theme B). 2017-2023.

### 3.3 Logistic regression (several rain measures)

*(Source: `selection_2026-10-17.json`; `docs/model_comparison.md`.)*

- **Result:** event score 0.232. High 4/28 (4-25%), 1.70 fa. Moderate 12/28 (29-57%), 8.16 fa. Caught at
  1/2/5/10 fa: 3/4/7/12. PR-AUC (ranking of individual hours) 0.0031 against 0.0101 for the champion.
- **Why it matters that PR-AUC is also low:** this is not only a threshold-placement effect. Its ranking
  of hours is poorer too.

| Hypothesis | Tag | Basis |
|---|---|---|
| Too many weights fitted from 30-56 floods per fold (5 rolling windows, 72-hour rain, storm rarity) | INFERENCE | Training-fold flood counts (theme A); the model has far more parameters than the rule's two. LightGBM improved with more labels, which hints at a data-size effect |
| The rolling windows (5, 15, 30, 60, 120 min) overlap heavily, so the weights are unstable | UNTESTED HYPOTHESIS | Not measured |
| The rule uses log of rain (`logit p = b + a*log(rain_60m/25)`, `docs/candidate_selection_2026-10.md`), while this model may use raw totals, so heavy rain is under-weighted | UNTESTED HYPOTHESIS | The sources I read do not say whether logistic's inputs are log-transformed |
| Weight learned from rows (hours) rather than floods, so a few long storms dominate | UNTESTED HYPOTHESIS | The static rule scales the data weight to the flood count for this reason, and logistic was not described as doing so |

- **Would settle it:** (1) fit logistic on log 60-minute rain alone; it should reproduce the rule. (2) Add
  one window at a time and watch the 2020-2023 score. (3) Check how correlated the inputs are. All
  2017-2023.

### 3.4 LightGBM (gradient-boosted trees)

*(Sources: `selection_2026-10-17.json`; `docs/candidate_selection_2026-10.md`.)*

- **Result:** event score 0.438. High 11/28 (25-54%), 1.53 fa, median warning 38 min. Moderate 19/28
  (54-82%), 8.60 fa. Caught at 1/2/5/10 fa: 3/11/16/19. PR-AUC 0.0246, which is **higher** than the
  champion's 0.0101.

| Hypothesis | Tag | Basis |
|---|---|---|
| Not enough floods to learn from | EVIDENCE | Score 0.087, 0.317, 0.438 as labels grew from 66 to 88 (the 80-label file was not kept) |
| Its most confident hours are often not floods: 3 caught at 1 fa against 9 for the champion | INFERENCE | The hit count at the strictest budget. Over-confidence on a few training storms would look like this |
| Row-level ranking (PR-AUC) and the event hit rate at a budget measure different things, so a better PR-AUC does not translate into more floods caught | INFERENCE | PR-AUC is higher than the champion's yet event score is lower. The reason is not isolated |
| Being non-linear helps with 100+ floods | UNTESTED HYPOTHESIS | Cannot be tested with 58 floods |

- **Would settle it:** a learning curve on 2017-2023 only: train on random subsets of the training floods
  (say 50%, 75%, 100%) and plot the score, with day-clustered resampling. If the curve is still rising
  at 100%, more data is the answer. Also compute event-level and row-level metrics side by side.

### 3.5 Models with zone features (`rule_rain60_static`, `logistic_static`, `lightgbm_static`)

*(Sources: `selection_2026-10-17.json` including `ablation` and `fold_coefficients`; `prior_sensitivity_2026-10-17.json`
via `docs/candidate_selection_2026-10.md`; `README.md`.)*

- **Result, rain rule with and without zone features, same 28 floods:** High 8/28 against 14/28 (paired
  difference -21 points, 90% CI -36 to -7). Moderate 19/28 against 18/28 (+4 points, -11 to +18), at 9.04
  against 4.52 fa. Event score 0.411 against 0.500.
- **Logistic with and without:** High 5/28 against 4/28 (+4 points, 0 to +11); Moderate 17/28 against
  12/28 (+18 points, +7 to +32). **LightGBM:** High 4/28 against 11/28 (-25 points, -43 to -11);
  Moderate 7/28 against 19/28 (-43 points, -61 to -25).
- **Prior-strength sweep (done after the fact, so not a selection):** event scores 0.482, 0.509, 0.429,
  0.411 for SD 0.05, 0.1, 0.25, 0.5. No paired interval was saved for SD 0.1.

| Hypothesis | Tag | Basis |
|---|---|---|
| The features pick up where floods are reported, not drainage | INFERENCE | Theme D: built-up effect is positive in all four folds. No reporting rate measured |
| Large per-zone shifts push thresholds far from the rain evidence | EVIDENCE | In the 2017-2023 refit, the rain-threshold multiplier ranges from 0.15 (Serangoon) to 19.5 (Straits View), median 0.77 (`docs/candidate_selection_2026-10.md`) |
| The three coefficients are learned from zones that flooded, then applied to the others | INFERENCE | Floods occur in 25 of 55 zones in 2017-2023 (theme D), so 30 zones' shifts are extrapolation from three numbers |
| Elevation partly measures building height | EVIDENCE | GLO-30 is a surface model (`README.md`). Its fitted effect is small (about -0.05 to -0.14 in three folds, +0.004 in one) |
| The three features overlap | EVIDENCE | Computed for this page from `data/reference/zone_static.csv` (55 zones): correlation of elevation with low-lying share is -0.69; built-up with low-lying -0.42; built-up with elevation -0.04. Overlap makes individual coefficients less reliable |
| "The extra alerts in built-up zones use up the false-alarm budget" (the earlier explanation for the High loss) | UNTESTED HYPOTHESIS | The saved High fa for the static rule is 1.39, well under 2.5, so the budget was not used up at High. See "Source notes" |
| LightGBM can memorise zones: the 3 static values are fixed per zone, so 55 distinct value sets act like a zone ID | INFERENCE | The -43 point loss at Moderate and the note that its static columns are not monotone (`docs/candidate_selection_2026-10.md`). Not directly shown |
| Logistic gains at Moderate because it was weak to begin with | INFERENCE | Its baseline is 12/28, far below the rule's 18/28. Zone features gave it a rough "where do floods get reported" signal |
| The prior-sweep "SD 0.1 matches the rule" is noise | EVIDENCE | The scores are not smooth across settings (0.482, 0.509, 0.429, 0.411); the SD was chosen after seeing these floods |

- **Would settle it:** (1) the reporting-count placebo in theme D. (2) Replace the three real static values
  with random values per zone (a placebo): if LightGBM degrades just as much, it is memorising zones.
  (3) Fit using zones' static features only for zones with a flood, and check how far predictions move for
  zones without. All 2017-2023.

### 3.6 T1: runoff-weighted rain

*(Sources: `docs/mentor_tests_2026-10.md`; `mentor_tests_2026-10-18.json`.)*

- **Result:** event score 0.518. High 15/28 (39-68%), 2.15 fa (+1 flood, paired CI +0 to +11 points).
  Moderate 21/28 (61-89%), 10.03 fa (+3 floods, +4 to +21 points). It lost no flood the champion caught.
  The pre-set margin was 0.550.
- **Why it fell short of the margin:** the event score rose by 0.018, which is 2 floods summed over the
  four reference budgets (0.0179 x 4 x 28 = 2.0, arithmetic for this page). The margin needed about 5.6.

| Hypothesis | Tag | Basis |
|---|---|---|
| The Moderate gain is budget use, not better signal | EVIDENCE | 10.03 against 4.52 fa; the same floods as in T2 and T3 (theme B) |
| A land-cover runoff factor is too crude: it says how much rain runs off, not whether the drain copes | INFERENCE | The factor `C = 0.9b + 0.3(1-b)` ranges only 0.31-0.83 across zones and uses no drainage data (`mentor_tests_2026-10-18.json`, `runoff_coefficient`; theme F) |
| It still behaves like a mild built-up effect, so some of theme D applies | UNTESTED HYPOTHESIS | It scales rain up in built-up zones. The pre-registration argues it cannot learn a separate busy-area term, which is true, but a fixed scaling still favours built-up zones |
| Its row-level ranking improved a lot (PR-AUC 0.0264 against 0.0101) but the event score barely moved | EVIDENCE | The two metrics differ, as for LightGBM (section 3.4) |
| The 0.9 and 0.3 coefficients were not tuned | EVIDENCE | By design; they are standard engineering values, not fitted to labels |

- **Would settle it:** the matched-fa comparison (theme B), and a day-clustered interval (theme C). Trying
  other coefficients would be tuning on these floods, and needs a new pre-registration.

### 3.7 T2: kriged rain plus one standard deviation

*(Sources: `docs/mentor_tests_2026-10.md` "Review notes"; `mentor_tests_2026-10-18.json`;
`docs/kriging_vs_idw.md`.)*

- **Result:** event score 0.446. High 14/28 (36-64%), 2.07 fa (gained 2020-04-30 Lorong Gambir and
  2020-06-23 Upper Changi Rd; lost 2021-08-30 Hougang Ave 8 and 2021-08-30 Punggol Way). Moderate 21/28
  (61-89%), 8.50 fa. At 1 and 2 fa it caught 6/28 against 9 and 11 for the champion
  (`docs/mentor_tests_2026-10.md`).
- **After the code review:** under the corrected reading of criterion (c), "floods lost", T2 also fails
  that criterion. The saved JSON still shows the old net value.

| Hypothesis | Tag | Basis |
|---|---|---|
| The upper-rain value is inflated everywhere on wet days, which blurs the difference between storm and non-storm zones | EVIDENCE | Mean kriged upper rain is 5-6 mm against 1.7-2.3 mm for IDW; the SD is island-wide on any wet step (`docs/mentor_tests_2026-10.md`). Row-level PR-AUC is 0.0021 against 0.0101 for the champion (`mentor_tests_2026-10-18.json`), which fits a blurrier ranking. The cause-and-effect link is still **inference** |
| Adding the SDs of the 12 steps as if fully correlated over-inflates the 60-minute sum | EVIDENCE | By definition of the test ("conservative upper bound"). Its effect on results is untested |
| The kriged mean adds little: kriging's gain is about 3% on heavy rain | EVIDENCE | `docs/kriging_vs_idw.md`: 27.31 against 26.40 mm/h |
| A single fixed variogram for all storms is a poor fit for some | UNTESTED HYPOTHESIS | Listed as a limit in `docs/kriging_vs_idw.md`, with no check of its effect |
| The population-variance versus sample-variance slip changed conclusions | EVIDENCE (against) | About 0.8% on the SD term, and the threshold is reset afterwards (`docs/mentor_tests_2026-10.md`) |
| **The +1 SD term caused 8.50 against 4.52 false Moderate episodes** | **NOT ESTABLISHED** | The cause of the fa gap is **untested and is being checked separately.** Rain30 and LightGBM reach 8.74 and 8.60 without any SD term (theme B). Do not cite this claim as a finding |

- **Would settle it:** (1) run the kriged mean alone (SD set to 0) through the same pipeline. If it matches
  IDW, the SD term is responsible for the loss in score. (2) Check the fa of each variant at matched
  catch counts (theme B). (3) Limit the SD to zones near the storm. Each is a new run, so pre-register
  first. 2017-2023.

### 3.8 T3: rain30 for Moderate, rain60 for High

*(Sources: `docs/mentor_tests_2026-10.md`; `mentor_tests_2026-10-18.json`.)*

- **Result:** High 14/28 (36-64%), 2.49 fa (unchanged). Moderate 21/28 (61-89%), 8.74 fa (+3, +4 to +21
  points). It **met** its pre-registered margin. This section is about why that is weaker evidence than it
  looks, not about a shortfall in the counts.

| Hypothesis | Tag | Basis |
|---|---|---|
| The gain is the unused budget, not better foresight | EVIDENCE | 8.74 against 4.52 fa; the same +3 in T1 and T2 |
| The evidence is about 2 storms, not 3 floods | EVIDENCE | Two of the three gained floods are on 2 Nov 2020 (theme C) |
| Passing was nearly known beforehand | EVIDENCE | The 21/28 at 8.74 figures were in the 17 Oct results before the test was written |
| The combined tier is worse than it looks, because two rules can disagree | UNTESTED HYPOTHESIS | A "High implies Moderate" rule would add some alerts. The false-alert rate of the combination was never measured |
| At matched fa, the gain over the champion is smaller than +3 | INFERENCE | Theme B: the champion's own curve passes 20 caught at 11.29 fa |

- **Would settle it:** measure the combined tier's fa and catches on the 2020-2023 out-of-fold results,
  with a day-clustered interval, and the matched-fa comparison (theme B).

### 3.9 Radar nowcast (rain forecast for the "in 1 hour" map layer)

*(Sources: `docs/radar_nowcast_result.md`; `docs/radar_calibration.md`; `docs/ROUND2_HANDOFF.md` section 3.)*

This is **not a flood model** and was not compared on flood catches. It has no flood score to fall short
of the champion with. The question is why its rain forecast skill is modest.

- **Result (43 storm days, 16 Jul to 30 Sep 2026; no flood labels used; these dates fall in the 2024–2026 period, so any further use of them beyond this existing rain-only result would be a "second look"):** CSI for at least 29.5 mm in
  60 minutes is 0.23 for the nowcast, 0.18 for radar held still, 0.03 for gauge persistence and 0.56 for
  radar later-observed (the upper bound). For at least 44.5 mm: 0.12, 0.08, 0.00 and 0.34. Gain over
  gauge persistence at 29.5 mm: +0.180 (90% CI +0.020 to +0.262). Gain over radar held still: +0.037
  (-0.028 to +0.084), which is not conclusive.

| Hypothesis | Tag | Basis |
|---|---|---|
| The nowcast moves rain but cannot grow or shrink a storm | EVIDENCE | "Rain moves but does not grow or decay" (`docs/radar_nowcast_result.md`) |
| Most skill is lost in forecasting, not in reading the radar | EVIDENCE | Observed radar reaches CSI 0.56 and 0.34 while the nowcast gets 0.23 and 0.12 |
| Radar reads low in the heaviest hours | EVIDENCE | Radar/gauge ratio 0.78 (283 hours) and 0.79 (378 hours) for hours of 20 mm or more (`docs/radar_calibration.md`) |
| The sample is small | EVIDENCE | 43 storm days; the motion gain interval includes 0 |
| Judged at 29.5 mm, the old Moderate level, not today's about 38 mm | EVIDENCE | `docs/radar_nowcast_result.md` against the v3 thresholds in `docs/model_comparison.md`. Skill at 38 mm was not measured |
| A better motion or growth model would close the gap | UNTESTED HYPOTHESIS | The doc says there is "room to improve"; nothing has been tried |

- **Would settle it:** nothing with 2017-2023 data, because the radar archive starts on 16 Jul 2026. Radar
  skill on **floods** would have to be scored on 2026 floods, which are inside the 2024-2026 test period.
  That would be a **SECOND LOOK**, on very few floods, and is not proposed. What can be done: more
  rain-only storm days as the archive grows, scored the same way, with intervals resampled by day.

### 3.10 Kriging instead of IDW (an input choice, not a model)

*(Source: `docs/kriging_vs_idw.md`, 4,000 storm steps, 1,352 days, 2017-2023.)*

- **Result (error in mm/h, lower is better):** heavy rain (10 mm/h or more) IDW 27.31, kriging 26.40,
  difference -0.91 (90% CI -0.98 to -0.85). Light rain IDW 4.40, kriging 4.63, difference +0.22
  (+0.19 to +0.26). Very heavy (30 or more): 39.72 against 38.28.
- **Why it did not win:** the gain is about 3% on heavy rain, and kriging is about 5% worse on light rain.

| Hypothesis | Tag | Basis |
|---|---|---|
| The limit is gauge spacing and the small size of convective cells, not the interpolation formula | INFERENCE | A hidden gauge in a storm core is "rarely matched by its neighbours", and neither method fixes the -16 to -17 mm/h bias (`docs/kriging_vs_idw.md`). The gauge spacing itself is not given in the sources I read |
| One variogram for all steps limits kriging | UNTESTED HYPOTHESIS | Listed as a limit; the effect was not tested |
| Error at gauge points says little about error that matters for floods | EVIDENCE | The doc says it was "not tested against flood labels" on 16 Oct. T2 (section 3.7) did test a variant on floods and found no gain |
| Storm steps were chosen by a gauge maximum, so light-rain rows are quiet gauges during storms | EVIDENCE | Listed as a limit in the doc |

- **Would settle it:** the same leave-one-gauge-out comparison restricted to the hour before each of the
  58 floods and to the gauges nearest to them (2017-2023), with intervals resampled by day.

## 4. What each test would cost, in one list

All use 2017-2023 data only, and most reuse saved out-of-fold results. None touches 2024-2026.

1. Day-clustered bootstrap on all comparisons (themes A and C). Cheap.
2. Hits versus fa on a fine threshold grid for all models, then compare at matched fa (theme B). Needs a
   re-run of the existing scripts, because curves are not saved.
3. Hits split by `time_precision` (theme E). Cheap.
4. Rain at the report time and distance to the nearest gauge for the seven shared misses (themes F, G).
   Needs the gauge store.
5. Reporting-count placebo and random-static-value placebo (theme D and section 3.5). Moderate effort;
   pre-register.
6. Learning curve for LightGBM and logistic (sections 3.3 and 3.4). Moderate effort.
7. Kriged mean alone, without the SD term (section 3.7). Needs the kriging builder (165 s for 2017-2023
   according to `docs/mentor_tests_2026-10.md`).

## 5. Source notes: things that do not line up between documents

- **Budget-crowding explanation for the static-feature High loss.** `docs/candidate_selection_2026-10.md`
  and `docs/model_comparison.md` say the extra alerts in built-up zones use up the false-alarm budget and
  push the threshold up. In `selection_2026-10-17.json` the static rule's High fa is 1.39 (budget 2.5)
  and its calibrated High threshold is 0.00531, lower than the champion's 0.00706. Thresholds from
  different calibrations are not strictly comparable, so this is not proof the claim is wrong, but the
  saved numbers do not show the budget being used up at High.
- **Radar doc against team decision.** `docs/radar_nowcast_result.md` says the layer should be "labelled
  experimental" and that the decision is on 19 Oct. The handoff records GO on 17 Oct, and `CLAUDE.md`
  bans "(experimental)" labels in the app. (`docs/model_comparison.md` already notes the date.)
- **Radar thresholds.** The radar doc uses 29.5 mm (Moderate) and 44.5 mm (High). The 17 Oct refit notes
  give the version 1 champion as 29.45 and 44.05 mm and the live version 3 Moderate as about 37.7 mm
  (`docs/candidate_selection_2026-10.md`). So skill at today's Moderate level is unmeasured.
- **PR-AUC and event score disagree** for LightGBM (PR-AUC 0.0246, event score 0.438) and T1 (PR-AUC
  0.0264, event score 0.518) against the champion (0.0101, 0.500). Both beat the champion on the row
  metric and neither clearly on the event metric. The docs rank by event score by pre-set rule, which is
  fine, but this should not be left unexplained if asked.
- **Sources not saved.** The 80-label LightGBM score (0.317) has no results file. Full threshold-versus-fa
  curves are not saved in any results file (only the four reference budgets). No paired interval was saved for rain30 against rain60 at
  High, or for prior SD 0.1.
- **Counts made for this page** (clusters, time precision, zone counts, zone-feature correlations) come
  from `data/reference/flood_events.csv` and `data/reference/zone_static.csv`, verified 2017-2023 rows
  only. They are not in any earlier doc, so a reviewer should recheck them.

## 6. What this document does not claim

- It does **not** claim any hypothesis marked INFERENCE or UNTESTED HYPOTHESIS is true. Only EVIDENCE
  rows have a number behind them, and even those show a link, not a proven cause.
- It does **not** claim the champion is better than the alternatives. With 28 floods on about 17 storm
  days, most gaps are 1-3 floods and inside the noise (themes A and C). The champion is kept because it
  is the simplest model and no rival met a margin set in advance.
- It does **not** claim the +1 SD term caused the 8.50 against 4.52 false Moderate episodes. That is
  untested, and being checked separately.
- It does **not** claim reporting bias, missing drainage or a gauge problem explains the missed floods.
  They are candidate explanations (themes D, F, G).
- It does **not** say radar helps catch floods. Radar skill is for rain, on 43 storm days in 2026; it has
  never been scored against flood labels.
- It does **not** use or propose using 2024-2026 data. The one place where that would be needed (radar
  against floods) is marked a SECOND LOOK and not recommended.
- It does **not** run or recommend any change to the false-alarm budgets (2.5 and 11) or to the live
  model. Whether to pursue T3 or any test above is the team's decision (B7 in the handoff).
