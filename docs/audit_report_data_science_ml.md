# Independent Data Science & ML Systems Audit Report: FloodSense

**Audit Date:** 6 October 2026  
**Auditor:** Independent Data Science and ML Systems Auditor  
**Target System:** FloodSense (Singapore URA Planning Area Flash-Flood Early Warning System)  
**Codebase Evaluated:** `src/floodsense/`, `models/`, `data/`, `databricks/`, `docs/`, `submission/`  
**Git Commit Audited:** `da757d9`

---

## Executive Summary

FloodSense is an end-to-end flash-flood risk intelligence system designed for Singapore's 55 URA Planning Areas under the DAISI Challenge 2026 (Track B2). The system incorporates automated 5-minute NEA weather station ingestion, inverse-distance-weighted spatial interpolation, temporal feature engineering, Platt-calibrated early warning scoring, a Streamlit operational dashboard, and a Databricks Lakeflow declarative pipeline.

This audit was conducted to critically examine the mathematical, statistical, hydrological, and data engineering foundations of FloodSense. While the project exhibits exemplary software engineering rigor—including 100% offline unit/integration test coverage, defensive schema validation, zero data fabrication, and verified Databricks Free Edition Lakeflow parity—**the audit identified fundamental statistical vulnerabilities, methodological edge cases, evaluation leaks, and regulatory compliance gaps with respect to the official DAISI Track B2 requirements.**

### Critical Audit Findings
1. **Model Reality:** The deployed "Machine Learning" model (`models/flood_model.joblib`) is not a learned multi-variate model. It is an unparameterized, single-feature 60-minute rainfall rule ($x / (x + 25\text{ mm})$) mapped via 1D Platt logistic regression directly to fixed rainfall thresholds ($\approx 29.4\text{ mm/h}$ for Moderate and $\approx 44.0\text{ mm/h}$ for High). All other engineered features (72-hour decay, storm rarity, short-interval bursts) are completely unutilized by the deployed inference engine.
2. **Evaluation & Temporal Causality Flaws:** 
   - A reported test "hit" (`2024-11-22-sembawang-woodlands-ave-10`) fired at 15:20 SGT with an official recorded lead time of **-200.5 minutes** (over 3 hours *after* the event midpoint), yet was counted as a successful early-warning hit (`hit: true`).
   - For exact-timestamp events, lead times collapse to a median of 7.5 minutes (with 4 of 12 test hits having $\le 5$ minutes lead time). The 37.5-minute lead time reported during cross-validation was an artifact of imprecise event windows in historical news reports.
3. **Spatial IDW Blind Spots:** IDW is globally formulated across all stations on the island without a spatial cutoff radius. If all gauges near a planning area go offline during an intense localized cloudburst, the system dynamically rebalances onto stations 30–45 km away, either hallucinating "phantom rain" or completely missing catastrophic floods. If all island stations fail, the system silently predicts 0.0 mm rain and 0.0% flood risk (silent fail-open).
4. **Live vs. Training Feature Skew:** In the training feature store, the 72-hour wet-ground exponential decay incorporates a 72-hour warmup. In live production serving, the API poller only fetches 6.0 hours of history due to rate limits, causing a 66-hour antecedent moisture truncation.
5. **Missing Track B2 Mandatory Requirements:** 
   - The 4th required dataset, **Flood Prone Areas (Annual, 2022–2025)**, is **completely missing** from the repository data directory and pipeline.
   - There is **no 3-year time-series trend analysis** of flood-prone areas as mandated by the DAISI Track B2 Participant Guide.
   - The Historical Rainfall Collections (2016–2024) omit the year **2016** entirely.

---

## 1. Model & Label Rigor Audit

### 1.1 Deployed Architecture & Estimator Decomposition
Inspection of `models/flood_model.joblib` and `src/floodsense/models/training.py` reveals the following model architecture:
- **Candidate Selected:** `rule_rain60`
- **Estimator Class:** `floodsense.models.training.RuleModel`
- **Internal Configuration:** `column_index: 3` (`rain_60m`), `scale_mm: 25.0`
- **Scoring Function:**
  $$s(x) = \frac{x}{x + 25.0}, \quad x = \text{rain\_60m} \ge 0$$
- **Calibration Engine:** Platt Scaling (`Calibrator(method='platt')`, fitted via `LogisticRegression(C=1e6)`)
- **Fitted Coefficients:** $\beta_1 = 2.13511105$, $\beta_0 = -6.15524398$

