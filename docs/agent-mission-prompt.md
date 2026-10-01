# MISSION: Autonomous End-to-End Build of "FloodSense" (DAISI Challenge 2026, Track B2)

You are acting as the Lead Staff Data Engineer, ML Architect, and Hackathon Competitor building **FloodSense: Flash Flood Prediction & Urban Drainage Intelligence** for the Databricks AI Social Impact (DAISI) Challenge Singapore 2026.

---

## 1. PROJECT GROUND RULES & CONTEXT

- **Target Event:** DAISI Challenge 2026, Track B2 (Climate Action & Resilience).
- **Upcoming Milestone:** Round 1 Idea Submission closes **Tuesday, 6 October 2026 at 11:59 PM SGT**. Submission requires a strictly structured 3-slide pitch deck or 1-page concept note (PDF) covering:
  - Slide 1: Problem & why it matters
  - Slide 2: Solution & data
  - Slide 3: Databricks architecture & impact
- **Execution Mandate:** You have full terminal, filesystem, and code execution permissions.
  - Implement, test, and verify everything locally first.
  - Decouple pure business logic from Databricks-specific runtimes so code is fully testable locally via pytest.
  - Never wait or ask for confirmation to write code, install libraries, create files, or run tests.
  - Only halt to ask the human user when hitting hard manual gates: providing missing API keys, running browser OAuth (`databricks auth login`), or approving final cloud deployments.
  - Proactively create/manage Python virtual environments (`.venv`) and install dependencies silently.

---

## 2. HARD PLATFORM CONSTRAINTS (Databricks Free Edition)

All production architecture must strictly comply with Free Edition quotas:

1. **Serverless Compute Only:** Strict daily compute caps. Any continuous execution will lock out the workspace for the day. Live pipelines MUST be triggered micro-batches (every 5–10 min during demos), NOT continuous 24/7 loops.
2. **Single Lakeflow Pipeline:** Free Edition allows only ONE active Lakeflow Declarative Pipeline. Ingestion, IDW spatial mapping, feature aggregation, and batch model inference MUST be unified into a single declarative pipeline.
3. **Compute Sizing:** 1 serverless SQL Warehouse at 2X-Small. No GPUs. No provisioned throughput serving endpoints. Model inference runs natively within the Lakeflow pipeline batch stream.
4. **Databricks Apps:** Up to 3 apps per workspace, auto-stopped after 24 hours. The app must start up rapidly and read from pre-computed Gold tables or a lightweight cache.

---

## 3. CORE ARCHITECTURAL SPECIFICATIONS

### A. Spatial Rainfall Engine (Stage 0)

- **Zones:** 55 URA Planning Areas from data.gov.sg (GeoJSON).
- **Rain Gauges:** ~50–60 NEA 5-minute automated weather stations.
- **Mapping:** Pre-compute an Inverse Distance Weighting (IDW) weight matrix from station coordinates to planning area centroids once:
  $$w_{ij} = \frac{1 / d(s_i, z_j)^p}{\sum_{k} 1 / d(s_k, z_j)^p}, \quad p=2$$
- **Dynamic Rebalancing:** Rain gauges go offline. The ingestion transform must dynamically re-normalize weights:
  $$\tilde{w}_{ij} = \frac{w_{ij} \cdot \mathbf{1}_{\{\text{station } i \text{ reporting}\}}}{\sum_{k} w_{kj} \cdot \mathbf{1}_{\{\text{station } k \text{ reporting}\}}}$$
- **Data Optimization:** The 9-year historical rainfall dataset (~47M rows) must be processed in stream/chunked generators. Discard all zero-rain intervals immediately to reduce the compute footprint by >85%.

### B. Feature Store & Storm Rarity (Stage 1 & Stage 3)

- **Rolling Windows:** Compute rolling 15-min, 30-min, 60-min, and 120-min cumulative rainfall per zone.
- **Storm Rarity Curve:** Fit an empirical return-period distribution (Generalized Pareto or Empirical Quantile) on active rain bursts per zone to compute return periods (e.g., "1-in-2-year storm in Bishan").
- **72-Hour "Wet Ground" Feature:**
  $$R_{\text{decay}}(t) = \sum_{\tau=0}^{72\text{h}} R(t - \tau) \cdot e^{-\lambda \tau}, \quad \lambda = \frac{\ln(2)}{24\text{ hours}}$$
- **PUB Monitored Flag:** Binary feature indicating whether the planning area has active PUB CCTV/water level sensors to correct for observational bias in unmonitored zones.

### C. Ground Truth Flood Event Extractor (Stage 2)

- **Target Variable:** Binary flag `flood_within_60min` $\in \{0, 1\}$ for zone $j$ at time $t$.
- **Extraction Schema (Pydantic):**
  ```python
  class FloodEvent(BaseModel):
      timestamp_start: datetime
      timestamp_end: Optional[datetime]
      location_raw: str
      ura_planning_area: str
      severity: Literal["Minor", "Moderate", "Severe"]
      source_reference: str
      geocoding_confidence: float
  ```
