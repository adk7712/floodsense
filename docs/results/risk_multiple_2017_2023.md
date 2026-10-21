# "N times the usual flood risk for a rainy hour" (T2a), 2017–2023 only

Script: `docs/results/risk_multiple_2017_2023.py`. Output: `risk_multiple_2017_2023.json`.
Test: `tests/test_risk_multiple.py`. The 2024–2026 test set was not read: the script loads only the
2017–2023 feature partitions, asserts that no row or used flood event is from 2024 onward, and
asserts that leaving out the 2024+ events changes no 2017–2023 label.

## Definition

- **Unit:** one zone × one 5-minute step that passes the active-rain gate (≥ 0.2 mm in the zone's
  last 120 minutes). These are exactly the feature-store rows. Gated-out (dry) steps are not in the
  store, and steps with no readings are absent, never counted as dry.
- **Outcome:** the training label: the chance that a verified flood starts in that zone within 60
  minutes. In-flood rows are left out (2,878), and imprecise report times count fractionally.
- **Usual risk:** the mean outcome over all gated steps, 55 zones pooled.
- **Tier risk:** the mean outcome over the steps that the served champion (`models/flood_model.joblib`,
  version 3) puts in that tier. Thresholds are read from the artifact (Moderate 0.531%, High 0.706%;
  equal to 37.65 and 43.15 mm of rain in 60 minutes). Moderate is the band [0.531%, 0.706%).
- **N** = tier risk / usual risk. N doesn't depend on whether the unit is a step or an hour.
- **Interval:** 90% percentile bootstrap that resamples whole SGT calendar days, B = 4000, seed
  20261021. A sensitivity check resamples 7-day blocks. The verification run used seed 7.

**Leakage.** `rule_rain60` has no fitted parameters and its calibrator is monotone, so a tier is
simply "rain_60m ≥ cut" in every year. The cuts were chosen on the 2020–2023 out-of-fold curve
under the false-alarm budget, so 2020–2023 is in-sample for that choice. 2017–2019 never touched the
cuts, the calibrator or the model choice, which makes it the clean check. The pooled 2017–2023
figure is the headline.

## Results (2017–2023 pooled; usual risk 290.6 flood-steps in 5,477,155 gated steps = 0.0053% per step)

| Tier | Steps (zone-hours) | Flood-steps | Floods preceded | N | 90% CI (days) | 90% CI (weeks) |
|---|---|---|---|---|---|---|
| Moderate (band) | 4,088 (341) | 6.07 | 6 | 28 | 10.6–49.4 | 10.5–50.7 |
| High | 3,921 (327) | 48.10 | 8 | 231 | 116.8–359.0 | 118.7–367.6 |
| Moderate or higher | 8,009 (667) | 54.17 | 8 | 127 | 64.8–199.2 | 65.2–205.6 |

- **Split check:**
  - 2017–2019 (clean): Moderate 28 and High 146, but each comes from only 2 floods, so the 90%
    intervals start at 0 (0–63 and 0–336).
  - 2020–2023: Moderate 31 (6.3–64.7) and High 295 (140–492).
- **Second seed (7):** High 118–361, Moderate 10.3–49.5. The point estimates match an independent
  recomputation from the mm cuts.
- **Analyst view:**
  - Moderate: the calibrated probability is 0.531–0.706%; the mean calibrated probability in the
    band is 0.61%, against an observed 0.15% per step.
  - High: ≥ 0.706%; the mean calibrated probability is 0.99%, against an observed 1.23% per step.

## Caveats

- **Few independent storms.** Every High figure rests on **8 floods on 8 days**, and Moderate on 6
  floods (the same storms: rain passes 37.65 mm on its way to 43.15 mm). A percentile bootstrap with
  this few clusters probably understates the uncertainty.
- **Day-only reports barely count.** 21 of the 58 floods in 2017–2023 are day-only reports, and
  under the label policy they add almost nothing to the outcome. 42 floods have any positive step.
  N is therefore really about floods with a timed report.
- **The baseline includes drizzle.** The base counts any 0.2 mm in two hours as "rainy", which is
  why N runs into the hundreds. A resident who pictures a "rainy hour" as a downpour would read the
  number as far larger than intended.
- **The baseline is pooled.** It covers all 55 zones, but the 88 verified floods (all years) fall in only 33
  planning areas. "For a rainy hour **here**" would claim a zone-specific base rate that wasn't computed.
- **Floods are under-reported** (only reported floods count). Both rates are lower bounds, so the
  direction of the bias in N is unknown.

## Recommendation

**Don't show "about N times"** for either tier. By the display rule fixed before the run, neither
interval is narrow enough (the High interval spans a factor of about 3, Moderate about 5). The rule
would allow "at least 10 times" (Moderate) and "at least 100 times" (High), but the clean
2017–2019 check can't rule out 0, and the large figures depend on the drizzle baseline. Prefer the
absolute calibrated chance, which is already validated. If the team wants a comparison anyway, the
most defensible form is High only: "Floods have been far more likely at this level (in past storms
in Singapore, over 100 times the risk of an ordinary rainy hour; based on 8 floods)", without
"here". This is a team decision.