#### Mathematical Equivalence:
Because $s(x)$ is a strictly monotonic transform of $x$, computing the logit yields:
$$\text{logit}(s(x)) = \ln\left(\frac{s(x)}{1 - s(x)}\right) = \ln\left(\frac{x / (x+25)}{25 / (x+25)}\right) = \ln(x) - \ln(25)$$
The Platt-calibrated probability is therefore:
$$p(x) = \sigma\left(\beta_1 \cdot \ln(x) - (\beta_1 \ln(25) - \beta_0)\right) = \frac{1}{1 + \exp\left(-(2.13511 \ln(x) - 13.02787)\right)}$$

This is a deterministic 1-dimensional logistic function of $\ln(\text{rain\_60m})$. 
**Finding:** Despite engineering 7 complex features (`rain_5m`, `rain_15m`, `rain_30m`, `rain_60m`, `rain_120m`, `rain_decay_72h`, `storm_rarity_score`), the deployed system is fundamentally a 1-parameter rainfall depth thresholding rule.

### 1.2 Failure of Complex ML Candidates (LightGBM & Logistic Regression)
In the cross-validation selection (`models/model_card.json`), the four candidates achieved the following event-level scores (mean hit rate at 1.0, 2.0, 5.0, 10.0 false episodes per zone-year):
- `rule_rain60`: **0.4891** (48.9%)
- `rule_rain30`: **0.4565** (45.7%)
- `logistic`: **0.1522** (15.2%)
- `lightgbm`: **0.0870** (8.7%)

#### Root-Cause Analysis:
Why did LightGBM fail catastrophically, catching only 2 of 23 validation events?
1. **Severe Extreme-Class Weighting Distortion:** In `src/floodsense/models/training.py`, function `_balanced(y, w)` reweights positive samples so that both classes carry equal total weight:
   $$w_{\text{pos}} = w_{\text{raw}} \times \frac{N_{\text{neg}}}{N_{\text{pos}}} \approx 1.0 \times \frac{3,305,580}{96} \approx 34,433$$
   Upweighting 96 positive rows across 4 years by a factor of $34,400\times$ in a 7-dimensional feature space forces tree splitting algorithms to create hyper-sensitive axis-aligned partitions around specific training storms.
2. **False Alarm Explosion:** High-dimensional decision boundaries caused false alarm spikes across dry or moderately wet days in other zones, blowing the false-alarm budget. To meet the stringent constraint of $\le 2.5$ false episodes per zone-year, LightGBM's threshold had to be raised so high that almost all real events were missed.
3. **Physical Hydrology vs. Tree Partitions:** In Singapore's urban drainage network, flash flooding is predominantly governed by whether stormwater runoff exceeds local canal discharge capacity over a 45–60 minute concentration time ($\approx 30\text{--}45\text{ mm/h}$). A 1D cumulative rainfall rule naturally matches the physical concentration time of Singapore catchments, whereas multi-tree gradient boosting overfit the sparse sample of 96 positive rows.

### 1.3 Probability Calibration, Brier Score & Confidence Intervals
- **Calibration Method Selection:** `config.py` sets `calibration_method = 'auto'` with `isotonic_min_positives = 200`. Because out-of-fold cross-validation contained only 96 positive rows, the system automatically fell back to Platt scaling.
- **Brier Score Distortion:** The Brier score is reported as $2.90 \times 10^{-5}$ in development and $0.000140$ in held-out test. 
  - *Vulnerability:* The base prevalence of floods in the test set is $294 / 2,091,174 = 0.0001406$.
  - A completely uninformative dummy model that constantly predicts $\hat{p} = 0.0$ yields a Brier score of exactly $0.0001406$.
  - The Brier score is dominated by true negatives ($>99.98\%$ of the dataset) and provides virtually zero evidence of calibration in the critical tail.
