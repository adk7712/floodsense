# When a refit may change a threshold (proposal, 22 Oct 2026)

**Status: draft for a team decision. adk owns the model work; nothing here is adopted or built.**
The false-alarm budgets (2.5 High, 11 Moderate episodes per zone-year) are not touched (CLAUDE.md
rule 7). This only decides when a refit's new threshold replaces the served one.

## The problem

The Moderate threshold has moved with every label batch:

| Labels | Development floods (2017–2023) | Moderate threshold |
|---|---|---|
| 66 (version 1, 1 Oct) | 36 | 0.30% |
| 88 (version 3, 18 Oct) | 58 | 0.53% |
| 92 (refit, 22 Oct) | 62 | 0.40% |

The cause is in `select_threshold` (`src/floodsense/models/evaluation.py`): it takes the threshold
with the **highest hit rate** whose false episodes fit the budget. With about 30 validation floods
the hit rate moves in steps of 1/30, so a looser threshold that catches **one** more flood wins,
however much more of the budget it spends. On 22 Oct that was 20 of 30 against 19 of 30 (90% CIs
53–80% and 50–77%) for 7.24 against 4.43 false Moderate episodes per zone-year. The rule isn't
wrong, but its answer is mostly noise at this sample size, and each swing means re-registering,
re-promoting and re-documenting the model.

## Options

1. **Keep the served threshold unless the change is clearly better (recommended).** A refit's
   threshold replaces the served one at a tier only if, on the same cross-validation folds and
   events:
   - its false episodes fit the budget (as now), **and**
   - the paired 90% interval for the change in hit rate is above 0
     (`select_candidates.paired_difference_ci`, the same test as the mentor-test margin), **and**
   - the other tier loses at most one flood.

   Otherwise the served threshold stays, and the refit's result is reported next to it.
   - Pros: simple, uses code and a test the project already has, stops one-flood swings, and keeps
     the budgets.
   - Cons: with about 30 floods almost no change will pass, so thresholds rarely move until there
     are many more labels. That is the honest consequence of having few floods.

2. **Change how the rule picks within the budget.** For example, take the highest (quietest)
   threshold whose hit rate is inside the 90% interval of the best one (a "within the
   uncertainty" rule), rather than the single best.
   - Pros: the threshold itself becomes less jumpy, and every refit applies the same rule.
   - Cons: it changes the threshold method, so it needs its own pre-registration and
     cross-validation, and it would move today's thresholds too.

3. **Refit only at fixed points.** For example, after a label batch of at least N new verified
   floods, or once per round, never between.
   - Pros: fewer swings and less rework.
   - Cons: it limits how often the rule runs, but each run is just as noisy.

Options 1 and 3 can be combined.

## Applying it to the 22 Oct refit

This proposal was written **after** seeing the 22 Oct result, and under option 1 that refit would
not change the threshold. Applying the rule to it now would mean choosing the threshold by its
result, which is what rule 7 guards against. So, whichever option is chosen:

- **It applies from the next refit on.** The 22 Oct refit is decided under the rule in force when
  it was run (the budget rule as it stands): register it as `candidate` and promote it after adk's
  sign-off, once the median-warning question below is answered.
- Write the decision and its date in `docs/ROUND2_HANDOFF.md` before the next refit is run.

## Open question first: median warning time

At the refit's lower threshold (0.40%) the median warning is 26 minutes, against 33 minutes at
0.53%. A lower threshold can only make the first alert earlier, or the same, for any flood both
catch, so a lower median needs explaining. Two possibilities:
- the medians cover different floods (the extra catch may come with a short warning);
- floods without a known start time are left out.

The cut-off comparison file shows `warning_minutes` as null for some caught floods. Akul has asked
adk (22 Oct).
