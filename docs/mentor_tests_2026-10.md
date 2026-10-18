# Mentor suggestions, round 2: pre-registered tests (B7)

**Status: pre-registration.** This section was written on 18 Oct 2026 and committed **before any of
the tests below were run**. The results are appended in a later section and do not change anything
above it.

## Why a second round

Suneel (mentor, 14 Oct) suggested kriging or a DEM instead of IDW, static catchment features, and
radar. The first tries (16–17 Oct) did not help:
- Kriging was scored only on rain error at gauges (`docs/kriging_vs_idw.md`): about 3% better on
  heavy rain. It was never scored on catching floods.
- Zone features went in as their own term (`rule_rain60_static`). The fit learned "built-up zones
  flood more", which mostly reflects where floods get reported. On 88 labels: High 8/28 against
  14/28 (`docs/candidate_selection_2026-10.md`).
- GLO-30 is a surface model, so in built-up areas "elevation" partly includes building height.

Akul (18 Oct) suggested better-targeted versions. These are tested here.

## Rules

- **Data:** 2017–2023 only. Forward-chaining cross-validation with validation years 2020–2023
  (`training.cross_validate`), on the 88 verified labels: 28 floods in the validation folds.
  **2024–2026 is not scored.**
- **Budgets unchanged:** 2.5 false High and 11 false Moderate episodes per zone-year. Thresholds come
  from the budget rule (`evaluation.select_threshold`).
- **Each test runs once.** No re-tuning after seeing results. At most 3 planned tests.
- **Baseline:** `rule_rain60`, out-of-fold on the same folds (`selection_2026-10-17.json`):
  - High 14/28 (90% CI 36–64%), 2.49 false episodes per zone-year.
  - Moderate 18/28 (50–79%), 4.52 false episodes per zone-year.
  - Event score 0.500.
  - The baseline is re-computed in the same run, so the pairing uses identical floods.

## Adoption margin (the same for T1 and T2)

A test's candidate is adopted only if **all three** hold against `rule_rain60`:
- (a) Its event score is at least **baseline + 0.05**.
- (b) At least one tier (High or Moderate) has a paired 90% bootstrap CI over floods
  (`select_candidates.paired_difference_ci`, 2,000 resamples, seed 0) with a **lower bound above 0**.
- (c) The other tier **loses at most 1 flood**.

Otherwise the result is reported as **"not distinguishable, kept the simpler rule"**.

**Expected outcome, stated up front:** with 28 floods the intervals are about ±15 points, so "no
change" is the likely and acceptable result.

## T1: runoff-weighted rain

- **Input:** `rain_60m × C_zone`, with a one-input `RuleModel` (`training.py`), the same as
  `rule_rain60`.
- **Runoff coefficient (rational method, fixed, not fitted to labels):**
  `C = 0.9 × b + 0.3 × (1 − b)`.
  - b is the built-up share (ESA WorldCover 2021, class 50) of the zone's **land** pixels.
  - Land is every valid pixel except permanent water (class 80). Open water is not catchment
    surface, which matters for Marina Bay and the reservoirs.
  - All non-built land is treated as vegetated (0.3).
  - The new column is `built_up_land_pct` in `data/reference/zone_static.csv`, with the same
    sources as the existing columns.
- **Why it might help:** it stays a one-input rule, so it cannot learn a separate "busy areas" term.
  It only scales each zone's rain by how much of it runs off.

## T2: kriged upper rain

- **Input:** for each zone and 5-minute step, the ordinary-kriging mean plus 1 kriging standard
  deviation, clipped at 0. Summed over the last 12 steps (60 min), with a one-input `RuleModel`.
- **Kriging:** the 16 Oct variogram, unchanged: exponential, nugget 0.46, partial sill 0.81,
  practical range 32 km, in units of the step's variance.
  - The kriging variance at each zone is scaled to mm² by the sample variance of that step's
    reporting gauges.
  - The 60-minute sum adds the SDs as if the steps' errors were fully correlated. This is the
    conservative upper bound.
- **Same rows as the store:** the same zone reference points and the same active-rain gate as the
  feature store. Only the rain column changes.
  - Gauges that did not report are left out of each step's system. Missing is never read as 0.
  - Only 2017–2023 is built (`assert_training_window`).
- **Why it might help:** both IDW and kriging under-predict heavy cells by about 16 mm/h. A
  plausible upper rain might catch floods earlier, at the cost of more false alarms. The budget
  rule then sets the threshold.

## T3 (conditional): rain30 for Moderate, rain60 for High

- **Gate, checked first:** the per-flood Moderate outcomes of `rule_rain30` (21/28) and
  `rule_rain60` (18/28), already saved in `selection_2026-10-17.json`.
  - If **2 or fewer** floods are caught by rain30 and missed by rain60, T3 is **skipped**: the extra
    catches mostly overlap.
- **If run:** High from `rule_rain60` at its budget threshold, unchanged. Moderate from `rule_rain30`
  at its own budget threshold.
- **Margin:** the event score is not defined for a two-rule hybrid, so (a) does not apply. Both of
  these must hold:
  - the Moderate paired 90% CI against `rule_rain60` has a lower bound above 0;
  - Moderate false episodes stay within the 11 budget.

## Deferred, not tested