- **Expected Calibration Error (ECE) Artifact:** ECE is reported as $5.50 \times 10^{-5}$. In `reliability_table()` (`calibration.py`), 10 equal bins are created between 0.0 and 1.0 ($[0.0, 0.1], [0.1, 0.2], \dots$). Because $>99.99\%$ of all observations have $p < 0.01$, virtually all data falls into Bin 0. The ECE simply measures the global sample bias rather than evaluating tail reliability.
- **Derivation of the 0.30% and 0.71% Thresholds:**
  The operational thresholds are not calibrated probabilities of true flood risk in a Bayesian sense. They are the exact operating points on the 41-point logarithmic grid where empirical false-alarm rates satisfy administrative budgets:
  - **Moderate Budget:** $\le 11.0$ false episodes/zone-year $\implies$ Grid point $p = 0.00300$ ($10.55$ false eps/yr, $73.9\%$ CV hit rate).
  - **High Budget:** $\le 2.5$ false episodes/zone-year $\implies$ Grid point $p = 0.00706$ ($2.32$ false eps/yr, $52.2\%$ CV hit rate).
  - **Rainfall Inversion:** Solving the calibrated model for $x$:
    $$x_{\text{Moderate}} = 25 \times \exp\left(\frac{\text{logit}(0.003) - (-6.1552)}{2.1351}\right) = \mathbf{29.44\text{ mm in 60 min}}$$
    $$x_{\text{High}} = 25 \times \exp\left(\frac{\text{logit}(0.00706) - (-6.1552)}{2.1351}\right) = \mathbf{44.04\text{ mm in 60 min}}$$
- **Confidence Intervals:** 
  The 90% bootstrap confidence interval on the High alert hit rate is **[26.7%, 53.3%]** around a point estimate of 40.0% (12/30 events). With only 30 test events, the standard error is $\pm 8.9\%$, reflecting wide statistical uncertainty that evaluators will scrutinize.

### 1.4 Temporal Data Leakage & Event Evaluation Integrity
The code enforces a strict temporal split: training on $\le 2023$, and scoring once on $\ge 2024$. Forward-chaining annual CV strictly prevents future fold data from leaking into model weights or rarity quantiles.

**However, a severe causality flaw was discovered in `src/floodsense/models/evaluation.py`:**
Lines 152–161 define an event "hit":
```python
window_lo, window_hi = w.start_lo - horizon_td, w.start_hi
timely = eps[(eps["end"] >= window_lo) & (eps["start"] < window_hi)]
```
1. **The Negative Lead Time Flaw:** For events with `day_only` precision, `start_lo = 00:00:00` and `start_hi = 23:59:00`.
   - In `models/final_report.json`, event `2024-11-22-sembawang-woodlands-ave-10` is recorded as a **HIT** (`hit: true`) with:
     - `first_alert: 2024-11-22T15:20:00+08:00`
     - `lead_minutes: -200.5`
   - The alert fired at 3:20 PM, but the reported start midpoint was 12:00 PM. **The alert was late by 3 hours and 20 minutes, yet was marked as an early warning hit!**
2. **Operational Lead Time Reality:** In the test set (30 floods, 12 High hits):
   - 1 hit was late by -200.5 minutes.
   - 1 hit had 0.0 minutes warning (`2024-12-29-bukit-timah-dunearn-rd`).
   - 4 hits had $\le 5$ minutes warning.
   - **Only 5 of 30 test floods (16.7%) received an actionable early warning $\ge 15$ minutes.**
3. **The Validation vs. Test Lead Time Collapse:**
   - Validation (2020–2023) median lead time: **37.5 minutes**.
   - Held-out test (2024–2026) median lead time: **7.5 minutes**.
   - *Cause:* In 2020–2023, events were culled from retrospective news articles where time precision was overwhelmingly coarse (`approx_hour` or `day_only`), artificially inflating lead times. When evaluated against exact-minute PUB Telegram alerts in 2024–2026, the apparent lead time collapsed by 80%.

---

## 2. Spatial & Feature Engineering Pipeline Audit

### 2.1 IDW Spatial Interpolation & Station Dropout Failures
In `src/floodsense/spatial/idw_matrix.py`:
- Algorithm: Base IDW with $p=2.0$ from all weather stations to planning area centroids:
  $$w_{ij} = \frac{d(s_i, z_j)^{-2}}{\sum_{k=1}^K d(s_k, z_j)^{-2}}$$
- Dynamic re-weighting when station set $\mathcal{A}$ reports:
  $$\tilde{w}_{ij} = \frac{w_{ij} \cdot \mathbf{1}_{\{s_i \in \mathcal{A}\}}}{\sum_{k \in \mathcal{A}} w_{kj}}$$

