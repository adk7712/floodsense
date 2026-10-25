# Model comparison: what we tried, and why `rule_rain60` is the model

FloodSense warns each of Singapore's 55 planning areas ("zones") when a flash flood is likely in
the next hour. This page lists every model we have tried for that warning. For each one it gives
the result, the strengths and weaknesses, and the reason it is or isn't the model we use. All
numbers come from results files already in the repo (listed under each heading). Nothing new was
run for this page.

## Summary

- **The model in use is `rule_rain60`.** It looks at one number: how much rain fell in a zone in the
  last 60 minutes. It turns that into a chance of a reported flood in the next hour.
  - The live copy is **version 4** (since 23 Oct), registered as `champion` in Databricks Unity Catalog.
  - It warns at **Moderate** from about 33 mm of rain in an hour, and at **High** from about 43 mm.
- **Why this one:** no alternative has replaced it under rules we fixed before looking at the results.
  - On the 92-label run (25 Oct), no alternative beats it on the event score: rule_rain30 and T1
    score 0.492, LightGBM and T2 0.425, against 0.500 for the rule. T1 is the closest. It misses the
    pre-set margin, which needs 0.550.
  - On 88 labels (18 Oct), T3 (rain30 for Moderate) met its pre-registered margin, but it was not
    re-run on 92 labels. It awaits a team decision. A post-hoc static run (prior SD 0.1, chosen
    after seeing the data) scored 0.509 on 88 labels, slightly above the rule's 0.500.
  - It is also the simplest model, and the easiest to explain to a resident.
- **How strong the evidence is:** weak, in both directions. We validate on only 30 past floods (2020–2023), so every
  result has an uncertainty of about ±15 percentage points.
  - A model has to beat the rule by a clear margin before we switch.
  - Several alternatives came close but did not clear that margin.

## Terms used on this page

- **Flood:** a flash flood reported by PUB, the news or another public source, with a time and a
  place. Each one was checked by a second person (`data/reference/flood_events.csv`).
- **Caught:** the model raised a warning for that zone in the hour before the flood was reported.
- **False alert episode:** a continuous stretch of warnings in a zone with no flood during or just
  after it.
- **fa:** false alert episodes per zone-year. For example, 2.5 means a typical zone would see about
  2–3 false warnings a year.
- **Zone-year:** one zone over one year. Testing on 55 zones for 4 years gives 220 zone-years.
- **False-alarm budget:** the most false alerts we accept. It is 2.5 per zone-year at High and 11 at
  Moderate.
  - For each model, the warning threshold is the one that catches the most floods while staying
    within the budget.
  - The budgets were set once and never changed to help a model.
- **Event score:** the main ranking number. It is the average share of floods caught when the
  model is allowed 1, 2, 5 and 10 false alerts per zone-year.
  - It rewards models that do well across strict and loose settings, not just at one threshold.
- **90% CI:** a 90% confidence interval. "14/28 (36–64%)" means 14 of 28 floods were caught, and the
  true catch rate is plausibly between 36% and 64%.
- **Paired difference:** the gap between two models on the *same* floods. It is more precise than
  comparing two separate intervals.

## How the models were tested

*(README "Training and evaluation" and "Evaluation protocol"; `docs/candidate_selection_2026-10.md`)*

- **Data:** NEA's 5-minute rain gauge readings from 2017 to 2023, and 92 verified flood reports.
  - 58 of the floods are from 2017–2023 and are used to build and compare models.
  - 30 are from 2024 to Sep 2026. They are kept aside as a one-time final test.
- **Cross-validation:** each model is trained on earlier years and tested on the next year, for
  2020, 2021, 2022 and 2023 in turn. That gives **28 validation floods**. (They are not the held-out 2024–2026 test set.)
  - This mimics real use: the model never sees the year it is tested on.
- **Calibration:** each model's raw score is converted into a probability (here, "Platt
  calibration"), so that "1%" means roughly a 1-in-100 chance.
- **Final test set (2024–2026):** scored **once**, on 1 Oct, for the Round 1 submission, with
  version 1 of the model.
  - Any later use would be labelled a "second look", because repeated peeking makes the result
    over-optimistic.
  - None of the comparisons on this page used it.
