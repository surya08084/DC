# Solution Document
## Vapor Tobacco Product Compliance Matching Framework

**Version:** 1.0  
**Date:** 2026-06-02  
**Status:** Implemented  
**Requirements Traceability:** See REQUIREMENTS.md

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Component Design](#4-component-design)
   - [4.1 Data Ingestion Layer (Layer 0)](#41-data-ingestion-layer-layer-0)
   - [4.2 Normalization Layer (Layer 1)](#42-normalization-layer-layer-1)
   - [4.3 Entity Resolution Layer (Layer 2)](#43-entity-resolution-layer-layer-2)
   - [4.4 Registry Layer (Layer 3)](#44-registry-layer-layer-3)
   - [4.5 Compliance Classification Layer (Layer 4)](#45-compliance-classification-layer-layer-4)
   - [4.6 API Layer](#46-api-layer)
   - [4.7 Pipeline Orchestrator](#47-pipeline-orchestrator)
5. [Data Models](#5-data-models)
6. [Algorithm Details](#6-algorithm-details)
   - [6.1 Position-Aware Flavor Extraction](#61-position-aware-flavor-extraction)
   - [6.2 Fuzzy Matching — Field-Weighted Composite](#62-fuzzy-matching--field-weighted-composite)
   - [6.3 Semantic Ensemble (Stage 3)](#63-semantic-ensemble-stage-3)
   - [6.4 Behavioral Re-ranking (Stage 4)](#64-behavioral-re-ranking-stage-4)
   - [6.5 LLM Adjudication (Stage 5)](#65-llm-adjudication-stage-5)
   - [6.6 LLM Normalization (Path A)](#66-llm-normalization-path-a)
   - [6.7 Extraction Validation](#67-extraction-validation)
   - [6.8 Confidence Rollup](#68-confidence-rollup)
7. [Compliance Rules Engine](#7-compliance-rules-engine)
8. [Configuration & Feature Toggles](#8-configuration--feature-toggles)
9. [Reference Dictionaries](#9-reference-dictionaries)
10. [End-to-End Data Flow](#10-end-to-end-data-flow)
11. [Error Handling & Resilience](#11-error-handling--resilience)
12. [Performance Design](#12-performance-design)
13. [Security Design](#13-security-design)
14. [Deployment Architecture](#14-deployment-architecture)
15. [Testing Strategy](#15-testing-strategy)
16. [Design Decisions & Trade-offs](#16-design-decisions--trade-offs)
17. [Limitations & Known Constraints](#17-limitations--known-constraints)

---

## 1. System Overview

The framework processes abbreviated internal product names (e.g. `"JOOL VT 5% POD"`) and outputs a per-product, per-state compliance classification (`FEDERAL_LICIT`, `GREY_MARKET`, `ILLICIT`, etc.) with a full audit trail.

The pipeline has five layers:

```
Layer 0 — Ingestion      Read CSV/Excel/PDF → RawSKU objects
Layer 1 — Normalization  RawSKU → NormalizedSKU (structured attributes)
Layer 2 — Matching       NormalizedSKU → CanonicalProduct match (with confidence)
Layer 3 — Registry       Canonical product → FDA + state authorization records
Layer 4 — Classification FDA status + state status + rules → ComplianceResult
```

All outputs carry a `confidence` score and a `reason` string. Records below a confidence floor are routed to a human review queue rather than auto-classified.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          EXTERNAL INPUTS                                │
│   CSV/Excel (POS / STARS)         PDF (State Directories)               │
│   REST API (single record)        FDA Authorization CSV                 │
└────────────┬──────────────────────────────────┬────────────────────────┘
             │                                  │
             ▼                                  ▼
┌────────────────────────┐        ┌─────────────────────────────────────┐
│  LAYER 0 — INGESTION   │        │  REFERENCE REGISTRIES               │
│  csv_parser.py         │        │  cpg_registry.py   (CPG master)     │
│  pdf_parser.py         │        │  fda_registry.py   (FDA auth)       │
│  → RawSKU              │        │  state_registry.py (50 states)      │
└────────────┬───────────┘        └──────────────┬──────────────────────┘
             │                                   │
             ▼                                   │
┌────────────────────────────────────────────┐   │
│  LAYER 1 — NORMALIZATION                   │   │
│  ┌───────────────────────────────────────┐ │   │
│  │ PATH A (LLM-First)                    │ │   │
│  │  llm_extractor.py → Claude API        │ │   │
│  │  extraction_validator.py              │ │   │
│  └───────────────────────────────────────┘ │   │
│  ┌───────────────────────────────────────┐ │   │
│  │ PATH B (Dictionary Fallback)          │ │   │
│  │  text_cleaner.py                      │ │   │
│  │  abbreviation_expander.py             │ │   │
│  │  unit_normalizer.py                   │ │   │
│  │  flavor_extractor.py                  │ │   │
│  └───────────────────────────────────────┘ │   │
│  → NormalizedSKU                           │   │
└────────────┬───────────────────────────────┘   │
             │                                   │
             ▼                                   │
┌────────────────────────────────────────────┐   │
│  LAYER 2 — ENTITY RESOLUTION               │   │
│  Stage 1: exact_matcher.py   (hash map)    │   │
│  Stage 2: fuzzy_matcher.py   (rapidfuzz)   │   │
│  Stage 3: semantic_ensemble.py             │   │
│    ├─ tfidf_matcher.py  (sklearn)          │   │
│    ├─ bm25_matcher.py   (rank_bm25)        │   │
│    └─ embedding_matcher.py (fastembed+FAISS│   │
│  Stage 4: behavioral_matcher.py            │   │
│  Stage 5: llm_adjudicator.py               │   │
│  → MatchResult                             │   │
└────────────┬───────────────────────────────┘   │
             │                                   │
             ▼                                   │
┌────────────────────────────────────────────┐   │
│  LAYER 3 — REGISTRY LOOKUP                 │◄──┘
│  fda_registry.py  → FDARecord              │
│  state_registry.py → StateRecord (×states) │
└────────────┬───────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────┐
│  LAYER 4 — COMPLIANCE CLASSIFICATION       │
│  rules_engine.py  (deterministic IF/THEN)  │
│  decision_engine.py (rollup + output)      │
│  → ComplianceResult (per SKU × state)      │
└────────────┬───────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────┐
│  OUTPUTS                                   │
│  REST API response (JSON)                  │
│  CSV export                                │
│  PostgreSQL (for reporting)                │
│  Review queue (REVIEW_REQUIRED records)    │
└────────────────────────────────────────────┘
```

---

## 3. Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.11 | Dominant ML/data ecosystem; type hints |
| Data validation | Pydantic v2 | Schema enforcement for all data models |
| Config | pydantic-settings | `.env` override, type-safe config |
| API framework | FastAPI | Async, auto-schema, OpenAPI docs |
| Fuzzy matching | rapidfuzz | C++ backend, 10× faster than fuzzywuzzy |
| TF-IDF | scikit-learn | Mature, `char_wb` analyzer, cosine similarity |
| BM25 | rank-bm25 | Pure-Python BM25Okapi, no external deps |
| Embeddings | fastembed (ONNX) | No PyTorch; single model download; 3× faster inference than sentence-transformers |
| Vector search | FAISS (faiss-cpu) | Billion-scale cosine search; persistent index |
| LLM | Claude (Anthropic SDK) | Structured JSON extraction; hosted API; no GPU required |
| PDF parsing | pdfplumber + pytesseract | Native PDF tables + OCR fallback for scanned docs |
| Data processing | pandas | CSV I/O, DataFrame operations |
| Testing | pytest | Smoke tests, no external dependencies required |
| Database | PostgreSQL | Persistent storage for results and overrides |

---

## 4. Component Design

### 4.1 Data Ingestion Layer (Layer 0)

**Files:** `vapor_compliance/ingestion/csv_parser.py`, `pdf_parser.py`

#### CSV / Excel Parser
- Reads CSV or Excel files into a list of `RawSKU` objects
- Configurable column mapping: source system column names are aliased to the canonical field names
- Deduplication: records with the same `sku_id` within a batch are deduplicated (last-wins)
- Preserves `raw_name` verbatim; never modifies source data

#### PDF Parser
- Used for state regulatory directory PDFs that are not available as CSV
- Primary: `pdfplumber` for PDFs with embedded table structure
- Fallback: `pytesseract` OCR for scanned/image PDFs
- Output: pandas DataFrame which is then loaded into the state registry

### 4.2 Normalization Layer (Layer 1)

**Files:** `vapor_compliance/normalization/normalizer.py` (orchestrator), plus six sub-modules

The normalizer runs a **two-path pipeline**. Path A is tried first; Path B is the fallback.

#### Path A — LLM-First (`llm_extractor.py` + `extraction_validator.py`)

**Trigger:** `use_llm=True` AND `ANTHROPIC_API_KEY` is set

**Step 1 — LLM Extraction**

`LLMExtractor.extract_one(raw_name)` or `extract_batch(raw_names)`:

1. Check MD5 hash cache (`_hash = md5(raw_name.lower().strip())`)
2. If cached, return immediately (zero API calls for duplicate SKUs)
3. If not cached, send to Claude API with a structured system prompt
4. Parse JSON response into `SKUExtractionResult` (Pydantic model)
5. On parse error or exception: log warning, return `None` (fall through to Path B)

The system prompt enforces:
- Nicotine conversion rule: `percent × 10 = mg/ml` (5% = 50.0, never 5.0)
- Exact field names and allowed enum values
- "Return ONLY valid JSON — no markdown, no explanation"

**Step 2 — Extraction Validation** (`extraction_validator.py`)

`ExtractionValidator.validate(extraction)` runs field-by-field against dictionaries:

```
brand    → brand_aliases.json (exact) → Jaro-Winkler ≥ 0.92 (fuzzy) → UNVERIFIED
flavor   → flavor_synonyms.json canonical set (exact) → synonym dict → UNVERIFIED
nicotine → recompute from nicotine_strength_raw; compare with LLM value (tolerance 0.1)
abbr     → check each {abbr: expansion} pair against abbreviations.json
form     → check against {POD, DISPOSABLE, CARTRIDGE, MOD, E-LIQUID, DEVICE, TANK}
```

Confidence computation:
```
validation_confidence = Σ (field_score × field_weight)
  brand:  weight 0.30
  flavor: weight 0.25
  nic:    weight 0.25
  abbr:   weight 0.10
  form:   weight 0.10

overall_confidence = 0.5 × llm_confidence + 0.5 × validation_confidence
```

If `overall_confidence < 0.20` → discard, fall through to Path B.

**Step 3 — Build NormalizedSKU**

The `_build_from_validated()` method:
- Uses validated (corrected) values from `ValidationResult`
- Supplements any missing fields with `unit_normalizer.extract_all()` output
- Sets `normalization_method` based on flag types:
  - `"llm+validated"` — all confirmed
  - `"llm+corrections"` — dictionary overrode some fields
  - `"llm+unverified"` — novel values kept from LLM
  - `"llm+corrections+unverified"` — both

---

#### Path B — Dictionary Fallback

**Sub-module execution order:**

**1. `text_cleaner.clean(text)`**
- `unicodedata.normalize("NFKD", text)` — normalize Unicode variants
- Lowercase
- Strip noise punctuation except `%`, `/`, `-`, `.`
- Collapse multiple whitespace to single space
- Strip leading/trailing whitespace

**2. `abbreviation_expander.expand_text(text)`**
- Tokenize on whitespace
- For each token, check `abbreviations.json` (key: `TOKEN_UPPER`)
- Replace token with expansion if found
- Return `(expanded_text, expansions_dict)`

**3. `unit_normalizer.extract_all(text)`**
Returns a dict: `{nicotine_mg_ml, volume_ml, puff_count, pack_count, product_type}`

Nicotine parsing (priority order):
- `\d+(?:\.\d+)?%` → value × 10 (5% → 50.0)
- `\d+(?:\.\d+)?\s*mg/ml` → value as-is
- `\d+(?:\.\d+)?\s*mg` → value as-is (assumed mg/ml)

Volume: `\d+(?:\.\d+)?\s*ml` → value as-is

Puff count: `\d+\s*(?:puffs?|hits?)` → integer

Product type: token-based lookup against `unit_mappings.json → product_type_map`

**4. `abbreviation_expander.resolve_brand(text)`**
- Check each token against `brand_aliases.json` (all alias→canonical mappings in uppercase)
- First match wins; return canonical brand name

**5. `flavor_extractor.extract(text, brand)`**
Position-aware residual method — see §6.1 for full algorithm.

**6. Build NormalizedSKU with confidence deductions:**
```
confidence = 1.0
if not brand:              confidence -= 0.25
if not flavor_canonical:   confidence -= 0.15
elif flavor_conf < 1.0:    confidence -= 0.15 × (1 - flavor_conf)
if not nicotine_mg_ml:     confidence -= 0.15
if not product_type:       confidence -= 0.10
confidence = max(0.0, round(confidence, 2))
```

---

### 4.3 Entity Resolution Layer (Layer 2)

**Files:** `vapor_compliance/matching/entity_resolver.py` (orchestrates all stages)

#### Stage Cascade Logic

```python
def resolve(sku: NormalizedSKU) -> MatchResult:
    # Stage 1
    if ENABLE_EXACT:
        result = exact_matcher.match(sku)
        if result.confidence >= AUTO_CLASSIFY_MIN:
            return result

    # Stage 2
    if ENABLE_FUZZY:
        result = fuzzy_matcher.match(sku)
        if result.confidence >= AUTO_CLASSIFY_MIN:
            return result

    # Stage 3 (parallel ensemble — not sequential)
    if any(ENABLE_TFIDF, ENABLE_BM25, ENABLE_EMBEDDING):
        result = stage3.match(sku)  # SemanticEnsemble
        if result.confidence >= AUTO_CLASSIFY_MIN:
            return result

    # Stage 4
    if ENABLE_BEHAVIORAL and result.matched_cpg_id:
        result = behavioral_matcher.rerank(sku, result)
        if result.confidence >= AUTO_CLASSIFY_MIN:
            return result

    # Stage 5
    if ENABLE_LLM and REVIEW_QUEUE_MIN <= best_conf <= LLM_TRIGGER_MAX:
        result = llm_adjudicator.adjudicate(sku, candidates)
        return result

    return best_result
```

Every stage's confidence is recorded in `stage_scores`. The cascade stops as soon as a stage returns ≥ `AUTO_CLASSIFY_MIN` (0.75).

---

### 4.4 Registry Layer (Layer 3)

**Files:** `vapor_compliance/registry/cpg_registry.py`, `fda_registry.py`, `state_registry.py`

#### CPG Registry
- In-memory dict keyed by `cpg_id`
- Loaded from CSV at startup; hot-reloadable
- Builds all matching indexes (TF-IDF, BM25, FAISS) from the CPG product list

#### FDA Registry
- Dict keyed by `cpg_id`
- Temporal query: `get_record(cpg_id, snapshot_date)` returns the active record (where `effective_from ≤ date ≤ effective_to`)
- Returns `fda_match_flag=False` if no record exists (not an error)

#### State Registry
- Dict keyed by `(cpg_id, state)`
- Per-state `state_has_registry` flag: if False, a missing state record does not trigger GREY_MARKET
- Supports three directory models:
  - `WHITELIST`: must be listed to be legal → absence = GREY_MARKET (if FDA-approved) or ILLICIT
  - `BLACKLIST`: must NOT be listed to be legal → presence = ILLICIT
  - `ENFORCEMENT_ONLY`: no formal list; FDA status governs

---

### 4.5 Compliance Classification Layer (Layer 4)

**Files:** `vapor_compliance/compliance/rules_engine.py`, `decision_engine.py`

#### Rules Engine (deterministic IF/THEN)

```
Input: fda_record, state_record, normalized_sku

Priority 1 — Low-confidence bypass:
  IF match_confidence < REVIEW_QUEUE_MIN
    → REVIEW_REQUIRED

Priority 2 — Flavor ban override:
  IF state_record.flavor_ban_applies = True
  AND normalized_sku.flavor_category IN state.banned_categories
  AND state_record.enforcement_status = IN_EFFECT
    → ILLICIT (reason: "Flavor ban: <category> banned in <state>")

Priority 3 — Core decision matrix:
  IF fda=Y AND state_listed=Y                     → FEDERAL_LICIT
  IF fda=Y AND state_listed=N AND has_registry=Y  → GREY_MARKET
  IF fda=Y AND state_listed=N AND has_registry=N  → FEDERAL_LICIT
  IF fda=N AND state_listed=Y                     → POTENTIAL_ILLICIT
  IF fda=N AND state_listed=N                     → ILLICIT
  IF fda=N AND no_state_record                    → ILLICIT

Priority 4 — Unknown:
  IF no FDA record AND no state record            → UNKNOWN
```

No machine learning. Every branch is a conditional statement. Every decision generates a `reason` string that references the specific record IDs used.

---

### 4.6 API Layer

**File:** `vapor_compliance/api/app.py` (FastAPI)

#### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/compliance/lookup` | Single SKU JSON → ComplianceResult |
| POST | `/compliance/batch` | CSV file upload → CSV/JSON results |
| GET | `/algorithms/status` | Current stage enable/disable state |
| POST | `/algorithms/toggle` | Enable/disable a stage at runtime |
| GET | `/algorithms/thresholds` | Current confidence thresholds |
| GET | `/health` | Liveness/readiness check |
| GET | `/review/queue` | List REVIEW_REQUIRED records (filterable) |

#### Request/Response Example

```json
POST /compliance/lookup
{
  "sku_id": "SKU-001",
  "raw_name": "JOOL VT 5% POD 4PK",
  "source": "POS",
  "state": "TX"
}

Response:
{
  "sku_id": "SKU-001",
  "raw_name": "JOOL VT 5% POD 4PK",
  "normalized_name": "JUUL Virginia Tobacco 50.0mg/ml Pod",
  "brand": "JUUL",
  "flavor_canonical": "Virginia Tobacco",
  "nicotine_mg_ml": 50.0,
  "form_factor": "POD",
  "pack_count": 4,
  "matched_cpg_id": "CP0001",
  "match_stage": "EXACT",
  "match_confidence": 1.0,
  "state": "TX",
  "compliance_status": "FEDERAL_LICIT",
  "confidence": 0.97,
  "reason": "FDA authorized (MGO-2021-001); TX has no state registry; federal authorization applies",
  "normalization_method": "dictionary",
  "is_review_required": false,
  "snapshot_date": "2026-06-02"
}
```

---

### 4.7 Pipeline Orchestrator

**File:** `vapor_compliance/pipeline/orchestrator.py`

```python
class CompliancePipeline:
    def startup(self):
        # Load all registries, build all indexes
        # Warm up LLM client if API key present
        # Pre-load fastembed model if embedding enabled

    def process_sku(self, raw: RawSKU, states: list[str]) -> list[ComplianceResult]:
        # 1. Normalize
        # 2. Match
        # 3. For each state: lookup FDA + state records, classify
        # 4. Return list[ComplianceResult] (one per state)

    def process_batch(self, raws: list[RawSKU]) -> pd.DataFrame:
        # normalize_batch() if LLM enabled (one batch API call)
        # else normalize each via dictionary
        # Then match + classify each
        # Return DataFrame

    def process_from_csv(self, path: str) -> pd.DataFrame:
        # csv_parser.parse(path) → list[RawSKU]
        # → process_batch()

    def get_summary(self, df: pd.DataFrame) -> dict:
        # Counts by status, by state, review queue size
```

---

## 5. Data Models

### 5.1 Input / Processing Models

```
RawSKU                     NormalizedSKU
──────────────────         ──────────────────────────────
sku_id: str                raw_sku_id: str  ← links to RawSKU
raw_name: str              brand: Optional[str]
source: str                manufacturer: Optional[str]
manufacturer: Optional     product_type: Optional[str]
state: Optional[str]       flavor: Optional[str]
price: Optional[float]     flavor_canonical: Optional[str]
retailer_ids: list[str]    flavor_category: Optional[str]
load_timestamp: datetime   nicotine_mg_ml: Optional[float]
source_version: str        volume_ml: Optional[float]
                           puff_count: Optional[int]
                           pack_count: Optional[int]
                           form_factor: Optional[str]
                           raw_name: str  ← preserved
                           normalized_name: str
                           abbreviations_expanded: dict
                           normalization_confidence: float
                           normalization_method: str
                           high_risk_flags: list[str]
                           source: str
```

### 5.2 Match Models

```
MatchResult
──────────────────────────────────────────
query_sku_id: str
matched_cpg_id: Optional[str]
match_stage: MatchStage  (EXACT|FUZZY|SEMANTIC_ENSEMBLE|BEHAVIORAL|LLM|NO_MATCH)
confidence: float
field_scores: Optional[FieldScore]
  └─ brand, flavor, nicotine, form_factor, product_line, composite: float
match_explanation: str
is_review_required: bool
candidates_considered: int
stage_scores: dict[str, float]        # {"exact": 0.0, "fuzzy": 0.82, ...}
stage3_predictions: dict[str, Stage3Prediction]
  └─ "tfidf": {cpg_id, confidence, predicted}
  └─ "bm25":  {cpg_id, confidence, predicted}
  └─ "embedding": {cpg_id, confidence, predicted}
stage3_agreement: str  ("all_agree"|"majority"|"split"|"single"|"")
```

### 5.3 Compliance Models

```
ComplianceResult
──────────────────────────────────────────────────────
sku_id: str
cpg_id: Optional[str]
state: str
snapshot_date: date
compliance_status: ComplianceStatus
  (FEDERAL_LICIT|STATE_LICIT|GREY_MARKET|
   POTENTIAL_ILLICIT|ILLICIT|REVIEW_REQUIRED|UNKNOWN|EXCLUDED)
confidence: float            # overall rollup
normalization_confidence: float
match_confidence: float
rule_confidence: float
reason: str                  # human-readable, references specific records
fda_approved: bool
state_listed: bool
state_has_registry: bool
fda_record: Optional[FDARecord]
state_record: Optional[StateRecord]
match_method: str
override_flag: bool
override_reason: Optional[str]
analyst_id: Optional[str]
```

---

## 6. Algorithm Details

### 6.1 Position-Aware Flavor Extraction

**File:** `vapor_compliance/normalization/flavor_extractor.py`

**Problem:** Flavor can appear at any position in a SKU name. Naive substring scan causes false matches on model codes (BC5000), product lines (ACE, Alto), and unit tokens (50mg, 5000puffs).

**Solution — Residual Token Method:**

```
Input tokens: ["elf", "bar", "bc5000", "blueberry", "ice", "50mg", "disposable"]

Strip pipeline (in order):
  1. Brand tokens:    {elf, bar} from brand_aliases.json + brand parameter
  2. Model codes:     regex ^([A-Za-z]{1,4}\d{2,}|\d{2,}[A-Za-z]{0,4}|[A-Za-z]{1,2}\d[A-Za-z]*)$
  3. Unit tokens:     regex ^\d+(?:\.\d+)?(?:%|mg(?:/ml)?|ml|puff|puffs|hit|hits|ct|pk|pack|count)$
  4. Product types:   product_type_map keys from unit_mappings.json
  5. Product lines:   generic + brand-specific + size keywords from product_lines.json
  6. Pure numbers:    regex ^\d+$

Residual: ["blueberry", "ice"]

N-gram match (longest first):
  2-gram "blueberry ice" → _FLAVOR_LOOKUP.get("BLUEBERRY ICE") → None
  1-gram "blueberry"     → _FLAVOR_LOOKUP.get("BLUEBERRY")     → "Blueberry" ✓

Confidence: 0.80 (single-token residual match)
```

**Fallback chain:**
1. Multi-word n-gram on residual → conf 1.0
2. Single-word n-gram on residual → conf 0.80
3. Full-text n-gram scan → conf 0.60 (residual was empty)
4. Longest-first substring scan → conf 0.40 (last resort)
5. No match → conf 0.0

**N-gram matching strategy:** slide window from `max_n = min(5, len(tokens))` down to 1; take first (longest) hit. This ensures `"Watermelon Ice"` wins over just `"Ice"` when both could match.

---

### 6.2 Fuzzy Matching — Field-Weighted Composite

**File:** `vapor_compliance/matching/fuzzy_matcher.py`

Three metrics, chosen for specific field characteristics:

| Field | Metric | Reason |
|-------|--------|--------|
| Brand | Jaro-Winkler similarity | Short strings; prefix differences matter most (`JUUL` vs `JOOL`) |
| Flavor | Token Set Ratio | Word order varies (`Ice Watermelon` = `Watermelon Ice`) |
| Full name | Damerau-Levenshtein | Handles transpositions — most common typing error |
| Nicotine | Exact equality (±0.1 mg/ml) | Never fuzzy-match; distinct regulated products |
| Form factor | Exact equality | Canonical set; no fuzzy needed |

**Composite score:**

```python
field_weight = {brand: 1.0, nicotine: 0.9, form_factor: 0.8, flavor: 0.7, product_line: 0.3}

composite = Σ(score_i × weight_i) / Σ(weight_i)

# Brand disqualification rule:
if brand_score < BRAND_MIN_SCORE (0.90):
    composite = min(composite, 0.50)
```

**Candidate pre-filtering:** before scoring all CPG records, filter to candidates where the first letter of the brand matches. This reduces the O(n) scan cost for large registries.

---

### 6.3 Semantic Ensemble (Stage 3)

**File:** `vapor_compliance/matching/semantic_ensemble.py`

Runs three matchers **in parallel** (not as a sequential fallback) and combines their predictions by confidence-weighted voting.

#### Stage 3a — TF-IDF (`tfidf_matcher.py`)

```python
vectorizer = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4), min_df=1)
# Fit on all CPG normalized_name strings at index build time
# At match time: transform query → cosine similarity against all reference vectors
# Return cpg_id with highest score (if >= TFIDF_THRESHOLD)
```

Character n-grams (`char_wb`) handle:
- Typos: `Menhtol` / `Menthol` share most 2/3-grams
- Concatenations: `BlueberryIce` / `Blueberry Ice`
- Partially-expanded abbreviations

#### Stage 3b — BM25 (`bm25_matcher.py`)

```python
tokenized_corpus = [doc.split() for doc in cpg_names]
bm25 = BM25Okapi(tokenized_corpus)

# At match time:
scores = bm25.get_scores(query.split())
normalized = scores / max(scores)  # → [0, 1]
# Return cpg_id with highest score (if >= BM25_THRESHOLD)
```

BM25 gives higher weight to **rare** terms (high IDF). A token like `JUUL` appears in many records; a specific product line token like `ALTITUDE` is rare and highly discriminative.

#### Stage 3c — Embedding (`embedding_matcher.py`)

```python
# At startup (once):
model = TextEmbedding("BAAI/bge-small-en-v1.5")  # ONNX; 33MB; no PyTorch
embeddings = list(model.embed(cpg_names))
index = faiss.IndexFlatIP(dim)  # inner product = cosine on unit-normalized vectors
index.add(np.array(embeddings))

# At match time (per query):
q_emb = list(model.embed([query_text]))[0]
scores, indices = index.search(q_emb.reshape(1, -1), k=10)
# Return best match (if score >= EMBEDDING_THRESHOLD)
```

fastembed (ONNX runtime) vs sentence-transformers:
- No PyTorch dependency
- Model loads in ~0.5s vs ~3s for sentence-transformers
- 33MB model vs 90MB for `all-MiniLM-L6-v2`
- BAAI/bge-small-en-v1.5 outperforms all-MiniLM on MTEB retrieval

Reference embeddings are pre-computed once at ingestion and stored in FAISS. At match time, only the incoming SKU is embedded.

#### Ensemble Voting

```python
def _vote(active_predictions):
    # active_predictions: list of (cpg_id, confidence)
    # from each enabled method (tfidf, bm25, embedding)

    cpg_weights = defaultdict(float)
    for cpg_id, confidence in active_predictions:
        cpg_weights[cpg_id] += confidence

    winner = max(cpg_weights, key=cpg_weights.get)
    votes_for_winner = sum(1 for cpg, _ in active_predictions if cpg == winner)
    total_votes = len(active_predictions)

    ensemble_confidence = cpg_weights[winner] / total_votes

    if total_votes > 1 and votes_for_winner == total_votes:
        ensemble_confidence += 0.05  # unanimous boost
        agreement = "all_agree"
    elif votes_for_winner > total_votes / 2:
        ensemble_confidence += 0.02  # majority boost
        agreement = "majority"
    else:
        agreement = "split"

    return winner, ensemble_confidence, agreement
```

---

### 6.4 Behavioral Re-ranking (Stage 4)

**File:** `vapor_compliance/matching/behavioral_matcher.py`

Applies behavioral signals as additive boosts (capped at `BEHAVIORAL_BOOST_MAX` = 0.10 total):

| Signal | Score | Weight |
|--------|-------|--------|
| Price deviation | `1 - abs(sku_price - ref_price) / ref_price` (clipped 0–1) | 0.30 |
| Retailer overlap | Jaccard(`sku.retailer_ids`, `cpg.typical_retailer_ids`) | 0.40 |
| State overlap | Jaccard(`sku.observed_states`, `cpg.typical_states`) | 0.20 |
| Launch timing | Gaussian decay on days since product launch | 0.10 |

```python
behavioral_score = Σ(signal_score × signal_weight)
boost = behavioral_score × BEHAVIORAL_BOOST_MAX
result.confidence = min(1.0, result.confidence + boost)
```

Stage 4 never changes the matched CPG — it only adjusts the confidence of the top-ranked candidate from Stage 3.

---

### 6.5 LLM Adjudication (Stage 5)

**File:** `vapor_compliance/matching/llm_adjudicator.py`

**Trigger:** `REVIEW_QUEUE_MIN ≤ best_confidence ≤ LLM_TRIGGER_MAX` (default: 0.50–0.69)

Expected < 5% of records based on historical accuracy at earlier stages.

**Input to Claude:**
- Normalized SKU attributes
- Top-3 CPG candidates with their field scores and canonical names

**Output:**
- Structured JSON: `{matched_cpg_id, confidence, reasoning, matched_fields}`

**Rate limit guard:** `LLM_MAX_CALLS_PER_BATCH` (default 500) prevents runaway API spend on large batches.

---

### 6.6 LLM Normalization (Path A)

**File:** `vapor_compliance/normalization/llm_extractor.py`

#### Batch Processing

```python
def normalize_batch(raws: list[RawSKU]) -> list[NormalizedSKU]:
    raw_names = [r.raw_name for r in raws]
    extractions = llm_extractor.extract_batch(raw_names)
    # extract_batch groups uncached names into chunks of BATCH_SIZE (20)
    # One API call per chunk → amortizes latency
    # Results keyed by raw_name, merged with cache hits
```

A batch of 100 SKUs → 5 API calls (20 SKUs each) vs 100 individual calls. At typical API latency of 1–2s per call, this is a 20× throughput improvement.

#### System Prompt (key excerpts)

```
- nicotine_mg_ml: ALWAYS convert to mg/ml. Rule: percent × 10 = mg/ml (5% = 50.0)
- normalized_sku: reconstruct a clean full name: "<Brand> <Flavor> <NicMgMl>mg/ml <FormFactor>"
- abbreviations_expanded: dict of every abbreviation you expanded {"VT":"Virginia Tobacco"}
- extraction_confidence: your honest confidence 0.0–1.0
- Respond with ONLY valid JSON — no markdown, no explanation.
```

---

### 6.7 Extraction Validation

**File:** `vapor_compliance/normalization/extraction_validator.py`

Full field-by-field validation logic:

#### Brand Validation
```
1. strip() the LLM brand
2. exact lookup in _BRAND_CANONICAL (brand_aliases.json, uppercased)
   → if found and differs from LLM: CORRECTION flag, use dict value
   → if found and matches: CONFIRMED, score = 1.0
3. if not found: compute Jaro-Winkler against all canonicals
   → score ≥ 0.92: CORRECTION (fuzzy), score = 0.85
   → score < 0.92: UNVERIFIED, score = 0.60
```

#### Nicotine Validation
```
1. recompute from nicotine_strength_raw using normalize_nicotine()
2. if both exist and |dict - llm| > 0.1: CORRECTION, use dict value
3. if only one exists: use whichever is available (score = 0.75)
4. if neither: MISSING flag, score = 0.0
```

#### Abbreviation Validation
```
for abbr, expansion in llm_result.abbreviations_expanded.items():
    dict_expansion = abbreviations_json.get(abbr.upper())
    if dict_expansion is None:
        UNVERIFIED — LLM found a new abbreviation (keep with flag), score = 0.70
    elif dict_expansion.lower() == expansion.lower():
        CONFIRMED, score = 1.0
    else:
        CORRECTION — dictionary wins, score = 0.60
```

---

### 6.8 Confidence Rollup

```
overall = 0.40 × normalization_confidence
        + 0.40 × match_confidence
        + 0.20 × rule_confidence

Thresholds:
  overall ≥ 0.75 → auto-classify (no review required)
  0.50 ≤ overall < 0.75 → auto-classify + optional review flag
  overall < 0.50 → REVIEW_REQUIRED (withheld from auto-classification)
```

The rule_confidence component reflects the quality of the evidence used in the compliance decision:
- Both FDA record and state record present with high match: 1.0
- One record missing but state has no registry: 0.80
- Inferred from absence (no records found): 0.60
- Flavor ban applied: reduces by 0.05 (ban is known, but product may be incorrectly classified)

---

## 7. Compliance Rules Engine

**File:** `vapor_compliance/compliance/rules_engine.py`

The rules engine is a pure function `classify(fda_record, state_record, normalized_sku, settings) → ComplianceResult`. No side effects, no ML, no stochastic behavior.

### Decision Matrix

```
┌─────────────┬───────────────────────┬────────────────────────────────────────────┐
│ FDA Status  │ State Status          │ Result                                     │
├─────────────┼───────────────────────┼────────────────────────────────────────────┤
│ AUTHORIZED  │ Listed                │ FEDERAL_LICIT                              │
│ AUTHORIZED  │ Not listed, has dir.  │ GREY_MARKET                                │
│ AUTHORIZED  │ Not listed, no dir.   │ FEDERAL_LICIT                              │
│ AUTHORIZED  │ + Flavor ban applies  │ ILLICIT (overrides LICIT)                  │
│ NOT AUTH    │ Listed                │ POTENTIAL_ILLICIT                          │
│ NOT AUTH    │ Not listed            │ ILLICIT                                    │
│ EITHER      │ confidence < floor    │ REVIEW_REQUIRED                            │
│ UNKNOWN     │ UNKNOWN               │ UNKNOWN                                    │
└─────────────┴───────────────────────┴────────────────────────────────────────────┘
```

### Flavor Ban Logic

```python
if state_record and state_record.flavor_ban_applies:
    if normalized_sku.flavor_category in state_flavor_bans[state]:
        if state_record.enforcement_status == "IN_EFFECT":
            return ILLICIT, f"Flavor ban: {flavor_category} banned in {state} (rule {rule_version})"
```

State flavor ban categories are defined in `flavor_synonyms.json → flavor_ban_categories`:
- California: MENTHOL, FRUIT, DESSERT, SPICE
- Massachusetts: MENTHOL, FRUIT, DESSERT, SPICE
- New York: MENTHOL, FRUIT, DESSERT, SPICE
- New Jersey: MENTHOL, FRUIT, DESSERT, SPICE

Adding a new state ban = edit JSON, no code change.

---

## 8. Configuration & Feature Toggles

**File:** `vapor_compliance/config.py`

All configuration is a single `AlgorithmConfig(BaseSettings)` Pydantic model. Environment variables override defaults at startup. Runtime toggles (via `/algorithms/toggle`) override in-process state.

```env
# Stage toggles
ENABLE_EXACT=true
ENABLE_FUZZY=true
ENABLE_TFIDF=true
ENABLE_BM25=true
ENABLE_EMBEDDING=true          # requires fastembed + faiss-cpu
ENABLE_BEHAVIORAL=true
ENABLE_LLM=true                # Stage 5 adjudication; requires ANTHROPIC_API_KEY
ENABLE_LLM_EXTRACTION=true     # Path A normalization; requires ANTHROPIC_API_KEY

# Confidence thresholds
FUZZY_THRESHOLD=0.85
TFIDF_THRESHOLD=0.75
BM25_THRESHOLD=0.70
EMBEDDING_THRESHOLD=0.75
AUTO_CLASSIFY_MIN=0.75
REVIEW_QUEUE_MIN=0.50
LLM_TRIGGER_MAX=0.69
BRAND_MIN_SCORE=0.90

# Field weights (fuzzy stage)
WEIGHT_BRAND=1.0
WEIGHT_NICOTINE=0.9
WEIGHT_FORM_FACTOR=0.8
WEIGHT_FLAVOR=0.7
WEIGHT_PRODUCT_LINE=0.3

# Rollup weights
ROLLUP_NORMALIZATION=0.40
ROLLUP_MATCH=0.40
ROLLUP_RULE=0.20

# LLM
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-6
LLM_MAX_CALLS_PER_BATCH=500
LLM_BATCH_SIZE=20
LLM_MAX_RETRIES=2
LLM_RETRY_DELAY=2.0

# Embedding
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
FAISS_INDEX_PATH=data/faiss_index.bin
FAISS_META_PATH=data/faiss_meta.json

# Database
DATABASE_URL=postgresql://user:pass@host/db
```

---

## 9. Reference Dictionaries

All dictionaries are versioned JSON files in `vapor_compliance/dictionaries/`. Loaded at module import time. Service restart (or `GET /registry/reload`) picks up changes.

### abbreviations.json
```json
{
  "VT":   "Virginia Tobacco",
  "MNTH": "Menthol",
  "JOOL": "JUUL",
  "BLBRY": "Blueberry",
  "STRWBRY": "Strawberry",
  "ICE": "Ice",
  "DISP": "Disposable"
}
```
Token-level lookup. Keys stored uppercase. Both key and value are checked when expanding.

### brand_aliases.json
```json
{
  "JUUL": ["JOOL", "JUL", "JUUL LABS", "JUUL2"],
  "ELF BAR": ["ELF", "ELFBAR", "EB"],
  "VUSE": ["VUES", "VUS"],
  "NJOY": ["N-JOY", "NJOY ACE"]
}
```
Canonical brand → list of aliases. At load time, every alias is indexed: `alias_upper → canonical`.

### flavor_synonyms.json
```json
{
  "canonical_flavors": {
    "Virginia Tobacco": ["VT", "Virginia Tobac", "VA TOBACCO", "VIRGINIA TOBACO"],
    "Menthol": ["MNTH", "MINT", "COOL MINT", "MENTHOL ICE"]
  },
  "flavor_categories": {
    "TOBACCO": ["Virginia Tobacco", "Golden Tobacco", "RY4"],
    "MENTHOL": ["Menthol"],
    "FRUIT": ["Mango", "Strawberry", "Blueberry", "Watermelon", "Peach", "Grape"]
  },
  "flavor_ban_categories": {
    "CA": ["MENTHOL", "FRUIT", "DESSERT", "SPICE"],
    "MA": ["MENTHOL", "FRUIT", "DESSERT", "SPICE"]
  }
}
```

### unit_mappings.json
```json
{
  "nicotine_to_mg_ml": {
    "5%": 50.0, "3%": 30.0, "1.8%": 18.0,
    "50mg": 50.0, "35mg": 35.0
  },
  "product_type_map": {
    "pod": "POD", "cart": "CARTRIDGE",
    "disp": "DISPOSABLE", "mod": "MOD"
  }
}
```

---

## 10. End-to-End Data Flow

### Single Record (Path A — LLM first)

```
1. POST /compliance/lookup {raw_name: "JOOL VT 5%"}
2. csv_parser / API → RawSKU(sku_id, raw_name, source)
3. normalizer.normalize(raw, use_llm=True)
   a. llm_extractor.extract_one("JOOL VT 5%")
      → MD5 cache miss → Claude API call
      → SKUExtractionResult(brand="JUUL", flavor_canonical="Virginia Tobacco",
                             nicotine_mg_ml=50.0, form_factor="POD", ...)
   b. validator.validate(extraction)
      → brand: "JUUL" exact match → CONFIRMED (1.0)
      → flavor: "Virginia Tobacco" exact canonical → CONFIRMED (1.0)
      → nicotine: raw="5%", dict=50.0, llm=50.0 → CONFIRMED (1.0)
      → overall_confidence = 0.5×0.95 + 0.5×0.975 = 0.963
   c. _build_from_validated → NormalizedSKU(normalization_method="llm+validated")
4. entity_resolver.resolve(normalized_sku)
   a. Stage 1: key="JUUL|Virginia Tobacco|50.0|POD" → hit → MatchResult(conf=1.0)
   b. Stop (conf ≥ AUTO_CLASSIFY_MIN)
5. fda_registry.get_record("CP0001", snapshot_date=today) → fda_approved=True
6. state_registry.get_record("CP0001", "TX") → state_has_registry=False
7. rules_engine.classify(fda=True, state_listed=None, has_registry=False)
   → FEDERAL_LICIT, reason="FDA authorized; TX has no registry"
8. ComplianceResult(status=FEDERAL_LICIT, confidence=0.97)
9. Return JSON response
```

### Batch Processing (Path B — no LLM)

```
1. CSV file loaded → list[RawSKU]
2. normalizer.normalize_batch(raws, use_llm=False)
   → each SKU through Path B (text clean → expand → units → brand → flavor)
3. entity_resolver.resolve(sku) for each normalized SKU
   → Stages 1→2→3→4 as needed per record
4. for each sku × state:
   fda_registry.get_record() + state_registry.get_record()
   rules_engine.classify()
5. DataFrame with one row per (sku_id × state)
6. Write to PostgreSQL + export CSV
```

---

## 11. Error Handling & Resilience

| Failure Mode | Behavior |
|--------------|---------|
| LLM API unavailable | Log warning; fall through to Path B; no exception propagates |
| LLM returns malformed JSON | Log warning; `_parse_single()` returns `None`; Path B runs |
| LLM confidence < 0.20 | Discard result; Path B runs |
| LLM API rate limit / timeout | Retry up to 2× with exponential back-off (2s, 4s); then Path B |
| fastembed model unavailable | Log warning; Stage 3c disabled for this run; TF-IDF + BM25 continue |
| FAISS index not found | Stage 3c disabled; other stages unaffected |
| No match found at any stage | `MatchResult(match_stage=NO_MATCH, confidence=0)` → `UNKNOWN` status |
| Single record exception in batch | Catch, log, emit `{sku_id, status=UNKNOWN, error=...}` row; batch continues |
| FDA record not found | `fda_approved=False`; classification proceeds with that assumption |
| State record not found | `state_listed=False`; `state_has_registry` check determines final status |

---

## 12. Performance Design

### Index Pre-computation

All matching indexes are built **once at startup** from the CPG registry:

| Index | Build time (100K products) | Query time |
|-------|--------------------------|------------|
| Exact (dict) | < 1s | O(1) |
| Fuzzy (no index) | N/A | O(n) — pre-filter on brand initial |
| TF-IDF (sklearn) | ~5s | < 10ms |
| BM25 | ~2s | < 5ms |
| FAISS | ~10s (embed) + ~1s (index) | < 50ms |

### Batch LLM Optimization

- Without LLM: 10,000+ records/minute (CPU-bound stages only)
- With LLM (Path A): throughput = API_calls × 20_SKUs / API_latency
  - At 1s/call and 20 SKUs/call → ~1,200 SKUs/minute
  - MD5 cache eliminates duplicate SKU calls; in practice ≫ 1,200/min for real POS data (many duplicates)

### FAISS Persistence

FAISS index (`faiss_index.bin`) and metadata (`faiss_meta.json`) are persisted to disk at ingestion time. On restart, the index is loaded from disk (< 1s) rather than rebuilt. This is critical for production restart latency.

---

## 13. Security Design

| Concern | Mitigation |
|---------|-----------|
| API key exposure | `ANTHROPIC_API_KEY` in `.env`; `.env` in `.gitignore`; never logged |
| SQL injection | All DB writes use parameterized queries via SQLAlchemy ORM |
| Input validation | FastAPI + Pydantic validate all JSON inputs; reject with HTTP 422 |
| Path traversal (CSV upload) | File uploads written to temp directory; path not user-controlled |
| PII in logs | `raw_name` logged at DEBUG, not INFO; no customer fields logged |
| Dictionary tampering | Dictionaries are read-only at runtime; writes require service restart |

---

## 14. Deployment Architecture

### Docker Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "vapor_compliance.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Environment Variables (runtime)

```env
ANTHROPIC_API_KEY=...       # Optional — enables Path A and Stage 5
DATABASE_URL=...            # PostgreSQL connection string
FAISS_INDEX_PATH=...        # Path to persistent FAISS index
```

### Without LLM (air-gapped / on-premise)

Set `ENABLE_LLM_EXTRACTION=false` and omit `ANTHROPIC_API_KEY`. All functionality except Path A normalization and Stage 5 adjudication is available. This is the minimum-dependency deployment.

### Minimal Dependencies

```bash
# Core (no LLM, no embeddings)
pip install rapidfuzz rank-bm25 scikit-learn pandas pydantic pydantic-settings numpy

# Add LLM normalization + adjudication
pip install anthropic

# Add embedding (Stage 3c)
pip install fastembed faiss-cpu

# Add PDF ingestion
pip install pdfplumber pytesseract pdf2image

# Full API service
pip install fastapi uvicorn sqlalchemy psycopg2-binary
```

---

## 15. Testing Strategy

**File:** `tests/test_pipeline.py`

All tests run without external dependencies (no API key, no database, no fastembed download).

| Test | What it verifies |
|------|-----------------|
| `test_text_cleaner` | Unicode normalization, punctuation stripping, whitespace collapse |
| `test_unit_normalizer` | Nicotine conversion (5%→50, 50mg→50), volume, puff count |
| `test_abbreviation_expander` | Token-level dict expansion, brand/manufacturer resolution |
| `test_flavor_extractor` | 10+ position-variant cases: flavor at start/end/middle, model codes, multi-word flavors |
| `test_extraction_validator` | 4 cases: all-confirmed, nicotine correction, abbreviation correction, novel brand |
| `test_normalizer` | Full Path B normalization (LLM disabled) |
| `test_exact_matcher` | Hash-map hit and miss |
| `test_fuzzy_matcher` | High-similarity hit, brand-disqualification cap |
| `test_tfidf_matcher` | Typo-tolerant match, below-threshold miss |
| `test_bm25_matcher` | Token-based retrieval, no-result case |
| `test_semantic_ensemble` | stage3_predictions breakdown, agreement field, confidence boost |
| `test_full_pipeline` | 24 records across TX, CA, NY: FEDERAL_LICIT, ILLICIT (flavor ban), GREY_MARKET |

**Run:**
```bash
pip install pytest
python -m pytest tests/test_pipeline.py -v
```

All 12 tests pass with no external dependencies.

---

## 16. Design Decisions & Trade-offs

### 16.1 Two-Path Normalizer (LLM + Dictionary)

**Decision:** Run LLM first (Path A), fall back to dictionary (Path B).  
**Rationale:** LLM handles novel abbreviations and highly ambiguous SKUs that no finite dictionary can cover. Dictionary path handles the bulk of well-known products cheaply. Path B is not a degraded mode — for simple, well-known products it is faster and equally accurate.  
**Trade-off:** Path A adds API cost and latency. Mitigated by batch calls (20 SKUs/call) and MD5 cache.

### 16.2 Dictionary Validates LLM — Not the Reverse

**Decision:** When LLM and dictionary disagree, dictionary wins.  
**Rationale:** LLMs make systematic errors on nicotine unit conversion and abbreviation expansion. The dictionary is deterministic and auditable. The `corrections` dict records every override for audit.  
**Trade-off:** Novel values that the LLM correctly identifies but the dictionary doesn't know about are flagged UNVERIFIED rather than silently accepted.

### 16.3 Deterministic Rules Engine

**Decision:** Compliance classification uses only IF/THEN logic; no ML.  
**Rationale:** Compliance decisions must be explainable in regulatory audits and legal proceedings. "The model said so" is not a defensible answer.  
**Trade-off:** Rule coverage must be explicitly maintained. New state regulations require a JSON update (no retraining lag).

### 16.4 Stage 3 Parallel Ensemble vs Sequential Fallback

**Decision:** TF-IDF, BM25, and embedding run in parallel and vote; not sequential (try TF-IDF, if fails try BM25, etc.)  
**Rationale:** Sequential fallback means later stages only see difficult cases that earlier stages failed on. Parallel ensemble allows the strengths of each method to complement each other. BM25 is strong on exact token overlap; TF-IDF handles typos; embeddings handle semantic paraphrase.  
**Trade-off:** All three stages always run (if enabled), even if TF-IDF already has a high-confidence result. Mitigated by the fact that TF-IDF and BM25 are very fast (< 10ms each).

### 16.5 fastembed over sentence-transformers

**Decision:** Use fastembed (ONNX runtime) for embedding inference.  
**Rationale:** No PyTorch dependency; model downloads once and is cached; faster inference; smaller model footprint.  
**Trade-off:** Slightly less flexible than full PyTorch ecosystem. Not a concern for this use case.

### 16.6 Residual Token Method for Flavor

**Decision:** Strip non-flavor tokens before flavor lookup, rather than scanning the full string.  
**Rationale:** Model codes (`BC5000`), product lines (`ACE`, `Alto`), and unit tokens (`50mg`) contain character sequences that match flavor terms. Scanning the full string gives false positives. The residual method achieves > 90% precision on position-variant test cases.  
**Trade-off:** If the stripping pipeline is too aggressive, it may consume the flavor token itself. Mitigated by the full-text fallback (conf 0.60) when the residual is empty.

### 16.7 Brand Score Disqualification Cap

**Decision:** If brand score < 0.90, composite confidence is capped at 0.50.  
**Rationale:** Brand identity is the primary axis of FDA authorization. JUUL and VUSE have completely separate PMTA authorizations. Allowing a JUUL SKU to match a VUSE reference product (because the flavor name is similar) would be a compliance error.  
**Trade-off:** Some legitimate partial matches are penalized. This is acceptable — uncertain brand matches should go to review rather than auto-classify.

---

## 17. Limitations & Known Constraints

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| LLM cost at scale | $0.01–0.05 per 20 SKUs; 1M SKUs = $500–2,500 | Cache eliminates duplicate calls; Path B handles obvious cases |
| fastembed first run | Model download (~33MB) on first startup | Pre-download in Docker image build step |
| Dictionary coverage | Novel brands/flavors not in dict → UNVERIFIED flag | Human review + dictionary feedback loop |
| State directory freshness | Outdated CSV → stale compliance decisions | Operations process: refresh state data at minimum quarterly |
| GREY_MARKET precision | Products with FDA approval but not on state list are classified GREY_MARKET; may be correctly authorized but state list is stale | Temporal versioning; alert on records > 90 days old |
| LLM non-determinism | Same SKU could return different extractions across API calls | MD5 cache ensures idempotency within a session; results should be re-validated on major LLM model version changes |
| No real-time streaming | System processes batches; no sub-second POS streaming | By design; streaming is explicitly out of scope in requirements |
| 50-state directory completeness | Not all 50 states have structured product directories | `state_has_registry=False` for states without directories; `FEDERAL_LICIT` applies where FDA approved |