#### Flaws Identified:
1. **Absence of Spatial Radius of Influence ($R_{\max}$):**
   - The formulation is globally unconstrained across the entire island of Singapore ($\sim 50\text{ km}$ wide).
   - In tropical meteorology, convective thunderstorm cells have spatial correlation scales of 2–5 km.
   - **Scenario 1 (Blind Spot):** If severe rain knocks out the 2 rainfall stations near Bukit Timah, the system renormalizes over distant stations in Changi and Jurong. If those stations are dry (0 mm), Bukit Timah is assigned 0.0 mm rain despite severe local flooding.
   - **Scenario 2 (Phantom Rain):** If a local squall dumps 60 mm in Changi while Western Catchment stations are offline, Western Catchment will be assigned non-zero interpolated rainfall from 40 km away, triggering false alarms.
2. **Total Station Failure (Fail-Open Vulnerability):**
   - Lines 94–98 in `idw_matrix.py`:
     ```python
     zero_mask = col_sums == 0.0
     col_sums[zero_mask] = 1.0
     rebalanced = rebalanced / col_sums
     ```
   - If an upstream NEA API outage occurs and 0 stations report, `rebalanced` evaluates to all zeros. The system outputs `rainfall_mm = 0.0`, resulting in a `Low` risk prediction (0.0% probability).
   - The system **fails silently open** instead of raising a critical telemetry degradation alarm.
3. **Centroid Discretization Error:**
   - Large planning areas (e.g., Western Catchment at $69\text{ km}^2$, Changi at $40\text{ km}^2$, Tuas at $30\text{ km}^2$) span over 10 km.
   - Assigning a single centroid `representative_point()` fails to capture sharp precipitation gradients across large zones.

### 2.2 72-Hour Antecedent Moisture & Feature Pipeline Edge Cases
In `src/floodsense/features/feature_pipeline.py`:
- Wet-ground decay is implemented via an IIR filter:
  $$y[n] = x[n] + \alpha y[n-1], \quad \alpha = \exp\left(-\frac{\ln(2)}{288}\right) \approx 0.997596$$
- **IIR vs. FIR:** The filter has an infinite impulse response; rainfall from 5 days prior still retains $3.1\%$ of its weight.
- **Production Serving Disconnect:**
  - Feature store generation uses `WARMUP = timedelta(hours=72)`.
  - In `src/floodsense/app/streamlit_app.py` line 446:
    `snapshots = NEAPoller().fetch_history(settings.live_history_hours)` where `settings.live_history_hours = 6.0`.
  - In live mode, only 6 hours of historical readings are fetched to avoid API pagination limits.
  - The live wet-ground decay feature is truncated by 66 hours and systematically understated compared to training data.

### 2.3 Storm Rarity Engine Anomalies
In `StormRarityEstimator.score_array()`:
1. **Discontinuity at 0.1 mm:**
   - If $r \le 0.1\text{ mm}$, score = 0.0 and return period = 0.0 years.
   - If $r = 0.1001\text{ mm}$, score jumps to $\approx 0.0033$ and return period jumps discontinuously to **0.1 years (36.5 days)**.
2. **Hydrological Validity:**
   - Return periods are mapped to heuristic anchors $[0.2, 0.5, 1.0, 2.0, 5.0, 10.0]$ years.
   - These are not fitted Extreme Value Distributions (e.g., GEV / Gumbel / Generalized Pareto via Peaks-Over-Threshold). This heuristic mapping cannot be defended hydrologically in an academic or technical judging panel.

---

## 3. Local vs. Databricks Parity Audit

### 3.1 Pipeline Architecture & Spark Execution Pattern
In `databricks/pipelines/lakeflow_pipeline.py`:
```python
window.withColumn("batch", F.lit(0)).groupBy("batch").applyInPandas(score, GOLD_SCHEMA)
```
- **Architectural Trade-off:** By forcing all 96 hours of readings across 70 stations into a single Spark partition (`batch = 0`), Databricks executes the exact Python/pandas scoring function (`pipeline_core.gold_from_silver`) on a single executor.
- **Parity vs. Scalability:** This eliminates numerical divergence between PySpark SQL and pandas, guaranteeing exact replay match. However, it completely bypasses Spark's distributed architecture, creating an executor memory bottleneck if the historical window expands.

### 3.2 The 0.2 mm Active-Rain Gate Vulnerability
In `src/floodsense/models/scoring.py` line 47:
```python
active = features["rain_120m"].to_numpy(dtype=float) >= settings.active_rain_min_mm_120m
```
- **Floating-Point Non-Determinism:**
  Accumulating 24 five-minute floating-point readings produces micro-variations around $0.2000000000\text{ mm}$ between Apple Silicon ARM64 (macOS) and Intel/AMD x86_64 (Linux Databricks runtime).
  - In the 17 Apr 2021 replay (4,675 rows), 1 row (Geylang 11:25) landed at $0.19999999999999998$ on Databricks (score 0.0) vs. $0.20000000000000004$ locally (score $7.1 \times 10^{-8}$).
  - In `tests/test_phase5_parity.py`, a special tolerance mask `_on_gate` had to be introduced to pass CI.