- **Pre-registration:** for the latest tests (T1–T3 below), the method and the margin needed to win
  were written down and committed to git *before* running them, so the bar could not be moved
  after seeing the results.

## Summary table

*(Run on 25 Oct 2026: 92 verified labels, 30 validation floods in 2020–2023, forward-chaining
out-of-fold, same folds for every row. Cut-offs from the false-alarm budget rule (2.5 High, 11
Moderate). Scratch run outputs: `t1_vs_v4_results.json`, `reselect_92_results.json`,
`t2_92_results.json`. The 18 Oct pre-registered sections below keep their 88-label numbers.)*

The two tier columns show floods caught (with the 90% CI), false alerts per zone-year (fa), and
median warning time.

| Model | What it uses | Event score | High: caught, fa, median warning | Moderate: caught, fa, median warning | Status | Why |
|---|---|---|---|---|---|---|
| **rule_rain60** (v4) | Rain in the last 60 min | **0.500** | 15/30 (37–67%), 2.43, 45 min | 20/30 (53–80%), 7.24, 26 min | **In use (champion)** | Highest score of the models tried; the simplest rule to run and explain. No alternative met the pre-set margin (needs 0.550) |
| rule_rain30 | Rain in the last 30 min | 0.492 | 11/30 (23–50%), 1.42, 26 min | 23/30 (63–90%), 8.83, 28 min | Not used | Catches 4 fewer floods at High. Its 3 extra Moderate catches cost 1.6 more false Moderate alerts per zone-year |
| Logistic regression | Several rain measures | 0.217 | 3/30 (3–20%), 1.25, 29 min | 14/30 (33–63%), 7.52, 55 min | Not used | Catches far fewer floods than the rule at the same false-alarm level |
| LightGBM | Several rain measures | 0.425 | 10/30 (20–47%), 1.45, 45 min | 21/30 (57–83%), 9.09, 33 min | Not used | Lower score than the rule; 9.1 false Moderate alerts per zone-year against 7.2 for the rule |
| rule_rain60_static | 60-min rain plus 3 zone features | 0.375 | 7/30 (10–37%), 0.60, 11 min | 20/30 (53–80%), 9.05, 16 min | Not used | Catches 8 fewer floods at High than the rule. It learns where floods get *reported*, not where they happen |
| Logistic + zone features | As logistic, plus zone features | 0.317 | 6/30 (10–33%), 1.69, 32 min | 17/30 (40–70%), 7.62, 36 min | Not used | Zone features helped it, but it is still well below the rule |
| LightGBM + zone features | As LightGBM, plus zone features | 0.208 | 3/30 (3–20%), 0.76, 14 min | 13/30 (30–60%), 9.40, 30 min | Not used | Zone features made it much worse at both tiers |
| T1 runoff-weighted rain | 60-min rain × how much of the zone is paved | 0.492 | 15/30 (37–67%), 2.15, 45 min | 23/30 (63–90%), 10.13, 23 min | Not used | Lost no flood and gained 3 at Moderate, but its score is below the rule's, and it misses the margin (needs 0.550). It uses 10.1 of the 11 false Moderate budget |
| T2 kriged rain + uncertainty | A "plausible upper" rain estimate from kriging | 0.425 | 9/30 (17–43%), 1.11, 28 min | 20/30 (53–80%), 6.20, 26 min | Not used | Lowest score of the kriging variants. At High it catches 9 floods against 15 for the rule |
| T3 rain30 for Moderate | 60-min rule for High, 30-min rule for Moderate | not defined | Not re-run on 92 labels (88-label result: 14/28, 2.49) | Not re-run on 92 labels (88-label result: 21/28, 8.74) | Team decision pending | Its 88-label result met its pre-set margin, but the extra catches come from allowing more false alerts, not better foresight |
| Radar nowcast | Radar images moved forward in time | n/a | n/a | n/a | In use, for the "in 1 hour" map layer only | Forecasts rain, not floods; only 3 months of radar exist, too little to train on |

## The current model: `rule_rain60`

*(README status block; `models/model_card.json`; `models/final_report.json`;
`models/candidates/rule_rain60_refit_2026-10-17.json`; `docs/ROUND2_HANDOFF.md` §0, §2, B4.4/B4.5)*