- **Bare-earth DEM** (FABDEM, buildings and trees removed) plus a topographic wetness index, for
  where water collects. This is closer to the "catchment" idea than zone-average elevation from
  GLO-30.
  - Not before Demo Day.
  - The licence needs checking (probably CC BY-NC-SA 4.0, UNVERIFIED).
- **PUB drainage capacity and catchment boundaries:** not openly available (B2).

---

## Results (run once, 18 Oct 2026, 19:26 SGT)

- **Command:** `python -m floodsense.models.mentor_tests --date 2026-10-18 --tests t1 t2 t3 --allow-dirty`.
- **Output:** `models/candidates/mentor_tests_2026-10-18.json`.
- **Provenance:** the run was on top of the pre-registration commit `538924b` (19:22 SGT). The new
  code was not yet committed, so the file records `dirty: true`. The code was committed afterwards
  without changes.
- **Kriged-upper features:** built for 2017–2023 in 165 s
  (`python -m floodsense.features.krig_upper --start-year 2017 --end-year 2023`). Before the
  pre-registration commit, a timing build of 2021 was made in a scratch folder; nothing was scored.
- **Scope:** 28 validation floods (2020–2023). 2024–2026 was not scored. Baseline `rule_rain60`
  re-computed in the same run: event score 0.500, the same as on 17 Oct.

| Test | Event score | High (90% CI) | False High / zone-yr | Moderate (90% CI) | False Moderate / zone-yr | Verdict |
|---|---|---|---|---|---|---|
| Baseline `rule_rain60` | 0.500 | 14/28 (36–64%) | 2.49 | 18/28 (50–79%) | 4.52 | — |
| T1 runoff-weighted | 0.518 | 15/28 (39–68%) | 2.15 | 21/28 (61–89%) | 10.03 | not distinguishable, kept the simpler rule |
| T2 kriged mean + 1 SD | 0.446 | 14/28 (36–64%) | 2.07 | 21/28 (61–89%) | 8.50 | not distinguishable, kept the simpler rule |
| T3 rain30 for Moderate | — | 14/28 (rain60, unchanged) | 2.49 | 21/28 (61–89%) | 8.74 | **meets its pre-registered margin** |

**Paired differences against the baseline, over the same 28 floods (90% bootstrap CI):**

- **T1:**
  - High +1 flood (+0 to +11 points). Gained 2020-04-30 Lorong Gambir; lost none.
  - Moderate +3 (+4 to +21). Gained 2020-11-02 Lorong Gambir, 2020-11-02 Mount Vernon Rd and
    2021-08-20 Tampines Ave 10; lost none.
  - Criteria: (b) and (c) hold; **(a) fails** (0.518 against the 0.550 needed).
- **T2:**
  - High ±0 (−11 to +11). Gained 2 (2020-04-30 Lorong Gambir, 2020-06-23 Upper Changi Rd) and lost
    2 (2021-08-30 Hougang Ave 8, 2021-08-30 Punggol Way).
  - Moderate +3 (+4 to +21). Gained both 2020-11-02 floods and 2021-08-24 Cashew Rd.
  - The event score is lower, mainly at the 1 and 2 false-episode budgets (6/28 against 9 and 11/28).
  - **(a) fails.**
- **T3:**
  - The gate passed: 3 floods are caught at Moderate by rain30 alone (both 2020-11-02 floods and
    2021-08-20 Tampines Ave 10), and none by rain60 alone.
  - Moderate +3 (+4 to +21) at 8.74 false episodes per zone-year (budget 11). Both pre-registered
    conditions hold.

### What the results mean (read before acting on T3)

1. **The Moderate gain is the same in all three tests, and it comes from using more of the false-alarm
   budget.**
   - `rule_rain60`'s budget pick sits at 4.52 false Moderate episodes per zone-year. Its next looser
     threshold already breaks the budget: 0.30% gives 11.29, from
     `cutoff_comparison_2026-10-17.json`.
   - The three candidates reach 21/28 at 8.5–10.0 false episodes, so about twice the false Moderate
     alerts for 3 more floods.
   - Two of the 3 gained floods are on the same day (2 Nov 2020), so the evidence is closer to 2
     storms than 3 floods.
2. **T3 is not independent evidence.** Its numbers (21/28 at 8.74) were already in the 17 Oct results
   when the test was written. The pre-registration fixed the margin but could not hide the answer.
3. **The hybrid's live behaviour is untested.**
   - Two rules can disagree: a zone could be High by rain60 but below Moderate by rain30.
   - Serving it would need "High implies at least Moderate". Its combined false-episode rate was not
     measured.
   - It is also a second model to register, explain and keep in sync.
4. **T1 is the most promising for later work.**
   - It lost no flood at either tier and had fewer false High episodes (2.15 against 2.49).
   - It still missed the +0.05 event-score margin, so it is not adopted.
5. **Kriging uncertainty (T2) does not help:** it is lower at strict budgets and trades High floods
   one for one.

**Recommendation (adk's session):**
- Do not promote anything before Demo Day.
  - T1 and T2 are not adopted.
  - T3 met its bar, but for the reasons above its gain is mostly the budget the champion leaves
    unused, not better foresight.
- Promoting T3 is a team decision (B7 in the handoff). If the team wants it:
  1. Pre-register and measure the combined tier (High implies Moderate) first.
  2. Then Akul registers it as `candidate`.