- **Impact on Model Training:**
  - In `DEPLOYMENT.md` Section 8: shifting the gate by $10^{-9}$ changes the training set by **9,982 rows** (0.13%).
  - Because `rule_rain60` beat `rule_rain30` by only 0.032 in event score (a margin of $\sim 1$ validation flood), training data sensitivity to floating-point gate noise represents an unquantified model selection risk.

---

## 4. Alignment with DAISI Track B2 Requirements

| Required Dataset | Status in Codebase | Compliance & Audit Finding |
|---|---|---|
| **1. Rainfall across Singapore (Real-time API)** | **COMPLIANT** | Ingested via data.gov.sg v2 API (`NEAPoller` in `poller.py`); handles pagination, retries, and validation. |
| **2. Historical Rainfall Collections (2016–2024)** | **NON-COMPLIANT (PARTIAL)** | Store covers 2017–2024 (Collection 2279). **The year 2016 is completely missing** from `data/raw/rainfall/readings/`. |
| **3. Flood Alerts across Singapore (Real-time API)** | **DEVIATION / PROXY** | No official data.gov.sg real-time flood alert API exists. The project built a high-quality manual ground-truth dataset (`flood_events.csv`, 66 events) from Telegram and news. |
| **4. Flood Prone Areas (Annual, 2022–2025)** | **CRITICAL DEFICIENCY: MISSING** | **Completely absent from the repository.** No dataset ingested, no GIS layer created, and **no 3-year time-series trend analysis** implemented. |

### 4.1 Detailed Breakdown of the Flood Prone Areas Deficiency
The DAISI Track B2 Participant Guide explicitly requires analyzing the annual Flood Prone Areas dataset (2022–2025) to evaluate climate resilience and drainage improvements over a multi-year horizon.
- In the FloodSense repository:
  - `data/` contains zero files relating to annual flood-prone areas.
  - `streamlit_app.py` and `round1_pitch_deck.md` contain only a single static qualitative text bullet:
    *"- Flood-prone land: about 3,200 ha in the 1970s, under 25 ha by 2025 (MSE, 4 Feb 2025)"*
  - The internal project mission prompt (`docs/agent-mission-prompt.md`) explicitly instructed:
    *"- 3-year historical flood-prone trend chart (PUB 2022–2025 hectarage)."*
  - **This was omitted from the implementation.** Judges reviewing against the official Track B2 dataset rubric will penalize this omission.

---

## 5. Actionable Roadmap to Unassailable Methodology

To elevate FloodSense from a hackathon prototype to an unassailable scientific submission, the following five architectural remediations must be executed:

```
┌────────────────────────────────────────────────────────────────────────┐
│               RECOMMENDED ARCHITECTURAL ENHANCEMENTS                   │
├──────────────────────────────┬─────────────────────────────────────────┤
│ 1. Causality & Evaluation    │ • Disallow negative lead times as hits  │
│    Integrity                 │ • Enforce 15-min minimum actionable lead│
│                              │ • Separate exact vs coarse event tiers  │
├──────────────────────────────┼─────────────────────────────────────────┤
│ 2. Spatial Telemetry & IDW   │ • Impose 10-km local search radius      │
│    Safeguards                │ • Fail-closed with Telemetry Alarm      │
│                              │ • Flag spatial uncertainty per zone     │
├──────────────────────────────┼─────────────────────────────────────────┤
│ 3. True Hydrological Return  │ • Fit GEV/GPD (Peaks-Over-Threshold)    │
│    Periods                   │ • Smooth 0.1 mm discontinuity           │
│                              │ • Train multi-feature hazard model      │
├──────────────────────────────┼─────────────────────────────────────────┤
│ 4. Deterministic Active-Rain │ • Replace hard cutoff with smooth gate  │
│    Gate                      │ • Round rain_120m to 2 decimals first   │
├──────────────────────────────┼─────────────────────────────────────────┤
│ 5. Track B2 Compliance       │ • Ingest 2022-2025 Flood Prone GIS data │
│    & Trend Analysis          │ • Compute annual hectarage per URA zone │
│                              │ • Backfill 2016 historical rainfall     │
└──────────────────────────────┴─────────────────────────────────────────┘
```

