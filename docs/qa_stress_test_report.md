# FloodSense UI/UX and Streamlit Quality Assurance Stress Test Report

**Date:** 06 October 2026
**Auditor:** Independent UI/UX & Streamlit QA Engineering Agent
**Target Environment:** FloodSense Streamlit App (`http://localhost:8501`)
**Methodology:** Automated End-to-End Playwright Browser Automation, DOM Tree Inspection, Network Failure Emulation, Multi-Viewport Rendering, and Static AST/Code Analysis.

---

## 1. Executive Summary

A comprehensive, unsparing stress test of the FloodSense application was performed across cold start states, network failures, mode transitions, 6 storm presets, 85 slider steps across 7 historical hours, 12 planning areas, edge dates, and 3 distinct viewport form factors (Desktop 1440x900, Laptop/Tablet 1024x768, Mobile 390x844).

### Key Test Metrics
| Test Category | Scope / Steps | Result | Latency (Avg / P95) |
|---|---|---|---|
| **Cold Start (Live API Offline)** | Fresh load against offline/rate-limited endpoint | **FAILED UX** (Raw exception trace, screen halts at `st.stop()`) | 2.60s |
| **Mode Switch (Live -> Replay)** | Sidebar segmented control transition | **PASSED** (Instant transition, zero state loss) | 3.04s |
| **Preset Storm Buttons** | 6 Featured storms (2018 to 2025) | **PASSED** (Accurate date & default peak alignment) | 2.53s / 2.54s |
| **Slider Scrubbing (17 Apr 2021)** | 85 steps (11:00 to 18:00 SGT, 5-min intervals) | **PASSED PERFORMANCE** (Zero blank flickers) | **0.200s / 0.221s** |
| **Zone Diagnostics Cycling** | 12 planning areas (high, moderate, 0-rain) | **PASSED INTEGRITY** (No NaN, no `inf`, no div-by-zero) | 1.57s / 2.34s |
| **Edge-Case Dates** | Leap year, 2017 boundary, dry day, out-of-range | **PASSED** (Handled without app crash) | 2.52s |
| **Responsive 1440x900 (Desktop)** | Full page width layout | **MINOR GLITCH** (Card footer text truncation) | Clean |
| **Responsive 1024x768 (Tablet)** | Medium viewport layout | **HIGH SEVERITY VISUAL BUG** (KPI metric truncation) | Broken cards |
| **Responsive 390x844 (Mobile)** | Small phone layout | **CRITICAL USABILITY BUG** (Sidebar covers screen) | Broken viewport |

---

## 2. Unsparing Vulnerability & Bug Catalog

### Bug #1: Raw Traceback & Dead-End Halting on Live Feed Cold Start
- **Severity:** Critical (P0) — Demo Blocker
- **Reproduction:** Load `http://localhost:8501` when `api-open.data.gov.sg` rate-limits or is offline without an API key.
- **Observed Behavior:**
  1. The app displays an unformatted raw Python exception traceback in red:
     `cloud_off Live feed unavailable: https://api-open.data.gov.sg/v2/real-time/api/rainfall: HTTPSConnectionPool(host='api-open.data.gov.sg', port=443): Max retries exceeded with url: /v2/real-time/api/rainfall?date=2026-10-06 (Caused by NameResolutionError("HTTPSConnection(host='api-open.data.gov.sg', port=443): Failed to resolve 'api-open.data.gov.sg' ([Errno 8] nodename nor servname provided, or not known)"))`
  2. The script immediately executes `st.stop()`.
  3. The entire main canvas below the error card remains a blank white void. No KPI metrics, no map, no diagnostic details render.
  4. The only action button rendered in the main area is **"Retry now"** (`st.button("Retry now")`), which re-triggers the exact same failing API call.
  5. The informational message says *"Switch to Replay Storm, or retry"*, but **there is no clickable button to switch to Replay Storm in the main view**. The user must know to open the sidebar and click the segmented control.
- **Screenshot Evidence:** `tests/screenshots/qa_task1_cold_start.png`
- **Root Cause:**
  ```python
  # src/floodsense/app/streamlit_app.py (lines 481-494)
  if mode == "Live Feed":
      features, total_stations, live_error = live_features()
      if features is None:
          st.title("FloodSense Intelligence Center")
          st.error(f"Live feed unavailable: {live_error}", icon=":material/cloud_off:")
          st.info(
              "No data is shown rather than substituting simulated rain. Switch to **Replay Storm**, "
              "or retry. Setting `FLOODSENSE_DATA_GOV_API_KEY` avoids anonymous rate limits."
          )
          if st.button("Retry now"):
              live_features.clear()
              st.rerun()
          st.stop()
  ```