- **Dual Pipeline:**
  - Workspace Target: SQL using `ai_query('databricks-meta-llama-3-3-70b-instruct', ...)` parsing ingested public text.
  - Local Fallback: Python script calling external LLM API + OneMap API reverse geocoder mapped to URA polygon boundaries via Shapely (`point.within(polygon)`).

### D. Model Training, Registry & Evaluation (Stage 3 & 5)

- **Models:**
  - Baseline: Rule heuristic (any station in zone >= 25mm in 30 min).
  - Primary: Class-weighted Logistic Regression (interpretable, robust on scarce ~90 flood events).
  - Challenger: LightGBM with strict Cross-Validated SMOTE (never apply SMOTE outside CV folds).
- **Target Tiers:** Convert output probabilities to Low, Moderate, High using PUB-aligned operational thresholds calibrated on validation data.
- **Validation Split:** Train on 2017–2023 data; strictly evaluate on unseen 2024–2026 data.
- **Metrics:** Precision-Recall AUC, Brier Score, and False-Alarm Rate on heavy rain days. (Reject accuracy as a metric due to severe class imbalance).
- **MLflow:** Log all runs, feature importance, PR curves, and register the winning artifact to Unity Catalog.

### E. Live Streaming, Replay & App (Stage 4 & 6)

- **Lakeflow Architecture:**
  - Landing: API poller drops validated 5-min JSON payloads into a Unity Catalog Volume.
  - Bronze: Auto Loader table with `cloudFiles.rescuedDataColumn` to defensively handle malformed responses.
  - Silver: Streaming table applying IDW weights with `@dlt.expect_or_drop("valid_rainfall", "rainfall_mm >= 0")`.
  - Gold / Predictions: Streaming batch scoring applying the registered ML model.
- **Replay Mode:** Include a pre-packaged replay slice of a known major storm (e.g., 17 April 2021 western Singapore flash flood) that can be dropped into the landing volume on demand, guaranteeing a live dynamic demo regardless of weather conditions.
- **Frontend App:** Streamlit app packaged for Databricks Apps, rendering:
  - Real-time Singapore choropleth map of URA planning areas colored by risk tier (Low / Moderate / High).
  - Zone inspection drawer showing storm rarity curves and 72-hour wet ground status.
  - 3-year historical flood-prone trend chart (PUB 2022–2025 hectarage).
  - Toggle switch between "Live Feed" and "Replay April 2021 Storm".

---

## 4. IMMEDIATE EXECUTION ROADMAP

Begin executing sequentially right now:

**Step 1: Environment Initialization & Contract Definition**
- Initialize a local git repository, virtual environment `.venv`, and `requirements.txt` (including pydantic, geopandas, shapely, scikit-learn, lightgbm, mlflow, streamlit, requests, pytest).
- Establish strict schema contracts in a shared module `src/common/schemas.py`.

**Step 2: Round 1 Pitch Deck Deliverable Generation**
- Automatically synthesize and export `submission/round1_pitch_deck.md` strictly matching the official DAISI 3-slide format:
  - Slide 1: Problem statement, 2025 record rainfall context, the "rare flood event" ML pitfall.
  - Slide 2: Two-stage solution (rarity gap + LLM event database), data source table.
  - Slide 3: Lakeflow serverless architecture diagram in ASCII/Mermaid, replay demo design, social impact.

**Step 3: Spatial Engine & Data Pipeline Build**
- Fetch URA Planning Area boundaries from data.gov.sg and compute centroids.
- Generate `src/spatial/idw_matrix.py` and produce static `data/processed/station_zone_weights.parquet`.
- Build defensive API poller `src/ingestion/poller.py` for NEA 5-min rainfall and PUB alerts with schema validation.

**Step 4: ML Training Engine & Evaluation Suite**
- Implement `src/features/feature_pipeline.py` (rolling accumulation, rarity calculation, decay factor).
- Write `src/models/train.py` with baseline comparison, MLflow tracking, and PR curve metrics.
- Write tests in `tests/test_pipeline.py` verifying weight rebalancing and zero-rain filtering.

**Step 5: Databricks Deployment Assets & Streamlit App**
- Construct `databricks/pipelines/lakeflow_pipeline.py` housing the unified Bronze → Silver → Predictions pipeline.
- Build the Streamlit application `src/app/streamlit_app.py` complete with local simulation mode and replay capabilities.
- Generate a single `DEPLOYMENT.md` providing the copy-paste CLI commands needed to authenticate, upload tables, and trigger the pipeline.

Report what packages and files you are creating as you execute. Begin Step 1 immediately.
