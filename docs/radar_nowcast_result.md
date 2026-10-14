# Radar nowcast: skill against persistence (plan A1.4)

**Question:** does radar tell us how much rain each planning area will get over the next 30 and
60 minutes, better than assuming nothing changes? This decides whether radar feeds a "forecast risk"
layer (plan A1.6).

**Method** (`python -m floodsense.radar.evaluate_nowcast`):
- **Data:** the archived 70 km radar frames, 16 Jul to 30 Sep 2026, converted to rain rates with
  the gauge-calibrated scale (`docs/radar_calibration.md`).
- **Nowcast:** motion from the last 3 frames (optical flow), with the current rain advected forward
  in 5-minute steps (`floodsense.radar.nowcast`). Rain moves but does not grow or decay.
- **Truth:** each zone's gauge-interpolated rain over the next 30/60 minutes. That's the feature
  store's `rain_30m`/`rain_60m`, the quantity the flood model uses. **No flood labels are used**, so
  the 2024–2026 test set is untouched.
- **Storm steps:** some zone has ≥5 mm in the truth window, or ≥5 mm/h on radar.
- **Scope:** 1,311 steps on 43 storm days, 39,339 zone × step pairs. Every method is scored on the
  same pairs.
- **Thresholds:** the flood model's tiers in rain terms. Moderate starts at 29.5 mm and High at
  44.5 mm of rain in 60 minutes.

**Results**

| | Nowcast | Radar persistence (radar held still) | Gauge persistence (last hour repeats) | Radar observed (upper bound) |
|---|---|---|---|---|
| 30 min: correlation | **0.70** | 0.65 | 0.51 | 0.88 |
| 30 min: CSI, ≥15 mm | **0.34** | 0.32 | 0.19 | 0.53 |
| 60 min: correlation | **0.58** | 0.51 | 0.21 | 0.90 |
| 60 min: CSI, ≥29.5 mm (Moderate) | **0.23** | 0.18 | 0.03 | 0.56 |
| 60 min: CSI, ≥44.5 mm (High) | **0.12** | 0.08 | 0.00 | 0.34 |

**CSI gain of the nowcast, with 90% bootstrap intervals (resampled by storm day):**

| | Against radar persistence | Against gauge persistence |
|---|---|---|
| ≥15 mm in 30 min | +0.024 (−0.003 to +0.047) | **+0.148 (+0.097 to +0.181)** |
| ≥29.5 mm in 60 min | +0.037 (−0.028 to +0.084) | **+0.180 (+0.020 to +0.262)** |
| ≥44.5 mm in 60 min | +0.028 (−0.023 to +0.085) | **+0.103 (+0.021 to +0.189)** |

**What this means**
- **Radar adds real foresight over gauges.** The current system only sees rain that has already
  fallen. "The last hour repeats" almost never anticipates a Moderate- or High-level hour of rain
  (CSI 0.03 and 0.00), while the radar nowcast catches a meaningful share (0.23 and 0.12). These
  gains are statistically clear.
- **Motion adds a smaller edge on top.** The nowcast beats holding the radar still on every
  measure, but with 43 storm days its CSI gain is not yet statistically conclusive. Correlation
  improves from 0.65 to 0.70 at 30 minutes, and from 0.51 to 0.58 at 60.
- **There is room to improve.** The radar's own later frames reach 0.88–0.90 correlation, so better
  motion and growth/decay modelling could close much of the gap.
- **Limitations:**
  - 3 months of data, so only 43 storm days.
  - Radar reads about 20% low in the heaviest hours (calibration).
  - The nowcast neither grows nor decays storms.

**Plan criterion:** "the nowcast beats persistence at 30 min on storm steps". It does, against both
baselines, clearly so against gauge persistence and modestly against radar persistence.
**Recommendation: GO** for the forecast-risk layer (A1.6), labelled experimental, with the honest
framing above. The decision is the team's on 19 Oct.
