# T1 vs v4: decision rule (written 25 Oct 2026, before any run on the 92-label basis)

**Status:** pre-registration for the promote-or-keep decision. Written and committed **before** the
comparison below is run. Results go in a later section and do not change anything above it.

## Context

- **v4 (champion, live since 23 Oct):** `rule_rain60` refit on 92 verified labels. Moderate 0.40%
  (about 32.9 mm in 60 min), High 0.71% (about 43.4 mm). Set by the budget rule.
- **T1 (candidate):** `rain_60m × C`, with C = 0.9 × built-up share + 0.3 × (1 − built-up share).
  Coefficients fixed (not fitted). Same one-input rule as v4. Thresholds from the budget rule.
- **The 18 Oct result:** T1 scored 0.518 against 0.500 on 88 labels and 28 floods. It missed the
  pre-registered margin (0.550 needed) and was not adopted (`docs/mentor_tests_2026-10.md`).

## The rule

T1 replaces v4 **only if all of the following hold** on the 92-label basis (30 floods in 2020–2023,
forward-chaining out-of-fold, same folds for both models):

- **(a) Event score:** T1's event score is at least v4's + **0.05**. Same as 18 Oct.
- **(b) A clear gain in at least one tier:** the paired 90% bootstrap CI for floods caught, T1 minus
  v4, has a lower bound **above 0**. Same as 18 Oct, with one addition (below).
  - **Primary method:** resample floods, 2,000 resamples, seed 0 (as on 18 Oct).
  - **Added 25 Oct:** the same interval, resampling by **storm** (floods within one storm stay
    together), must also have a lower bound above 0. Floods from the same storm are not independent,
    so this is the stricter reading. It is a change from 18 Oct and is made after T1's 18 Oct result
    was known.
- **(c) No real loss in the other tier:** the other tier loses **at most 1 flood** to T1, counting
  floods lost (not the net change). Same as 18 Oct, after the criterion-(c) fix in the review notes.
- **Budgets unchanged:** 2.5 false High and 11 false Moderate episodes per zone-year (`config.py`).
  Thresholds for both models come from the budget rule.

**Reported, not decision criteria:**
- Brier score, PR-AUC and calibration (expected calibration error). They are shown because the app
  shows a % chance.
- **Pre-declared veto:** if T1's calibration is clearly worse than v4's, T1 is not promoted even when
  (a) to (c) hold. "Clearly worse" means its expected calibration error is above v4's on the same
  folds. This is decided now, not after the numbers are seen.
- Replay of 17 Apr 2021 and 30 Sep 2026 (how often the risk level differs). Shown to explain the
  change to the app, not to decide it.

## Outcomes

- **Promote T1** (adk signs off, then Akul registers and promotes) only if the rule above holds and the
  calibration veto does not apply.
- **Keep v4** otherwise. The result is reported as "not distinguishable, kept v4" when no criterion
  is clearly met.

## Test years

- 2024 to Sep 2026 (30 floods) is **not** part of the decision.
- It is run **only** after the decision, and only if T1 is recommended. It is labelled a
  **"second look"** (CLAUDE.md rule 2).