- **Remediation:**
  1. Catch and sanitize raw network exceptions into human-readable messages (e.g., *"Unable to reach data.gov.sg (Network unreachable or rate limit reached)"*).
  2. Add an instant fallback CTA directly next to Retry:
     ```python
     col_retry, col_fallback = st.columns(2)
     with col_retry:
         if st.button("Retry Live Feed", use_container_width=True):
             live_features.clear()
             st.rerun()
     with col_fallback:
         if st.button("⟳ Switch to Replay Storm", type="primary", use_container_width=True):
             st.session_state["mode"] = "Replay Storm"
             st.rerun()
     ```

---

### Bug #2: Severe KPI Card Truncation & Metric Mangling at 1024x768 (Laptop / Tablet)
- **Severity:** High (P1) — Visual Degradation
- **Reproduction:** Set viewport to `1024x768` (standard iPad landscape or 13" MacBook split-view) with sidebar expanded.
- **Observed Behavior:**
  1. The 5 KPI metric cards are forced into `st.columns(5)` within an 8-column remaining container (~700px width).
  2. The values are completely truncated into ellipses:
     - High Risk: `0 ...` instead of `0 / 55`
     - Moderate Risk: `6 ...` instead of `6 / 55`
     - Max 30-min Rain: `3...` instead of `39 mm`
     - Gauges Reporting: `6...` instead of `67 / 70`
  3. Metric labels truncate: `HIGH RIS...`, `MODERA...`, `MAX 30-...`, `GAUGES ...`.
  4. Metric footers truncate: `High from 0`, `Moderate from `, `Heaviest T...`, `Wettest T...`, `Reporting now `.
  5. The subtitle pill indicator wraps into 8 choppy lines.
- **Screenshot Evidence:** `tests/screenshots/qa_layout_laptop_1024x768.png`
- **Root Cause:**
  - Hardcoded `st.columns(5)` without responsive grid wrapping or container queries.
  - Inline CSS rule `white-space:nowrap;` in card footers combined with `overflow:hidden; text-overflow:ellipsis;` in cards narrower than 120px.
- **Remediation:**
  - Implement adaptive column distribution via CSS media queries or Streamlit column wrapping:
    ```css
    @media (max-width: 1200px) {
        div[data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
        }
        div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
            min-width: 140px !important;
            flex: 1 1 140px !important;
        }
    }
    ```

---

### Bug #3: Mobile Viewport Obscuration by Expanded Sidebar
- **Severity:** Critical (P0) — Mobile Unusable
- **Reproduction:** Open app on iPhone / Android viewport (`390x844`).
- **Observed Behavior:**
  1. `st.set_page_config(initial_sidebar_state="expanded")` forces the sidebar to open over the entire canvas on load.
  2. On a 390px mobile viewport, the 320px sidebar obscures 85% of the screen. The main dashboard is completely hidden except for sliced letter fragments (`ce`, `p:`, `0.71%`).
  3. If the user does collapse the sidebar, the 5 metric cards stack vertically into a 1,600px tall column, requiring 4 full screen swipes before the user sees the interactive map.
- **Screenshot Evidence:**
  - `tests/screenshots/qa_layout_mobile_390x844.png` (obscured)
  - `tests/screenshots/qa_mobile_sidebar_collapsed.png` (stacked cards)
- **Root Cause:**
  `initial_sidebar_state="expanded"` set in `st.set_page_config()`.
- **Remediation:**
  Change `initial_sidebar_state="auto"` so Streamlit automatically collapses the sidebar on mobile viewports (<768px). On small viewports, display top KPIs in a 2x3 compact grid or horizontal scrolling chip strip.

---

### Bug #4: State Desync in "Replay the 17 Apr 2021 storm" CTA Button
- **Severity:** Medium (P2) — State Persistence Inconsistency
- **Reproduction:**
  1. Go to Replay Storm mode on 17 Apr 2021.
  2. Scrub the time slider to `17:45` (rain is 0 mm, calm evening).
  3. Switch to another preset storm (e.g., `08 Jan 2018`).
  4. In the Zone Diagnostic card, click `"⟳ Replay the 17 Apr 2021 storm"`.
- **Observed Behavior:**
  The app switches back to `17 Apr 2021`, but the slider remains stuck at `17:45` where rainfall is 0 mm, rather than resetting to `12:15` (the demo storm peak). The user sees a dry, uneventful scene rather than the flood demonstration.
- **Root Cause:**
  ```python
  # streamlit_app.py (lines 422-426)
  def _replay_pinned_storm() -> None:
      """Jump to the 17 Apr 2021 replay. A callback, so it runs before the widgets are drawn."""
      st.session_state["mode"] = "Replay Storm"
      st.session_state["replay_date"] = replay_days.PINNED_STORM
  ```
  `_replay_pinned_storm` does not reset `st.session_state[f"replay-time-{replay_days.PINNED_STORM}"] = REPLAY_DEFAULT_TIME`.
- **Remediation:**
  Add the slider reset to the callback:
  ```python
  def _replay_pinned_storm() -> None:
      st.session_state["mode"] = "Replay Storm"
      st.session_state["replay_date"] = replay_days.PINNED_STORM
      st.session_state[f"replay-time-{replay_days.PINNED_STORM}"] = REPLAY_DEFAULT_TIME
  ```

---

### Bug #5: Obsolete / Dead CSS Selectors for Modern Streamlit
- **Severity:** Low (P3) — Code Quality / Styling Dead Code
- **Root Cause:**
  In `MODERN_TELEMETRY_LIGHT_CSS` (lines 196, 261-276):
  1. `button[data-testid="stButtonGroupButton"]`: Streamlit modern segmented controls render as `button[data-variant="segmented_control"]`. The active orange pill styles do not target this class.
  2. `div[data-testid="stSlider"] div[role="slider"]` and `div[data-baseweb="slider"]`: Streamlit migrated from BaseWeb sliders to React-Aria with `<input type="range">`. These rules never match any DOM node.
- **Remediation:**
  Update custom CSS selectors to target current Streamlit React-Aria elements:
  `div[data-testid="stSlider"] div[data-rac][data-orientation="horizontal"]` and `button[data-variant="segmented_control"]`.

---

## 3. Detailed Experimental Test Results

### 3.1 Preset Storm Buttons & Date Alignment
Each of the 6 curated flood events in `replay_days.py` was triggered via button click:
| Preset Label | Target Date | Slider Initial Time | Max 30-min Rain | Gauges Live | Transition Latency |
|---|---|---|---|---|---|
| **17 Apr 2021** | 2021-04-17 | 12:15 SGT | 39 mm (Tanglin) | 67 / 70 (96%) | 2.53s |
| **08 Jan 2018** | 2018-01-08 | 10:30 SGT | 28 mm (Bedok) | 56 / 56 (100%) | 2.53s |
| **23 Jun 2020** | 2020-06-23 | 08:00 SGT | 37 mm (Bedok) | 50 / 50 (100%) | 2.53s |
| **22 Nov 2024** | 2024-11-22 | 14:20 SGT | 40 mm (Sembawang) | 66 / 66 (100%) | 2.54s |
| **13 Apr 2025** | 2025-04-13 | 17:45 SGT | 41 mm (Yishun) | 66 / 66 (100%) | 2.53s |
| **04 Dec 2025** | 2025-12-04 | 17:35 SGT | 28 mm (Boon Lay) | 68 / 68 (100%) | 2.53s |

*Verification:* Date input, slider options, station counts, and model scoring stay 100% in sync across all presets.

---

### 3.2 Slider Scrubbing Stress Test (17 Apr 2021: 11:00 to 18:00)
The slider was programmatically scrubbed across **85 contiguous 5-minute intervals** (steps 132 to 216).
- **Latency Distribution:**
  - Min: 0.183s
  - Mean: 0.200s
  - Median: 0.193s
  - P95: 0.221s
  - Max: 0.234s
- **Render Stability:** **0 blank screen flickers** detected. Streamlit caching (`@st.cache_data`) for `day_features` successfully eliminates re-computation overhead, enabling near 60fps-equivalent scrubbing responsiveness.

#### Key Telemetry Progression Across Storm
- **11:00 SGT (Pre-storm):** 0 High risk, 0 Moderate risk, Max Rain: 0.0 mm.
- **12:15 SGT (Demo opening):** 0 High risk, 6 Moderate risk, Max Rain: 39 mm.
- **12:45 SGT (Storm Peak 1):** **9 High risk zones**, **19 Moderate risk zones**, Max Rain: 41 mm (Central Water Catchment), Wet-Ground: 77. Bukit Timah flood prob: **0.80% (High Risk)**.
  - *Screenshot:* `tests/screenshots/qa_peak_1245_17apr2021.png`
- **13:35 SGT (Storm Peak 2):** **11 High risk zones**, **7 Moderate risk zones**, Max Rain: 40 mm (Jurong West), Wet-Ground: **101**. Bukit Timah flood prob: **0.59% (Moderate Risk)**.
  - *Screenshot:* `tests/screenshots/qa_peak_1335_17apr2021.png`
- **16:00 SGT (Recession):** 0 High risk, 0 Moderate risk, Max Rain: 6 mm.
- **17:45 SGT (Dry evening):** 0 High risk, 0 Moderate risk, Max Rain: 0 mm.

---

### 3.3 Planning Area Dropdown Cycling
12 diverse planning areas were selected to test spatial geometry, edge micro-climates, and zero-rain conditions:
| Planning Area | Latency | Risk Classification | Flood Prob | Gauge Percentile | NaN / Inf Check |
|---|---|---|---|---|---|
| **BUKIT TIMAH** | 2.34s | High Risk (at 12:45) | 0.80% | Rare (99.8%) | **Clean (False)** |
| **BEDOK** | 1.57s | Low Risk | 0.04% | Typical (24.1%) | **Clean (False)** |
| **JURONG EAST** | 1.56s | High Risk (at 12:45) | 0.92% | Rare (99.9%) | **Clean (False)** |
| **CENTRAL WATER CATCHMENT** | 1.58s | High Risk (at 12:45) | 1.05% | Rare (100.0%) | **Clean (False)** |
| **CHANGI** | 1.57s | Low Risk | 0.01% | No rain (0.0%) | **Clean (False)** |
| **TUAS** | 1.57s | Low Risk | 0.08% | Typical (42.0%) | **Clean (False)** |
| **PUNGGOL** | 1.55s | Low Risk | 0.02% | Typical (12.5%) | **Clean (False)** |
| **NOVENA** | 1.57s | Moderate Risk | 0.38% | Heavier (88.4%) | **Clean (False)** |
| **QUEENSTOWN** | 1.57s | Moderate Risk | 0.44% | Heavier (89.1%) | **Clean (False)** |
| **MARINA SOUTH** | 1.57s | Low Risk | 0.05% | Typical (31.0%) | **Clean (False)** |
| **SIMPANG** | 1.56s | Low Risk | 0.01% | No rain (0.0%) | **Clean (False)** |
| **WESTERN ISLANDS** | 1.58s | Low Risk | 0.02% | No rain (0.0%) | **Clean (False)** |

*Observation:* Mathematical stability is rock solid. No divisions by zero in rarity scoring or probability thresholding were detected.

---

### 3.4 Edge-Case Dates Testing
| Date Tested | Context | Outcome | Notes |
|---|---|---|---|
| **2020-02-29** | Leap Year Day | **Passed** | 50 stations loaded, full 288 steps replayed seamlessly. |
| **2017-01-01** | Historical Store Start | **Passed** | 56 stations loaded, 72h warm-up handled correctly. |
| **2019-02-15** | Dry Day (0 rain) | **Passed** | All 55 zones Low risk, 0% flood probability, gauge shows 0.0% with clear caption. |
| **2010-01-01** | Out of bounds date | **Passed** | App catches empty store and shows warning notice instead of crashing. |

---

## 4. Prioritized Recommendations for Demo Readiness

1. **Implement Fallback Button on Cold Start Error Screen (P0):**
   Replace `st.stop()` with an actionable two-button choice: `[Retry Live Feed]` and `[⟳ Replay 17 Apr 2021 Storm (Recommended)]`.
2. **Make `initial_sidebar_state="auto"` (P0):**
   Prevent the sidebar from devouring mobile viewports on load.
3. **Responsive KPI Grid Layout (P1):**
   Wrap `st.columns(5)` into 2 rows on medium viewports (`@media (max-width: 1200px)`), avoiding truncated metric values like `0 ...` and `3...`.
4. **Fix State Reset in `_replay_pinned_storm` (P2):**
   Ensure that clicking "Replay the 17 Apr 2021 storm" resets both `replay_date` and the slider key `f"replay-time-{PINNED_STORM}"` to `"12:15"`.
5. **Modernize CSS Selectors (P3):**
   Remove dead BaseWeb selectors and align styling with Streamlit's React-Aria DOM nodes.
