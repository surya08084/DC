# Vapor Tobacco Compliance Matching Framework

SKU-level regulatory resolution across FDA authorization and 50 US state directories.

Classifies every vapor/tobacco product in your POS scan and shipment data as **FEDERAL_LICIT**, **GREY_MARKET**, **POTENTIAL_ILLICIT**, **ILLICIT**, **REVIEW_REQUIRED**, or **UNKNOWN** — per product, per state, with a full audit trail.

---

## Table of Contents

1. [What Problem This Solves](#1-what-problem-this-solves)
2. [How It Works — Full Pipeline](#2-how-it-works--full-pipeline)
3. [Architecture Overview](#3-architecture-overview)
4. [Layer 0 — Reference Ingestion](#4-layer-0--reference-ingestion)
5. [Layer 1 — Normalization & Standardization](#5-layer-1--normalization--standardization)
6. [Layer 2 — Entity Resolution (5-Stage Matching)](#6-layer-2--entity-resolution-5-stage-matching)
7. [Stage 3 Deep Dive — Semantic Ensemble](#7-stage-3-deep-dive--semantic-ensemble)
8. [Layer 3 — Regulatory Match Registry](#8-layer-3--regulatory-match-registry)
9. [Layer 4 — Compliance Rules Engine](#9-layer-4--compliance-rules-engine)
10. [Confidence Scoring](#10-confidence-scoring)
11. [Compliance Status Taxonomy](#11-compliance-status-taxonomy)
12. [Data Model](#12-data-model)
13. [Dictionaries & Reference Assets](#13-dictionaries--reference-assets)
14. [Project Structure](#14-project-structure)
15. [Installation](#15-installation)
16. [Configuration](#16-configuration)
17. [Running the Pipeline](#17-running-the-pipeline)
18. [API Reference](#18-api-reference)
19. [Enable / Disable Algorithms](#19-enable--disable-algorithms)
20. [Adding New States or Products](#20-adding-new-states-or-products)
21. [Human Review & Learning Loop](#21-human-review--learning-loop)
22. [Design Decisions](#22-design-decisions)

---

## 1. What Problem This Solves

Vapor tobacco retailers and distributors receive product data in inconsistent, heavily abbreviated formats:

```
Internal POS:    "JOOL VT POD 5%"
FDA Database:    "JUUL Virginia Tobacco, 5% Nicotine by Weight, Cartridge"
CA Directory:    "JUUL Labs - Virginia Tobacco 50mg/ml Pod"
```

The same product, three different naming conventions. A product legal at the federal level may be banned in California. A product on a state whitelist may not have FDA authorization.

This framework:
- **Normalizes** all three data sources into a canonical structured form
- **Matches** your internal SKUs to FDA and state reference records using a multi-algorithm ensemble
- **Classifies** each (product × state) pair as licit, illicit, or grey market
- **Explains** every decision for regulatory audit defense

---

## 2. How It Works — Full Pipeline

```
Internal SKU (raw)
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 1: NORMALIZATION                                  │
│  "JOOL VT POD 5%" → {brand: JUUL, flavor: Virginia      │
│   Tobacco, nicotine_mg_ml: 50.0, form_factor: POD}      │
│                                                          │
│  Steps: clean text → expand abbreviations → unit norm   │
│         → brand/flavor resolution → confidence score    │
└──────────────────────────┬──────────────────────────────┘
                           │  NormalizedSKU
                           ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 2: ENTITY RESOLUTION (5 Stages)                  │
│                                                          │
│  Stage 1  Exact match      hash map, O(1)               │
│  Stage 2  Fuzzy match      Jaro-Winkler + DL + TSR      │
│  Stage 3  Semantic         TF-IDF ─┐                    │
│           Ensemble         BM25   ─┼─ parallel vote     │
│                            Embed  ─┘                    │
│  Stage 4  Behavioral       price/retailer/geo re-rank   │
│  Stage 5  LLM              Claude API edge cases only   │
│                                                          │
│  Output: MatchResult {cpg_id, confidence, stage,        │
│           stage3_predictions{tfidf,bm25,embedding}}     │
└──────────────────────────┬──────────────────────────────┘
                           │  MatchResult
                           ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 3: REGULATORY LOOKUP                             │
│  FDA Registry:   Is CPG on the PMTA authorized list?    │
│  State Registry: Is CPG listed in each state directory? │
│  (temporal check: effective_from / effective_to)        │
└──────────────────────────┬──────────────────────────────┘
                           │  FDARecord + StateRecord[]
                           ▼
┌─────────────────────────────────────────────────────────┐
│  LAYER 4: COMPLIANCE RULES ENGINE                       │
│  Deterministic IF/THEN logic → ComplianceResult         │
│  One result per (SKU × state)                           │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
              ComplianceResult[]
     sku_id × state × status × confidence × audit trail
```

---

## 3. Architecture Overview

```
vapor_compliance/
├── config.py               Algorithm enable/disable flags + thresholds
├── models/                 Pydantic data models
│   ├── sku.py              RawSKU, NormalizedSKU, CanonicalProduct
│   ├── match.py            MatchResult, Stage3Prediction, MatchStage
│   └── compliance.py       ComplianceResult, FDARecord, StateRecord
├── dictionaries/           Versioned reference assets (JSON)
│   ├── abbreviations.json  VT→Virginia Tobacco, MNTH→Menthol, etc.
│   ├── flavor_synonyms.json Canonical flavor + category + state bans
│   ├── brand_aliases.json  JOOL/JUL/JUUL → JUUL, etc.
│   ├── manufacturer_aliases.json
│   └── unit_mappings.json  5%→50mg/ml, pod/cart/disp product types
├── normalization/          Layer 1
│   ├── text_cleaner.py     Lowercase, unicode, punctuation removal
│   ├── unit_normalizer.py  5%=50mg/ml, volume, puff count, product type
│   ├── abbreviation_expander.py Brand/flavor/manufacturer resolution
│   └── normalizer.py       Orchestrates normalization, builds NormalizedSKU
├── matching/               Layer 2
│   ├── exact_matcher.py    Stage 1 — hash map
│   ├── fuzzy_matcher.py    Stage 2 — rapidfuzz field-weighted scoring
│   ├── semantic_ensemble.py Stage 3 — parallel TF-IDF + BM25 + Embedding
│   ├── tfidf_matcher.py    Stage 3a — sklearn char n-gram TF-IDF
│   ├── bm25_matcher.py     Stage 3b — rank_bm25 token retrieval
│   ├── embedding_matcher.py Stage 3c — fastembed + FAISS vector search
│   ├── behavioral_matcher.py Stage 4 — Jaccard/price/geo re-ranker
│   ├── llm_adjudicator.py  Stage 5 — Claude API structured adjudication
│   └── entity_resolver.py  Cascade orchestrator
├── registry/               Layer 3
│   ├── cpg_registry.py     Canonical Product Group store
│   ├── fda_registry.py     FDA PMTA authorized products
│   └── state_registry.py   Per-state directory records (50 states)
├── compliance/             Layer 4
│   ├── rules_engine.py     Deterministic IF/THEN classification
│   └── decision_engine.py  Combines match + FDA + state → results
├── ingestion/              Layer 0
│   ├── csv_parser.py       CSV/Excel → RawSKU list
│   └── pdf_parser.py       PDF tables + OCR fallback
├── pipeline/
│   └── orchestrator.py     End-to-end: CSV in → results DataFrame out
└── api/
    └── app.py              FastAPI — single lookup + bulk upload + toggles
```

---

## 4. Layer 0 — Reference Ingestion

External reference data arrives in mixed formats. The ingestion layer handles all of them.

| Source | Format | Method |
|--------|--------|--------|
| FDA PMTA authorized list | CSV / Excel | `CSVParser` |
| State directories (structured PDFs) | PDF with tables | `pdfplumber` |
| State directories (scanned PDFs) | PDF images | `pytesseract` OCR fallback |
| State web portals | HTML tables | `BeautifulSoup` + `httpx` |
| Internal POS / shipment data | CSV / Excel | `CSVParser` |

**Change detection** — each file is MD5-hashed on load. Re-ingestion only triggers when the hash changes, preventing unnecessary re-indexing.

**Versioned reference store** — every ingestion records a `source_version` and `snapshot_date`. This enables temporal compliance queries: *"what was the compliance status of SKU X in California on March 15, 2024?"*

---

## 5. Layer 1 — Normalization & Standardization

Transforms messy raw text into a structured `NormalizedSKU` object.

### Normalization Steps (in order)

| Step | What it does | Example |
|------|-------------|---------|
| Clean text | Lowercase, unicode normalize, strip noise punctuation | `"JOOL VT POD 5%!"` → `"jool vt pod 5%"` |
| Abbreviation expansion | Dictionary exact lookup on each token | `VT` → `Virginia Tobacco`, `MNTH` → `Menthol` |
| Brand resolution | Brand alias dictionary + partial token match | `JOOL` / `JUL` → `JUUL` |
| Manufacturer resolution | Manufacturer alias dictionary | `ALTRIA` → `Altria Group` |
| Flavor resolution | Canonical flavor + category from synonym dictionary | `strbry ice` → `Strawberry` (category: FRUIT) |
| Unit normalization | Converts all nicotine expressions to `mg/ml` | `5%` → `50.0 mg/ml`, `50mg` → `50.0` |
| Volume normalization | All volumes to ml | `1.5ml` → `1.5` |
| Product type mapping | Maps shorthand to canonical type | `pod/cart/disp` → `POD/CARTRIDGE/DISPOSABLE` |

### Dimensional Equivalence

The framework enforces these unit conversions before any comparison. Never fuzzy-match nicotine strength — it is a legally distinct attribute.

```
5%       = 50.0 mg/ml    (percent × 10 for aqueous nicotine solutions)
50mg     = 50.0 mg/ml    (assumed mg/ml when unit omitted for vape products)
50mg/ml  = 50.0 mg/ml    (already canonical)

5% ≠ 3%   — these are DIFFERENT regulated products
```

### Normalization Confidence

Each `NormalizedSKU` carries a `normalization_confidence` score (0–1). Deductions:

| Missing field | Deduction |
|--------------|-----------|
| Brand unresolved | −0.25 |
| Flavor unresolved | −0.15 |
| Nicotine strength missing | −0.15 |
| Product type missing | −0.10 |

Records with `normalization_confidence < 0.50` are flagged `high_risk` and automatically routed to the review queue before matching even begins.

### High-Risk Flags

The normalizer tags records with flags that carry forward into the audit trail:

- `brand_not_in_dictionary` — brand could not be resolved
- `flavor_not_resolved` — flavor could not be mapped to a canonical form
- `nicotine_not_found` — no nicotine strength detected
- `no_abbreviations_expanded` — pre-expanded name had no dictionary hits (possible LLM over-expansion)

### LLM Pre-Expansion (Copilot Integration)

Your upstream Copilot/LLM can pre-expand raw SKU names into a structured JSON before they reach this pipeline. The normalizer accepts a `pre_expanded_name` argument. It then **validates** the LLM expansion against the abbreviation dictionary:

```
LLM output:  "JUUL Vermont Tobacco 50mg Pod"
Dict says:   VT → Virginia Tobacco (not Vermont)
Flag raised: over_expansion_detected
Method:      "hybrid" (LLM + dictionary)
```

If the LLM and dictionary disagree, the **dictionary wins** and a flag is raised for human review.

---

## 6. Layer 2 — Entity Resolution (5-Stage Matching)

Matches each `NormalizedSKU` to a record in the Canonical Product Group (CPG) registry.

The cascade **stops as soon as** a stage returns `confidence ≥ AUTO_CLASSIFY_MIN` (default 0.75). Lower-confidence results continue to the next stage for a second opinion. Every stage attempted is recorded in `stage_scores`.

### Stage 1 — Exact Match

```python
key = f"{brand}|{flavor_canonical}|{nicotine_mg_ml}|{form_factor}"
hit = index.get(key)
```

- Algorithm: Python dict hash map — O(1) lookup
- Confidence: always 1.0 on hit
- Expected hit rate: ~60% of clean, consistently-named data
- No threshold — exact means exact

### Stage 2 — Fuzzy Match

Field-level matching using different algorithms chosen per field:

| Field | Algorithm | Library | Reason |
|-------|-----------|---------|--------|
| Brand | Jaro-Winkler | `rapidfuzz` | Prefix-weighted — `JUUL` vs `JUULL`. Short strings. |
| Flavor | Token Set Ratio | `rapidfuzz` | Word-order invariant — `Virginia Tobacco JUUL` = `JUUL Virginia Tobacco` |
| Full name | Damerau-Levenshtein | `rapidfuzz` | Handles transpositions — `Menhtol` → `Menthol` |
| Nicotine | **Exact only** after unit norm | — | `5%` and `3%` are legally different products. Never fuzzy. |
| Form factor | Exact only | — | Pod ≠ Disposable |

**Brand is disqualifying.** If the brand score falls below `BRAND_MIN_SCORE` (default 0.90), the overall composite is capped at 0.50 regardless of other field scores. JUUL cannot match to VUSE no matter how similar the flavor name.

**Field weights for composite score:**

```
brand         × 1.0   (disqualifying if wrong)
nicotine      × 0.9   (near-disqualifying)
form_factor   × 0.8
flavor        × 0.7
product_line  × 0.3
```

### Stage 3 — Semantic Ensemble

All three semantic methods run **in parallel**. See [Stage 3 Deep Dive](#7-stage-3-deep-dive--semantic-ensemble) below.

### Stage 4 — Behavioral Re-Ranker

Does not produce new candidates — adjusts the confidence of the Stage 3 winner using auxiliary signals from your internal data:

| Signal | Method | Notes |
|--------|--------|-------|
| Price similarity | Percentage difference | Within 15% → full score; >50% → 0 |
| Retailer overlap | Jaccard similarity on retailer ID sets | `\|A∩B\| / \|A∪B\|` |
| State distribution overlap | Jaccard on state sets | Same geography → higher confidence |
| Manufacturer agreement | Jaro-Winkler | Validates manufacturer name matches |

Max boost: `BEHAVIORAL_BOOST_MAX` (default ±0.10). If signals are bad, it can **reduce** confidence too.

### Stage 5 — LLM Adjudication

Triggered only when `REVIEW_QUEUE_MIN ≤ confidence ≤ LLM_TRIGGER_MAX` (default 0.50–0.69) — edge cases only, expected <5% of volume.

The LLM receives **structured context**, not raw text:

```json
{
  "query":     {"brand": "JUUL", "flavor_canonical": "Virginia Tobacco", "nicotine_mg_ml": 50.0, ...},
  "candidate": {"cpg_id": "CP0001", "brand": "JUUL", "flavor_canonical": "Virginia Tobacco", ...},
  "prior_confidence": 0.63,
  "stage_scores": {"tfidf": 0.61, "bm25": 0.65}
}
```

Returns structured JSON:
```json
{"decision": "SAME", "confidence": 0.85, "reasoning": "Brand, flavor, and strength match; minor name variant."}
```

Decisions: `SAME` → update match, `NOT_SAME` → discard candidate, `UNCERTAIN` → flag for human review.

Volume cap: `LLM_MAX_CALLS_PER_BATCH` (default 500) prevents runaway API costs.

---

## 7. Stage 3 Deep Dive — Semantic Ensemble

This is the most important change from a naive sequential cascade.

### Why Ensemble Over Sequential Fallback?

**Sequential** (old): TF-IDF hits at 0.80 → pipeline stops. BM25 and Embedding never run. If TF-IDF picked the wrong candidate at 0.80, you never know.

**Ensemble** (current): All three run. If TF-IDF says CP0001 at 0.87, BM25 says CP0001 at 0.91, and Embedding says CP0001 at 0.89 — you have three independent signals all agreeing. That consensus is far more reliable than any single signal.

### The Three Methods

**3a — TF-IDF (Character N-gram + Cosine Similarity)**
- Uses character 2–4 grams (`analyzer='char_wb'`)
- Handles typos: `Menhtol` and `Menthol` share most character bigrams
- Handles abbreviations that slipped through the dictionary
- Good at partial brand/flavor matches across token boundaries
- Builds a sparse matrix at index time; query is a single transform + dot product

**3b — BM25 (Token Retrieval)**
- BM25Okapi on whitespace-tokenized product fields
- IDF-weighted: rare brand names (e.g., `SMOORE`) get higher weight than common words (e.g., `tobacco`)
- Scores normalized to [0, 1] by dividing by the max score in the corpus
- Zero model download, pure Python — works in any environment
- Excellent for retrieving candidate sets when the catalog is large

**3c — fastembed + FAISS (Semantic Embedding)**
- Model: `BAAI/bge-small-en-v1.5` (33MB, outperforms `all-MiniLM-L6-v2` on benchmarks)
- Model loads **once at service startup** and stays in memory — no re-download per request
- Reference corpus embeddings are **pre-computed at ingestion time** and stored in a FAISS index on disk
- At match time, only the incoming SKU is embedded (single forward pass)
- FAISS `IndexFlatIP` with L2 normalization = cosine similarity via inner product
- Captures semantic similarity: `Cool Mint` ~ `Arctic Ice` even if token overlap is zero

### Voting Logic

```
Active predictions = {method: (cpg_id, confidence) for each enabled method that produced a result}

For each candidate CPG:
    weighted_votes[cpg] = sum(confidence for methods voting for this cpg)

winner = argmax(weighted_votes)
n_agree = number of methods that voted for winner
n_active = total active methods

if n_agree == n_active:   agreement = "all_agree",  boost = +0.05
elif n_agree > n_active/2: agreement = "majority",   boost = +0.02
else:                       agreement = "split",      boost = 0, flag review

ensemble_confidence = avg(confidence of agreeing methods) + boost
```

Confidence-weighted voting means a high-confidence BM25 score of 0.95 outweighs a low-confidence TF-IDF score of 0.52, not just 1 vote vs 1 vote.

### Stage 3 Output

Every `MatchResult` carries a full breakdown:

```python
result.stage3_predictions = {
    "tfidf":     Stage3Prediction(cpg_id="CP0001", confidence=0.928, predicted=True),
    "bm25":      Stage3Prediction(cpg_id="CP0001", confidence=0.910, predicted=True),
    "embedding": Stage3Prediction(cpg_id="CP0004", confidence=0.720, predicted=True),
}
result.stage3_agreement = "majority"
result.match_stage       = MatchStage.SEMANTIC_ENSEMBLE
result.match_explanation = "Stage3 [majority] tfidf→CP0001(0.93), bm25→CP0001(0.91), embedding→CP0004(0.72) → winner=CP0001 conf=0.952"
```

### Degradation Modes

| Methods enabled | Behaviour | Stage label |
|----------------|-----------|-------------|
| TF-IDF + BM25 + Embedding | Full ensemble, 3-way vote | `semantic_ensemble` |
| TF-IDF + BM25 | 2-way ensemble | `semantic_ensemble` |
| TF-IDF only | Solo mode | `tfidf` |
| None | Stage 3 skipped | — |

---

## 8. Layer 3 — Regulatory Match Registry

After entity resolution produces a `cpg_id`, the decision engine looks up that CPG in two registries.

### FDA Registry

```
CPG_ID → FDARecord {
    fda_match_flag     : bool
    fda_match_id       : "FDA_99123"
    pmta_order_number  : "PM0003236"
    authorized_manufacturer
    effective_from     : date
    effective_to       : date      # null = still valid
    marketing_granted  : bool
    confidence         : float
}
```

Source: FDA's Marketing Granted Orders (MGO) list. Updated when FDA publishes new authorizations or revokes existing ones.

### State Directory Registry

```
(CPG_ID, STATE) → StateRecord {
    directory_match_flag : bool
    directory_match_id   : "TX_77821"
    state_has_registry   : bool    # False for states with no formal directory
    directory_model      : WHITELIST | BLACKLIST | ENFORCEMENT_ONLY
    enforcement_status   : IN_EFFECT | PENDING | ENJOINED
    flavor_ban_applies   : bool
    effective_from / to  : date
    rule_version         : "TX_2024"
}
```

### Temporal Validity

Both registries enforce date-range checks before a record is considered valid:

```python
def is_valid(record, today):
    if record.effective_from and today < record.effective_from:
        return False   # not yet in effect
    if record.effective_to and today > record.effective_to:
        return False   # expired
    return True
```

This means the same CPG can be LICIT in 2024 and ILLICIT in 2025 if its PMTA authorization was revoked — and historical compliance queries return the correct answer for any past date.

---

## 9. Layer 4 — Compliance Rules Engine

Deterministic IF/THEN logic. No machine learning. Every decision is explainable and reproducible.

### Decision Matrix

```
FDA Approved  │ State Listed │ State Has Registry │ Status
──────────────┼──────────────┼────────────────────┼────────────────────
TRUE          │ TRUE         │ any                │ FEDERAL_LICIT
TRUE          │ FALSE        │ TRUE (whitelist)   │ GREY_MARKET
TRUE          │ FALSE        │ FALSE              │ FEDERAL_LICIT *
TRUE          │ not listed   │ TRUE (blacklist)   │ STATE_LICIT
FALSE         │ TRUE         │ any                │ POTENTIAL_ILLICIT
FALSE         │ FALSE        │ TRUE               │ ILLICIT
FALSE         │ FALSE        │ FALSE              │ POTENTIAL_ILLICIT **
any           │ ENJOINED ban │ —                  │ LICIT (enforcement paused)
any           │ flavor ban   │ —                  │ ILLICIT (overrides FDA)
confidence < REVIEW_QUEUE_MIN (0.50) → REVIEW_REQUIRED (regardless of above)
```

`*` A state with no formal directory cannot deny a product — absence of listing ≠ illicit.

`**` Enforcement-only states (no registry) default to POTENTIAL_ILLICIT when FDA approval is also absent.

### Flavor Ban Enforcement

Flavor bans (e.g., California, Massachusetts, New York) are enforced at the **flavor category level**, not product level:

```
flavor_canonical = "Menthol"
flavor_category  = "MENTHOL"
CA bans          = [FRUIT, DESSERT, SPICE, MENTHOL]
→ flavor_ban_applies = True → compliance_status = ILLICIT (overrides state listing)
```

Bans are configured in `dictionaries/flavor_synonyms.json` under `flavor_ban_categories`.

### Confidence Downgrade

Even when evidence says FEDERAL_LICIT, if `match_confidence < AUTO_CLASSIFY_MIN`:

```
status → REVIEW_REQUIRED  (flagged: "match confidence moderate — verify")
```

This prevents auto-classifying LICIT based on a shaky entity match.

---

## 10. Confidence Scoring

Three independent confidence components roll up into a single overall score:

```
overall_confidence = (
    0.40 × normalization_confidence   # how clean was the input?
  + 0.40 × match_confidence           # how strong was the entity match?
  + 0.20 × rule_confidence            # how certain is the regulatory data?
)
```

Rollup weights are configurable (`ROLLUP_NORMALIZATION`, `ROLLUP_MATCH`, `ROLLUP_RULE`).

### Action Thresholds

| Range | Action |
|-------|--------|
| ≥ 0.75 (`AUTO_CLASSIFY_MIN`) | Auto-classify, log match method for audit |
| 0.50–0.74 | Route to **human review queue** |
| 0.50–0.69 + LLM enabled | LLM adjudication attempted first |
| < 0.50 | `REVIEW_REQUIRED` — do not auto-classify |

---

## 11. Compliance Status Taxonomy

| Status | FDA Approved | State Listed | Notes |
|--------|-------------|--------------|-------|
| `FEDERAL_LICIT` | ✓ | ✓ | Full compliance |
| `STATE_LICIT` | ✓ | ✓ | All state conditions met |
| `GREY_MARKET` | ✓ | ✗ | State has registry but product not listed |
| `POTENTIAL_ILLICIT` | ✗ | ✓ | State listed but no FDA authorization |
| `ILLICIT` | ✗ | ✗ | Neither FDA nor state authorization |
| `REVIEW_REQUIRED` | — | — | Confidence too low to auto-classify |
| `UNKNOWN` | — | — | Product identity not resolved |
| `EXCLUDED` | — | — | Out of scope (accessories, non-vape) |

---

## 12. Data Model

### RawSKU — Input

```python
class RawSKU(BaseModel):
    sku_id:       str             # your internal ID
    source:       str             # "POS" | "STARS" | "MANUAL"
    raw_name:     str             # "JOOL VT POD 5%"
    manufacturer: Optional[str]
    state:        Optional[str]   # "TX" — 2-letter code
    price:        Optional[float]
    retailer_ids: list[str]       # for behavioral signals
```

### NormalizedSKU — After Layer 1

```python
class NormalizedSKU(BaseModel):
    brand:                   Optional[str]   # "JUUL"
    flavor_canonical:        Optional[str]   # "Virginia Tobacco"
    flavor_category:         Optional[str]   # "TOBACCO"
    nicotine_mg_ml:          Optional[float] # 50.0
    form_factor:             Optional[str]   # "POD"
    normalized_name:         str             # "JUUL Virginia Tobacco 50.0mg/ml Pod"
    abbreviations_expanded:  dict[str, str]  # {"VT": "Virginia Tobacco"}
    normalization_confidence: float          # 0.0–1.0
    high_risk_flags:         list[str]
```

### MatchResult — After Layer 2

```python
class MatchResult(BaseModel):
    matched_cpg_id:    Optional[str]
    match_stage:       MatchStage          # exact | fuzzy | semantic_ensemble | ...
    confidence:        float
    stage3_predictions: dict[str, Stage3Prediction]  # tfidf / bm25 / embedding
    stage3_agreement:  str                # "all_agree" | "majority" | "split" | "single"
    match_explanation: str                # human-readable explanation
    stage_scores:      dict[str, float]   # score at each stage attempted
    is_review_required: bool
```

### ComplianceResult — Final Output

```python
class ComplianceResult(BaseModel):
    sku_id:             str
    cpg_id:             Optional[str]
    state:              str
    snapshot_date:      date
    fda_approved:       bool
    state_listed:       bool
    compliance_status:  ComplianceStatus
    confidence:         float           # rolled-up overall
    normalization_confidence: float
    match_confidence:   float
    rule_confidence:    float
    reason:             str             # plain-English explanation
    matched_fda_id:     Optional[str]
    matched_state_id:   Optional[str]
    match_method:       str             # "exact" | "fuzzy" | "semantic_ensemble" | ...
    fda_record:         Optional[FDARecord]
    state_record:       Optional[StateRecord]
```

---

## 13. Dictionaries & Reference Assets

All dictionaries are versioned JSON files in `vapor_compliance/dictionaries/`.

### abbreviations.json

Maps abbreviated tokens to full forms. Case-insensitive on lookup.

```json
{
  "VT":   "Virginia Tobacco",
  "MNTH": "Menthol",
  "STRB": "Strawberry",
  "5NIC": "5% Nicotine",
  "JOOL": "JUUL",
  "DISP": "Disposable"
}
```

**Adding new abbreviations:** Add the key-value pair and re-run the pipeline. No code change needed.

### flavor_synonyms.json

Three sections:

- `canonical_flavors` — all known aliases for each canonical flavor name
- `flavor_categories` — which category each flavor belongs to (MENTHOL, TOBACCO, FRUIT, DESSERT, SPICE)
- `flavor_ban_categories` — which categories are banned per state

```json
{
  "canonical_flavors": {
    "Menthol": ["menthol", "cool mint", "arctic ice", "glacier breeze", "icy mint"]
  },
  "flavor_ban_categories": {
    "CA": ["FRUIT", "DESSERT", "SPICE", "MENTHOL"],
    "MA": ["FRUIT", "DESSERT", "SPICE", "MENTHOL"]
  }
}
```

### brand_aliases.json

Maps all known brand name variants to the canonical brand.

```json
{
  "JUUL": ["juul", "jool", "jul", "juuls", "juul labs"],
  "VUSE": ["vuse", "vuse alto", "vuse solo"]
}
```

### unit_mappings.json

Nicotine unit conversions, volume conversions, product type mappings.

---

## 14. Project Structure

```
DC/
├── vapor_compliance/          Main package
├── data/
│   ├── sample_cpg.csv         10 sample canonical products
│   ├── sample_fda.csv         FDA authorization status per CPG
│   ├── sample_state.csv       State directory records (TX, CA, NY)
│   ├── sample_pos.csv         8 sample internal POS SKUs
│   ├── faiss_index.bin        Generated — FAISS embedding index
│   └── faiss_meta.json        Generated — FAISS index metadata
├── tests/
│   └── test_pipeline.py       Smoke tests (no external dependencies)
├── requirements.txt
└── README.md
```

---

## 15. Installation

```bash
# Clone and enter repo
git clone <repo-url>
cd DC

# Install dependencies
pip install -r requirements.txt

# Optional: install embedding + FAISS support
pip install fastembed faiss-cpu

# Optional: install PDF/OCR support
pip install pdfplumber pytesseract pdf2image

# Copy environment template
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY if using LLM adjudication (Stage 5)
```

### Minimal install (no embeddings, no PDF, no LLM)

Stages 1, 2, 3a (TF-IDF), 3b (BM25), and 4 work with just:

```bash
pip install rapidfuzz rank-bm25 scikit-learn pandas pydantic pydantic-settings numpy
```

---

## 16. Configuration

All settings live in `vapor_compliance/config.py` and can be overridden via environment variables or a `.env` file.

### Algorithm Toggles

```env
ENABLE_EXACT=true
ENABLE_FUZZY=true
ENABLE_TFIDF=true
ENABLE_BM25=true
ENABLE_EMBEDDING=true     # requires fastembed + faiss-cpu
ENABLE_BEHAVIORAL=true
ENABLE_LLM=true           # requires ANTHROPIC_API_KEY
```

### Confidence Thresholds

```env
FUZZY_THRESHOLD=0.85        # minimum fuzzy score to accept as a match
TFIDF_THRESHOLD=0.75        # minimum TF-IDF cosine score
BM25_THRESHOLD=0.70         # minimum BM25 normalized score
EMBEDDING_THRESHOLD=0.75    # minimum embedding cosine score
AUTO_CLASSIFY_MIN=0.75      # auto-classify above this overall confidence
REVIEW_QUEUE_MIN=0.50       # route to human review below this
LLM_TRIGGER_MAX=0.69        # call LLM only when confidence is in [0.50, 0.69]
BRAND_MIN_SCORE=0.90        # brand score below this → cap overall at 0.50
```

### Field Weights (Fuzzy Stage)

```env
WEIGHT_BRAND=1.0
WEIGHT_NICOTINE=0.9
WEIGHT_FORM_FACTOR=0.8
WEIGHT_FLAVOR=0.7
WEIGHT_PRODUCT_LINE=0.3
```

### Confidence Rollup Weights

```env
ROLLUP_NORMALIZATION=0.40
ROLLUP_MATCH=0.40
ROLLUP_RULE=0.20
```

### Embedding

```env
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5    # downloaded once, cached
FAISS_INDEX_PATH=data/faiss_index.bin
FAISS_META_PATH=data/faiss_meta.json
```

### LLM

```env
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-6
LLM_MAX_CALLS_PER_BATCH=500
```

---

## 17. Running the Pipeline

### Smoke Tests

```bash
python tests/test_pipeline.py
```

Expected output includes per-stage test results and a full 24-record pipeline run across TX/CA/NY.

### Python — Single SKU Lookup

```python
from vapor_compliance.models.sku import RawSKU
from vapor_compliance.registry.cpg_registry import CPGRegistry
from vapor_compliance.registry.fda_registry import FDARegistry
from vapor_compliance.registry.state_registry import StateRegistry
from vapor_compliance.pipeline.orchestrator import CompliancePipeline

# Load registries
cpg = CPGRegistry(); cpg.load_from_csv("data/sample_cpg.csv")
fda = FDARegistry(); fda.load_from_csv("data/sample_fda.csv")
state = StateRegistry(); state.load_from_csv("data/sample_state.csv")

# Build pipeline (loads all indexes — do once at startup)
pipeline = CompliancePipeline(cpg, fda, state)
pipeline.startup()

# Single SKU lookup across multiple states
raw = RawSKU(sku_id="SKU_001", source="POS", raw_name="JOOL VT POD 5%", state="TX")
results = pipeline.process_sku(raw, target_states=["TX", "CA", "NY"])

for r in results:
    print(f"{r.state}: {r.compliance_status} (confidence={r.confidence:.2f}) — {r.reason}")
```

### Python — Batch CSV Processing

```python
df = pipeline.process_from_csv(
    path="data/sample_pos.csv",
    source="POS",
    target_states=["TX", "CA", "NY", "MA", "FL"],
    output_path="results/compliance_output.csv",
)

summary = pipeline.get_summary(df)
print(summary["by_status"])
# {'FEDERAL_LICIT': 45, 'GREY_MARKET': 12, 'ILLICIT': 3, 'REVIEW_REQUIRED': 5}
```

### Start the API Server

```bash
uvicorn vapor_compliance.api.app:app --host 0.0.0.0 --port 8000 --reload
```

---

## 18. API Reference

### Health Check

```
GET /health
→ {"status": "ok", "pipeline_ready": true}
```

### Single SKU Lookup (Real-Time)

```
POST /compliance/lookup
```

```json
{
  "sku_id": "SKU_001",
  "raw_name": "JOOL VT POD 5%",
  "source": "POS",
  "state": "TX",
  "target_states": ["TX", "CA", "NY"],
  "pre_expanded_name": "JUUL Virginia Tobacco Pod 5%"
}
```

Returns `list[ComplianceResult]` — one per target state.

### Bulk CSV Upload

```
POST /compliance/batch?source=POS&states=TX,CA,NY
Content-Type: multipart/form-data
file: <your_pos_scan.csv>
```

Returns JSON summary + full record list.

### Algorithm Status

```
GET /algorithms/status
→ {"ENABLE_EXACT": true, "ENABLE_TFIDF": true, "ENABLE_BM25": true, ...}
```

### Toggle a Stage (Live, No Restart)

```
POST /algorithms/toggle
{"stage": "bm25", "enabled": false}
```

Valid stages: `exact`, `fuzzy`, `tfidf`, `bm25`, `embedding`, `behavioral`, `llm`

When you disable a Stage 3 method, the ensemble continues with the remaining enabled methods. Disable all three and Stage 3 is skipped.

### View Thresholds

```
GET /algorithms/thresholds
→ {"FUZZY_THRESHOLD": 0.85, "TFIDF_THRESHOLD": 0.75, ...}
```

---

## 19. Enable / Disable Algorithms

The framework is designed for performance benchmarking. Toggle methods on/off to measure their individual contribution.

### Common Configurations

| Config | Enabled Stages | Use Case |
|--------|---------------|---------|
| Full | 1+2+3a+3b+3c+4+5 | Production, highest accuracy |
| No embedding | 1+2+3a+3b+4+5 | Environments without fastembed/FAISS |
| Fast | 1+2+3a+4 | High throughput, TF-IDF only for Stage 3 |
| Minimal | 1+2 | Near-instant, dictionary-quality data only |
| Benchmark TF-IDF | 1+2+3a | Measure TF-IDF contribution |
| Benchmark BM25 | 1+2+3b | Measure BM25 contribution |

### Runtime Toggle via API

```bash
# Disable BM25 from the ensemble at runtime
curl -X POST http://localhost:8000/algorithms/toggle \
  -H "Content-Type: application/json" \
  -d '{"stage": "bm25", "enabled": false}'

# Re-enable
curl -X POST http://localhost:8000/algorithms/toggle \
  -d '{"stage": "bm25", "enabled": true}'
```

Note: Runtime toggles affect the in-process settings only. Restart the server (or set `.env`) to make changes permanent.

---

## 20. Adding New States or Products

### Add a New State Directory

1. Obtain the state's product registry as CSV or PDF
2. If PDF: `from vapor_compliance.ingestion.pdf_parser import PDFParser; df = PDFParser().extract_tables("CA_registry_2025.pdf")`
3. Map columns to the schema: `cpg_id, state, directory_match_flag, directory_match_id, confidence, effective_from, directory_model, enforcement_status, flavor_ban_applies, rule_version`
4. Load: `state_registry.load_from_csv("ca_2025.csv", state="CA", version="CA_2025")`
5. If the state has a flavor ban, add it to `dictionaries/flavor_synonyms.json` under `flavor_ban_categories`

### Add New Products to the CPG Registry

1. Add rows to your CPG CSV with all canonical fields populated
2. Reload the CPG registry and call `pipeline.startup()` to rebuild all indexes (this rebuilds TF-IDF, BM25, and FAISS)
3. If using the API: implement a `POST /registry/reload` endpoint that calls `pipeline.startup()` — this lets you hot-reload without downtime

### Add New Abbreviations

Edit `dictionaries/abbreviations.json`:

```json
{
  "MYABBR": "My Full Expansion"
}
```

No code change. The `AbbreviationExpander` loads the file at import time. Restart the service (or reload the module) to pick up changes.

---

## 21. Human Review & Learning Loop

Records with `is_review_required = True` should be routed to an analyst queue. Analysts can:

1. **Confirm** — the automatic match was correct; increase threshold
2. **Reject** — wrong match; flag this candidate pair as a known false positive
3. **Link** — manually specify the correct CPG ID
4. **Add abbreviation** — if the miss was due to an unknown abbreviation, add it to `abbreviations.json`
5. **Add brand alias** — if the miss was due to an unknown brand variant, add to `brand_aliases.json`

Every analyst decision should be stored with:
- `analyst_id`
- `decision` (confirm/reject/link)
- `override_reason`
- `timestamp`

These feed back into the dictionaries and thresholds — over time the review queue shrinks as the dictionaries grow.

---

## 22. Design Decisions

### Why no ML for compliance classification?

Compliance decisions must be **explainable in a regulatory audit** or legal challenge. "The model said so" is not a defensible answer. The IF/THEN rules engine produces a plain-English `reason` for every decision and can be updated in minutes when legislation changes — no retraining lag.

ML is appropriate only for Stages 3c (embeddings) and 4 (behavioral signals), where it acts as a scoring assistant, never as the final decision maker.

### Why fastembed instead of sentence-transformers?

- **No re-download**: fastembed uses ONNX runtime; the model is downloaded once, cached, and loaded via ONNX (no PyTorch dependency)
- **3× faster inference** on CPU
- **Smaller model** (33MB vs 90MB for `all-MiniLM-L6-v2`)
- **Better benchmarks**: `BAAI/bge-small-en-v1.5` outperforms `all-MiniLM-L6-v2` on MTEB retrieval tasks

Reference corpus embeddings are **pre-computed once at ingestion time** and stored in FAISS. The model is loaded once at service startup and stays in memory. At match time, only the incoming SKU is embedded — a single fast forward pass.

### Why character n-grams for TF-IDF?

`analyzer='char_wb'` with `ngram_range=(2,4)` means the vectorizer works on character sequences, not words. This makes it naturally robust to:
- Typos (`Menhtol` → `Menthol` share most 2/3-grams)
- Concatenated words (`BlueberryIce` vs `Blueberry Ice`)
- Abbreviations that partially survived normalization

### Why Damerau-Levenshtein over standard Levenshtein?

Standard Levenshtein counts insertions, deletions, substitutions. Damerau-Levenshtein also counts **transpositions** (swapping two adjacent characters). Transpositions are the most common human typing error — `Menhtol`, `Tobacoc`, `Mentholk`. They cost 2 edits under standard Levenshtein but only 1 under Damerau-Levenshtein, giving more accurate similarity scores.

### Why is nicotine strength never fuzzy-matched?

`5%` (50mg/ml) and `3%` (30mg/ml) are **distinct regulated products** with different PMTA authorizations. Fuzzy-matching strength would allow a 5% product to match a 3% authorization — a compliance error with real legal consequences. Strength is normalized to `mg/ml` first, then compared with exact equality (tolerance ±0.1mg/ml for floating point).

### Why does brand score cap the composite?

If the brand score is below `BRAND_MIN_SCORE` (0.90), the overall composite is capped at 0.50 regardless of other field scores. A JUUL flavor name does not match a VUSE flavor name, no matter how similar the flavor description. Brand identity is the primary axis of FDA authorization — each PMTA is filed by a specific manufacturer for specific products.

### Why Jaccard for retailer/state overlap?

Jaccard (`|A∩B| / |A∪B|`) is symmetric, intuitive, and handles the case where one set is much larger than the other correctly. It ranges [0, 1] which fits naturally into the confidence score arithmetic. It rewards shared distribution geography, which is a meaningful signal — a product only sold in Texas is unlikely to match a reference product only distributed in New England.
