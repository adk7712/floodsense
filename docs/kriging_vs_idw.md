# Kriging vs IDW: leave-one-gauge-out (plan item B3)

Mentor suggestion: does ordinary kriging predict rain at a point better than the inverse-distance
weighting (IDW, power 2) we use today?

## Method

- **Data window:** 2017-01-01 to 2023-12-31 only. 2024-2026 is the held-out test set and the code
  refuses it (`assert_training_window`). Nothing here is a "second look".
- **Storm steps:** 5-minute steps where at least one gauge read 2.0 mm or more (about 24 mm/h).
  There are 40,268 such steps out of 716,632 scanned. A seeded random 1,000 were used to fit the
  variogram and a different random 4,000 to validate.
- **Leave-one-gauge-out:** in each validation step, every gauge with a reading is hidden and
  predicted from the other reporting gauges, by IDW (the pipeline's `IDWMatrixEngine`, with the
  weights rebalanced over the gauges that reported) and by ordinary kriging. Missing readings stay
  missing; they are never treated as 0. A step needs at least 4 other gauges (all 4,000 qualified).
  Gauge positions are the ones valid at that time (a few gauges moved).
- **Kriging:** exponential variogram on haversine distance, fitted once on pooled normalised
  semivariances from the 1,000 fit steps (nugget 0.46, partial sill 0.81, practical range 32 km,
  in units of the step's variance). It beat the spherical model on fit error (0.036 against 0.049),
  which is why it was chosen. Because OK weights do not depend on the overall variance scale, the
  validation steps never influenced the variogram. Negative predictions are clipped to 0. Where a
  field was constant, the constant is returned: 213 of 242,714 predictions (0.09%). No singular
  systems.
- **Groups:** by what the held-out gauge actually read, as a mm/h equivalent (5-minute total x 12):
  light is under 10, heavy is 10 or more. "Very heavy" (30 or more) is shown as extra detail.
  Errors are in mm/h-equivalent.
- **Uncertainty:** 90% percentile bootstrap (2,000 resamples) of the RMSE difference, kriging
  minus IDW. Negative means kriging is better. The main interval resamples time steps. Steps in
  one storm are correlated, so a second interval resampling whole days is also shown.

## Results

| Group of held-out gauge | n (gauge-steps) | RMSE IDW | RMSE kriging | Bias IDW | Bias kriging | Kriging minus IDW, 90% CI (steps) | 90% CI (days) |
|---|---|---|---|---|---|---|---|
| Light (<10 mm/h) | 216,659 | 4.40 | 4.63 | +2.16 | +1.95 | +0.22 (0.19 to 0.26) | 0.18 to 0.27 |
| Heavy (10 mm/h or more) | 26,055 | 27.31 | 26.40 | -17.35 | -15.98 | -0.91 (-0.98 to -0.85) | -0.98 to -0.84 |
| Very heavy (30 mm/h or more) | 11,066 | 39.72 | 38.28 | -32.86 | -31.01 | -1.44 (-1.54 to -1.34) | -1.55 to -1.34 |
| All | 242,714 | 9.87 | 9.69 | +0.07 | +0.03 | -0.18 (-0.20 to -0.16) | -0.20 to -0.15 |

4,000 time steps, 1,352 distinct days.

## What it shows

- For heavy rain, kriging's error is lower and the interval excludes 0. That is a 3% RMSE
  reduction (0.9 of 27.3 mm/h). It is small next to the error itself.
- For light rain, kriging is slightly worse (about 5%, interval also excludes 0).
- Both methods badly under-predict heavy cells (bias about -16 to -17 mm/h): a hidden gauge
  inside a convective core is rarely matched by its neighbours. Neither method fixes that.
- Limits: storm steps were picked by a gauge maximum (so light-rain rows are the quiet gauges
  during storms, not dry days); one fixed variogram for all steps; error is at gauge points, not
  zone averages, and was not tested against flood labels or the model's scores.

## Decision

Kriging is about 3% better on heavy rain (RMSE −0.91 mm/h, 90% CI −0.98 to −0.85) and about 5%
worse on light rain (+0.22 mm/h, 90% CI +0.19 to +0.26). Both methods badly under-predict heavy
cells (bias about −16 to −17 mm/h).

The pre-set rule was that kriging would be adopted if the 90% interval of the heavy-rain RMSE
difference excluded 0 in kriging's favour. It does exclude 0, so the rule is met, but the gain is
small and, in this 16 Oct analysis, it was not tested on the model or against flood labels (that was
done later, on 18 Oct: see the B7 paragraph below). The team judged the gain too small to switch.

**IDW stays in serving. Kriging is not wired into the pipeline.**

**Scored on floods (18 Oct, B7, T2):** a rule on the 60-minute kriged mean + 1 kriging SD was
tested on the 2020–2023 cross-validation (`docs/mentor_tests_2026-10.md`). Event score 0.446
against 0.500 for `rule_rain60`; High 14/28 (2 gained, 2 lost); Moderate 21/28 against 18/28 but at
8.50 against 4.52 false episodes per zone-year. Not adopted. Any change is the team's call
(no code freeze since 18 Oct).

## Reproduce

```bash
.venv/bin/python -m floodsense.spatial.compare_interpolation   # about 20 s
```

Writes `docs/results/kriging_vs_idw.json` (committed) with the full numbers, seed 42.
Code: `src/floodsense/spatial/kriging.py`, `src/floodsense/spatial/compare_interpolation.py`;
tests: `tests/test_kriging.py`.