### 5.1 Step 1: Enforce Strict Causality in Event Evaluation
Modify `src/floodsense/models/evaluation.py`:
1. Reject negative lead times from hit attribution:
   ```python
   # Only alerts issued strictly BEFORE reported flood start qualify as true warnings
   lead_minutes = (reported_start - first["start"]) / pd.Timedelta(minutes=1)
   is_hit = timely and (lead_minutes >= 0.0)
   ```
2. Establish a dual reporting standard:
   - **Operational Early Warning Rate:** Hits with lead time $\ge 15\text{ minutes}$.
   - **Detection Rate:** Hits with lead time $\ge 0\text{ minutes}$.
3. Segregate evaluation into two tiers: (a) Exact Telegram ground truth (2024–2026, $N=26$), and (b) Imprecise news archive (2017–2023, $N=36$).

### 5.2 Step 2: Spatial IDW Radius Cutoff & Telemetry Failsafe
Modify `src/floodsense/spatial/idw_matrix.py`:
1. Apply a maximum distance cutoff $R_{\max} = 10.0\text{ km}$:
   $$w_{ij} = \begin{cases} d(s_i, z_j)^{-2} & \text{if } d(s_i, z_j) \le 10\text{ km} \\ 0 & \text{otherwise} \end{cases}$$
2. Implement explicit spatial degradation metrics in `ZoneRainfall`:
   - `nearest_station_km`: Distance to the closest reporting station.
   - `local_stations_reporting`: Count of reporting stations within 10 km.
   - If `local_stations_reporting == 0`: set `rainfall_quality = "DEGRADED"` and warn operators rather than borrowing distant rainfall.
3. Fail-closed: if island-wide reporting stations drop below 10, raise an alert on the UI: `CRITICAL TELEMETRY LOSS: LIVE SENSING SUSPENDED`.

### 5.3 Step 3: Hydrologically Sound Extreme Value Modeling
1. Replace empirical percentile interpolation with **Generalized Pareto Distribution (GPD)** fitted via Peaks-Over-Threshold (POT) on 30-minute and 60-minute rainfall bursts.
2. Smooth the rarity curve around $0.1\text{ mm}$ using a logistic transition:
   $$\text{weight} = \sigma\left(\frac{r - 0.1}{0.02}\right)$$
   eliminating the discontinuous jump to 0.1 years.

### 5.4 Step 4: Deterministic Active-Rain Gate
In both `build_features.py` and `scoring.py`, eliminate floating-point platform discrepancies by rounding to 2 decimal places before comparison:
```python
active = (
    np.round(features["rain_120m"].to_numpy(dtype=float), 2) >= settings.active_rain_min_mm_120m
)
```
This guarantees identical evaluation across ARM64, x86_64, macOS, and Linux.

### 5.5 Step 5: Implement 3-Year Flood Prone Area Trend Analysis (Track B2)
1. Ingest PUB's annual Flood Prone Area GeoJSON / Shapefiles for 2022, 2023, 2024, and 2025.
2. Compute spatial intersection with URA Planning Areas using Shapely:
   $$\text{Hectarage}(z, \text{year}) = \frac{\text{Area}(\text{Polygon}_z \cap \text{FloodProne}_{\text{year}})}{10,000}$$
3. Add a dedicated **3-Year Trend Analysis** tab in `src/floodsense/app/streamlit_app.py`:
   - Stacked area chart showing island-wide and per-zone flood-prone land reduction from 2022 to 2025.
   - Identification of zones that have achieved 100% alleviation vs. chronic flood-prone zones (e.g., Bukit Timah, Dunearn Road corridor).
4. Backfill the 2016 historical rainfall records into `data/raw/rainfall/readings/year=2016/` to ensure full compliance with the 2016–2024 requirement.

---

## 6. Conclusion

FloodSense demonstrates outstanding software engineering, reproducible Databricks Lakeflow execution, and admirable honesty in admitting that a transparent 60-minute rainfall rule beat complex gradient boosted trees. However, its claims of providing 7.5 to 37.5 minutes of early warning are undermined by negative lead times in imprecise event labels, while its spatial interpolation and live serving pipelines suffer from radius-free distortion and antecedent moisture truncation. Addressing the missing Flood Prone Areas (2022–2025) dataset and implementing the causal evaluation and spatial failsafe recommendations above will make FloodSense's methodology scientifically unassailable before the DAISI Challenge jury.