**How it works**
- For each zone, it adds up the rain of the last 60 minutes. Gauges are spread unevenly, so each
  zone's rain is estimated from nearby gauges, with closer gauges counting more
  ("inverse-distance weighting", IDW).
- Calibration turns that rain total into a chance of a reported flood in the next hour.
- The Moderate and High thresholds are set by the false-alarm budgets.

**Versions**

| | Version 1 (1 Oct) | Version 3 (18–23 Oct) | Version 4 (live since 23 Oct) |
|---|---|---|---|
| Trained on | 66 verified labels (36 floods in 2017–2023) | 88 verified labels (58 floods in 2017–2023) | 92 verified labels (62 floods in 2017–2023) |
| Moderate warning from | 0.30% chance, about 29.5 mm in 60 min (indicative; handoff §2/§0) | 0.53% chance, about 38 mm (37.7 mm) | **0.40% chance, about 33 mm** (32.9 mm) |
| High warning from | 0.71% chance, about 44 mm (indicative; version 1's own calibration; handoff §2) | 0.71% chance, about 43 mm (43.2 mm) | **0.71% chance, about 43 mm** (43.4 mm) |
| Scored on the 2024–2026 test set? | Yes, once | No | **No.** A re-score would be a "second look" |

- The chances look small because floods are rare in any given zone-hour. What matters is how they
  rank hours against each other.
- Version 2 was the same model saved under a different file name. It is superseded.
- **Why the Moderate threshold moved from 0.30% to 0.53%:** after more floods were verified, 0.30%
  gave 11.29 false Moderate alerts per zone-year. That is over the budget of 11, so the budget
  rule picked 0.53% instead. The budgets themselves did not change.
- **What that cost:** at 0.30% the rule caught 20 of 28 validation floods (90% CI 57–86%); at 0.53% it
  catches 18 (50–79%).
  - The two floods lost are 2021-08-20 Tampines Ave 10 and 2021-08-24 Cashew Rd.
  - In exchange, false Moderate alerts fall from 11.29 to 4.52 per zone-year.
- adk signed off on version 3 on 18 Oct, and Akul promoted it to `champion`.
- **Version 4 (live since 23 Oct):** the same rule refitted on all 92 verified labels
  (62 floods in 2017–2023). The budget rule moves Moderate to 0.40% (about 32.9 mm); High stays
  0.71%. On the same 2020–2023 folds (30 floods) it catches 20 at Moderate (90% CI 53–80%) at 7.24
  false episodes per zone-year, against 19 (50–77%) at 4.43 for the 0.53% cut-off. The event score
  is 0.500 for both: not a measurable improvement (`models/model_card_v4.json`).
  - Every flood both cut-offs catch is warned at the same time or earlier at 0.40%. The median
    warning still falls (33 → 26 min) because the one extra catch, 2020-08-14 Craig Rd, came 10 min
    after the reported start.
  - adk signed off on 23 Oct, and Akul registered it as version 4 and promoted it to `champion`.

**Published final-test result (version 1, 2024 to Sep 2026, 30 floods, scored once)**

| Tier | Floods caught (90% CI) | Median warning before the report | fa |
|---|---|---|---|
| High (0.71%) | 12/30 (27–53%) | 10 min | 1.56 |
| Moderate (0.30%) | 21/30 (57–83%) | 15 min | 7.28 |

- This result describes version 1. Version 3 uses a different Moderate threshold, so it has no
  held-out score of its own.

**Version 3's rule in cross-validation** (28 floods):
- High: 14/28 (36–64%), fa 2.49, median warning 45 min.
- Moderate: 18/28 (50–79%), fa 4.52, median warning 33 min.
- These warning times are longer than on the final test. The two aren't directly comparable,
  so don't read the gap as a change in the model.

**Strengths**
- **Easy to explain:** "about 38 mm of rain in the last hour in your area" is something a resident
  or a town council can act on.
- **No alternative beat it on the 92-label run (25 Oct).** T1 scored 0.492 and T2 0.425, against
  0.500 for the rule. On 88 labels (18 Oct), T3 met its pre-set margin and awaits a team decision.
  The post-hoc static run (prior SD 0.1, chosen after seeing the data) scored 0.509 on 88 labels,
  slightly above the rule's 0.500.
- **Can't learn reporting bias.** It knows nothing about a zone except its rain, so it can't learn
  that busy areas simply get more floods *reported* (see the zone-feature models below).
- **Calibrated,** with thresholds set by a fixed rule rather than tuned by hand.

**Weaknesses**
- **Rain only.** It knows nothing about drains, terrain, tides, or whether a storm is growing.
- **It misses many floods.**
  - In cross-validation it catches about half at High (14/28, 90% CI 36–64%) and about two-thirds at
    Moderate (18/28, 50–79%).
  - On the final test (version 1): High 12/30 (27–53%), Moderate 21/30 (57–83%).
- **Most warnings are false alarms.** Floods are rare, so even a good warning system raises many
  alerts that come to nothing. The fa numbers above show how many.
- **Short warning on the final test** (a median of 10 and 15 minutes). It reacts to rain that has
  already fallen. The radar nowcast (below) is meant to add foresight.

## The alternatives

### rule_rain30: the same rule on 30-minute rain
*(`selection_2026-10-17.json`)*
- **What it is:** identical to the champion, but it looks at the last 30 minutes of rain instead of 60.
- **Result:** score 0.491.
  - High: 11/28 (25–54%), fa 1.41.
  - Moderate: 21/28 (61–89%), fa 8.74.
- **Strengths:**
  - It catches 3 more floods at Moderate.
  - It reacts faster to short, intense bursts.
- **Weaknesses:**
  - It catches 3 fewer floods at High (11 against 14).
  - Its Moderate setting raises about twice as many false alerts (8.74 against 4.52).
- **Why it isn't used:** its overall score is slightly lower (0.491 against 0.500). The two rules are
  too close to separate reliably, so the pre-set ranking (event score) decided, and the 60-minute
  rule stayed.
- Its best part, Moderate, is tested as a hybrid in T3 below.

### Logistic regression
*(`selection_2026-10-17.json`)*
- **What it is:** a standard statistical model that weighs several rain measures together (5, 15,
  30, 60 and 120-minute totals, rain over the last 72 hours, and how unusual the storm is).
- **Result:** score 0.232.
  - High: 4/28 (4–25%).
  - Moderate: 12/28 (29–57%), fa 8.16.
- **Strengths:** a well-known, transparent method that uses more information than the rule.
- **Weaknesses:** it has to learn a weight for every input from very few floods (30–56 per training
  period). With so little data, the weights are noisy.
- **Why it isn't used:** it catches far fewer floods than the rule at the same false-alarm level.
  Its High interval (4–25%) doesn't even overlap the rule's (36–64%).

### LightGBM (gradient-boosted trees)
*(`selection_2026-10-17.json`; `models/model_card.json`)*
- **What it is:** a machine-learning model built from many small decision trees, using the same rain
  measures as logistic regression. It can pick up non-linear patterns, such as "30-minute rain
  matters only once the ground is already wet".
- **Result:** score 0.438.
  - High: 11/28 (25–54%), fa 1.53.
  - Moderate: 19/28 (54–82%), fa 8.60.
- **How it changed as floods were added:** score 0.087 with 66 labels (16 Oct), 0.317 with 80, and
  0.438 with 88.
  - The 80-label figure is recorded in `candidate_selection_2026-10.md`; that run's results file was
    not kept.
- **Strengths:**
  - It is now the closest learned model to the rule.
  - With much more data it could learn patterns a single threshold can't.
- **Weaknesses:**
  - It is unstable: its score more than quadrupled as 22 floods were added, which shows how
    sensitive it is to small amounts of data.
  - Its High warnings come later (median 38 min against 45).
  - It raises nearly twice the Moderate false alerts.
- **Why it isn't used:** it still scores below the rule. With 28 validation floods, the order of the top
  three models isn't firmly settled, so the simpler model stays.
- **Open question:** whether it would win with 100 or more floods. We can't test that yet.

### Models with zone features ("static catchment features")
*(`selection_2026-10-17.json`; `prior_sensitivity_2026-10-17.json`; handoff B2/B4.2b/B2.4)*
- **What they are:** the models above, plus three fixed facts about each zone, from satellite data:
  - how built-up it is (ESA WorldCover land-cover map);
  - its average height above sea level;
  - its share of low-lying ground (both from the Copernicus GLO-30 elevation map).
- **How they were added:** `rule_rain60_static` adds one weight per zone feature to the rule. The
  weights were held close to zero unless the data clearly supported them (a "prior" with SD 0.5,
  fixed before the run).
- **This answered a mentor suggestion** (Suneel, 14 Oct): use zone characteristics such as paved
  surface and drainage.
- **Same model with and without zone features, on the same 28 floods:**

| Model | Tier | Floods caught: with / without | Difference (90% CI) |
|---|---|---|---|
| rule_rain60 | High | 8/28 vs 14/28 | −21 points (−36 to −7) |
| rule_rain60 | Moderate | 19/28 vs 18/28 | +4 points (−11 to +18), but at 9.04 vs 4.52 fa |
| Logistic | High | 5/28 vs 4/28 | +4 points (0 to +11) |
| Logistic | Moderate | 17/28 vs 12/28 | +18 points (+7 to +32) |
| LightGBM | High | 4/28 vs 11/28 | −25 points (−43 to −11) |
| LightGBM | Moderate | 7/28 vs 19/28 | −43 points (−61 to −25) |

- **Strengths:**
  - It is a direct, measured answer to the mentor's suggestion.
  - Zone features clearly helped logistic regression at Moderate.
- **Weaknesses: why it went wrong.**
  - The models learned that built-up zones flood more (+0.52 to +0.73 on the log-odds scale for each
    standard deviation of the features, on 66 labels).
  - That mostly reflects where floods get **reported**. Roads in busy areas are seen and reported;
    a flooded field is not. It says little about drainage.
  - The extra warnings in busy zones use up the false-alarm budget. The thresholds then have to
    rise, so floods elsewhere are missed. That is why High fell from 14 to 8 floods.
  - The elevation map (GLO-30) measures the top surface, including buildings and trees. In built-up
    areas "height" partly means building height, not ground level.
- **Why they aren't used:** the result is mixed at best, and worse at High. On the earlier
  66-label run (16 Oct) they were worse at both tiers.
- **A check made after the fact:** we also tried different strengths for the "stay near zero" prior.
  This was not used to choose a model, because SD 0.5 was fixed in advance.

| Prior SD (smaller = weights held closer to zero) | Event score | High: caught, fa | Moderate: caught, fa |
|---|---|---|---|
| 0.05 | 0.482 | 14/28 (36–64%), 2.25 | 20/28 (57–86%), 10.22 |
| **0.1** | 0.509 | 15/28 (39–68%), 2.17 | 20/28 (57–86%), 9.85 |
| 0.25 | 0.429 | 10/28 (21–50%), 1.36 | 17/28 (46–75%), 7.82 |
| 0.5 (the pre-set value) | 0.411 | 8/28 (14–43%), 1.39 | 19/28 (54–82%), 9.04 |

  - SD 0.1 scores 0.509 against the rule's 0.500. That is less than one flood's difference at one
    budget.
  - It gains 1 flood at High and 2 at Moderate, but with about twice the Moderate false alerts.
  - The scores jump around between settings (0.482, 0.509, 0.429, 0.411). That pattern looks like
    noise, not a real effect.
  - The setting was picked after seeing these floods, so it can't count as independent proof.
    Paired intervals for it were not recorded.

### Different Moderate thresholds for the current rule
*(`models/candidates/cutoff_comparison_2026-10-17.json`; handoff "17 Oct, later" block)*
- **What it is:** the champion rule itself, with the Moderate warning threshold moved up or down.
  The results are for the same 28 validation floods.

| Moderate threshold | Floods caught (90% CI) | fa (budget 11) | Median warning |
|---|---|---|---|
| 0.20% | 22/28 (64–89%) | 18.22 | 31 min |
| 0.30% (version 1) | 20/28 (57–86%) | **11.29** (over budget) | 31 min |
| 0.40% | 18/28 (50–79%) | 7.25 | 33 min |
| 0.45% | 18/28 (50–79%) | 6.03 | 33 min |
| **0.53% (version 3)** | 18/28 (50–79%) | 4.52 | 33 min |
| 0.60% | 18/28 (50–79%) | 3.55 | 28 min |
| 0.71% (the High threshold, for reference) | 14/28 (36–64%) | 2.49 | 45 min |

- **Lower thresholds** catch more floods: 2 more at 0.30%, 4 more at 0.20%. The price is many more
  false alerts. 0.30% just breaks the budget, and 0.20% is far over it.
- **Why 0.53%:** it is what the budget rule picks. Between 0.40% and 0.60% the rule catches the same
  18 floods, so 0.53% keeps the catch rate while cutting false alerts.
- The intervals overlap heavily, so thresholds from 0.30% to 0.60% aren't statistically
  distinguishable on 28 floods.

### T1: runoff-weighted rain (pre-registered, 18 Oct)
*(`docs/mentor_tests_2026-10.md`; `mentor_tests_2026-10-18.json`)*
- **What it is:** the champion rule, but each zone's rain is first multiplied by a runoff
  coefficient: the share of rain that runs off the surface instead of soaking in.
  - The coefficient is `C = 0.9 × b + 0.3 × (1 − b)`, where b is the built-up share of the zone's
    land (water bodies left out).
  - 0.9 for paved ground and 0.3 for vegetation are standard engineering values (the "rational
    method"). They were **not** fitted to our floods.
- **Why we tried it:** it uses the zone's surface, as the mentor suggested, but stays a one-input
  rule. So unlike the zone-feature models, it can't learn "busy zones flood more".
- **Result:** score 0.518.
  - High: 15/28 (39–68%), fa 2.15.
  - Moderate: 21/28 (61–89%), fa 10.03.
  - Against the champion on the same floods: High +1 flood (+0 to +11 points), Moderate +3
    (+4 to +21). It lost no flood that the champion caught.
- **Strengths:**
  - No flood lost at either tier.
  - Fewer false High alerts.
  - Based on a standard engineering method, not on our own labels.
- **Weaknesses:**
  - The gain at Moderate comes with more than twice the false alerts (10.03 against 4.52).
  - The overall score improved only slightly.
- **Why it isn't used:** the pre-set rule required the score to rise by at least 0.05 (to 0.550). It
  reached 0.518, so the result is "not distinguishable; kept the simpler rule".
- It is the most promising idea for future work, once more floods are available to test on.

### T2: kriged rain plus its uncertainty (pre-registered, 18 Oct)
*(`docs/mentor_tests_2026-10.md`; `mentor_tests_2026-10-18.json`)*
- **What it is:** a different way to estimate each zone's rain from the gauges.
  - "Kriging" estimates rain between gauges using how rain at nearby points tends to be related. It
    also says how unsure it is.
  - We used "best estimate + 1 standard deviation", a plausible upper value for the rain.
- **Why we tried it:** both kriging and the current method underestimate the heaviest storm cells
  by about 16 mm/h. An upper estimate might catch dangerous storms earlier.
- **Result:** score 0.446.
  - High: 14/28 (36–64%), fa 2.07. It gained 2 floods but lost 2 others.
  - Moderate: 21/28 (61–89%), fa 8.50, +3 floods (+4 to +21).
- **Strengths:**
  - A principled way to account for gaps between gauges.
  - Fewer false High alerts.
- **Weaknesses:**
  - The score dropped, mostly at the strictest settings (6 of 28 floods at 1–2 false alerts per
    zone-year, against 9 and 11 for the champion).
  - The uncertainty is large on any wet day, even far from the storm. The "upper" rain is therefore
    inflated everywhere (5–6 mm on average, against 1.7–2.3 mm for the current method). This
    probably explains the weak scores at strict settings.
- **Why it isn't used:** it fails the pre-set margin.
  - Its score is below the champion's, and it lost 2 floods at High.
  - A code review after the run found the "floods lost" criterion had counted the *net* change
    (+2 −2 = 0). It now counts floods lost, under which T2 also fails. The saved results file still
    shows the old value.

### T3: 30-minute rule for Moderate, 60-minute rule for High (pre-registered, 18 Oct)
*(`docs/mentor_tests_2026-10.md`; `mentor_tests_2026-10-18.json`)*
- **What it is:** a hybrid. High warnings come from the champion rule, unchanged. Moderate warnings
  come from the 30-minute rule, at its own budget threshold.
- **Why we tried it:** the 30-minute rule looked better at Moderate and the 60-minute rule better at
  High. A check first confirmed that the 30-minute rule catches 3 Moderate floods the 60-minute rule
  misses.
- **Result:**
  - High: 14/28 (36–64%), fa 2.49 (unchanged).
  - Moderate: 21/28 (61–89%), fa 8.74, +3 floods (+4 to +21).
  - This **meets the margin set in advance** for this test. The saved results file says `adopt`;
    that means "met its margin", not "promoted".
- **Strengths:**
  - It meets its margin.
  - High warnings are unchanged.
- **Weaknesses: why we're cautious.**
  - **It isn't new information.** T1, T2 and T3 all gain about 3 Moderate floods, mostly the same
    ones. Two of them are from the same storm on 2 Nov 2020, so the evidence is closer to 2 storms
    than 3 floods.
  - **The gain comes from allowing more false alerts, not from better foresight.** The champion's
    Moderate setting uses only 4.52 of the 11 false alerts allowed, because its next looser
    threshold goes over budget. Any rule that uses more of that allowance catches a few more floods.
  - **Its answer was already known.** These numbers were in the 17 Oct results before the test was
    written down, so passing it is weak evidence.
  - **Its live behaviour is untested.** Two rules can disagree: a zone could be High under one and
    not even Moderate under the other. It would need a "High always counts as Moderate" rule, and
    the false-alert rate of that combination hasn't been measured.
  - It means two models to register, explain and keep in sync.
- **Why it isn't used (yet):** adk recommends keeping the champion; the team decides (task B7 in the
  handoff).
  - If the team wants to pursue it: first pre-register and measure the combined version, then Akul
    registers it as a `candidate` model.

### Kriging instead of IDW (an input choice, not a model)
*(`docs/kriging_vs_idw.md`)*
- **What it is:** two ways of estimating rain between gauges.
  - IDW (current) averages nearby gauges, giving closer ones more weight.
  - Kriging also uses how rain at different distances tends to be related.
  - We hid each gauge in turn and predicted it from the others, over 4,000 storm moments in
    2017–2023.
- **Result (error in mm/h; lower is better):**

| Gauges grouped by actual rain | Error, IDW | Error, kriging | Kriging minus IDW (90% CI) |
|---|---|---|---|
| Heavy (10 mm/h or more) | 27.31 | 26.40 | −0.91 (−0.98 to −0.85): about 3% better |
| Light (under 10 mm/h) | 4.40 | 4.63 | +0.22 (+0.19 to +0.26): about 5% worse |

- **Strengths:** a small but real improvement for heavy rain, the rain that matters for floods.
- **Weaknesses:**
  - The gain is small next to the error itself.
  - Both methods underestimate the heaviest storm cells by about 16–17 mm/h.
  - Kriging is worse for light rain.
- **Why it isn't used:** the team judged the gain too small to switch. When kriging was later scored
  on catching floods (T2 above), only kriged mean + 1 kriging SD was scored. Kriged mean alone is
  untested, so we can't say whether kriging helps or hurts flood catching.

### Radar nowcast (used for the forecast map, not as a model input)
*(`docs/radar_nowcast_result.md`; `docs/radar_calibration.md`; handoff §3, A1)*
- **What it is:** NEA radar images every 5 minutes.
  - We estimate how the rain is moving from the last 3 images, and push it forward up to an hour.
  - This drives the app's "risk in 1 hour" layer, which feeds the predicted rain into the current
    model. Nothing was retrained.
- **Result** (43 storm days, 16 Jul to 30 Sep 2026; rain only, no flood labels):
  - The skill score (CSI, 0 to 1, higher is better) is for predicting at least 29.5 mm in the next
    60 minutes. That was version 1's Moderate level; today's Moderate is about 38 mm.

| Method | CSI |
|---|---|
| Radar nowcast | 0.23 |
| Radar held still (assume the rain doesn't move) | 0.18 |
| Gauge persistence (assume the last hour repeats) | 0.03 |

  - Better than gauge persistence: +0.180 (90% CI +0.020 to +0.262).
  - Better than radar held still: +0.037 (−0.028 to +0.084). Not conclusive, so most of the gain
    comes from having radar at all, not from the motion estimate.
- **Strengths:**
  - It gives foresight the gauges can't: it sees rain before it reaches a gauge.
  - It is calibrated against the gauges (hourly correlation 0.92).
- **Weaknesses:**
  - Radar reads about 20% low in the heaviest hours (ratio 0.78–0.79; `radar_calibration.md`).
  - It moves storms but can't predict them growing or dying out.
  - It was evaluated on only 43 storm days.
- **Why it isn't a model input:** the radar archive starts on 16 Jul 2026, only about 3 months.
  The flood model is trained on 2017–2023, when we have no radar, so it can't learn from it.
- **Status:** approved by the team on 17 Oct (handoff §3) for the forecast layer only. The radar
  doc's line about a decision "on 19 Oct" was written before that. Its proven skill is for rain,
  not floods.

## Not tried yet, or deferred

*(`docs/mentor_tests_2026-10.md` "Deferred"; handoff §0 B7, B2 row)*

- **Bare-earth elevation** (FABDEM, an elevation map with buildings and trees removed, plus a
  "wetness index" showing where water collects): not before Demo Day (decided by adk, 18 Oct).
  - This round was capped at 3 planned tests, and all three were used.
  - Unlike runoff, there is no standard engineering weight for elevation, so any rule we chose would
    be arbitrary.
  - Singapore's water mostly moves through man-made drains and canals, so a natural-terrain index
    may not show where floods really happen.
  - The FABDEM licence (probably CC BY-NC-SA 4.0, non-commercial) is **unverified**.
  - With 28 validation floods, a small gain wouldn't be distinguishable from luck.
  - It goes on the slides as a next step. About a day of work if revisited.
- **PUB drainage capacity and catchment boundaries:** not openly available (unverified).
- **A model trained on radar:** not possible with about 3 months of radar.
- **LightGBM or logistic regression with 100+ floods:** can't be tested until more floods are
  verified.
- **Re-scoring version 3 or 4 on the 2024–2026 test set:** not done. It would be a "second look".

## What we learned

- **Zone features pick up reporting bias.** Busy areas get more floods *reported*, so a model given
  zone facts learns to warn in busy areas. That uses up the false-alarm budget and costs floods
  elsewhere.
- **28 validation floods is very few.** Each result is uncertain by about ±15 points, so differences of 1–3
  floods can't be told apart from luck.
- **"More floods caught" often just means "more false alerts allowed".** The champion's Moderate
  setting uses 4.52 of the 11 false alerts allowed. T1, T2, T3 and the 30-minute rule all reach
  21 of 28 floods by using 8.5–10.0. That is about twice the false alerts for 3 more floods.
- **Results shift as floods are added.** LightGBM's score went 0.087, 0.317, 0.438 as the label set
  grew from 66 to 88. A single good run, such as prior SD 0.1, shouldn't be trusted on its own.
- **When results can't be separated, keep the simpler model.** The margins were written down in
  advance: the score up by 0.05, a clear gain in one tier, and no more than 1 flood lost in the
  other.
- **Fixed false-alarm budgets keep the comparison fair.** The 2.5 and 11 budgets were never changed
  to help a model look better.

## Sources

- `README.md` (status block, "Training and evaluation", "Evaluation protocol")
- `models/model_card.json`, `models/final_report.json`
- `docs/candidate_selection_2026-10.md`
- `models/candidates/selection_2026-10-16.json`, `selection_2026-10-17.json`
- `models/candidates/prior_sensitivity_2026-10-17.json`, `cutoff_comparison_2026-10-17.json`,
  `rule_rain60_refit_2026-10-17.json`
- `docs/mentor_tests_2026-10.md`, `models/candidates/mentor_tests_2026-10-18.json`
- `docs/kriging_vs_idw.md`, `docs/radar_nowcast_result.md`, `docs/radar_calibration.md`
- `docs/ROUND2_HANDOFF.md` (§0, §2, §3, §6, rows B4.4, B4.5, B7)
