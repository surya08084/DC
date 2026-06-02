# Requirements Document
## Vapor Tobacco Product Compliance Matching Framework

**Version:** 1.0  
**Date:** 2026-06-02  
**Status:** Approved  
**Scope:** USA Vapor / Electronic Nicotine Delivery Systems (ENDS) market

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Business Context](#2-business-context)
3. [Stakeholders](#3-stakeholders)
4. [Business Requirements](#4-business-requirements)
5. [Functional Requirements](#5-functional-requirements)
6. [Non-Functional Requirements](#6-non-functional-requirements)
7. [Data Requirements](#7-data-requirements)
8. [Compliance & Regulatory Requirements](#8-compliance--regulatory-requirements)
9. [Integration Requirements](#9-integration-requirements)
10. [Audit & Traceability Requirements](#10-audit--traceability-requirements)
11. [User Interface Requirements](#11-user-interface-requirements)
12. [Exclusions & Out-of-Scope](#12-exclusions--out-of-scope)
13. [Assumptions & Dependencies](#13-assumptions--dependencies)
14. [Glossary](#14-glossary)

---

## 1. Executive Summary

Tobacco and vapor product retailers, distributors, and compliance officers operating in the United States must verify that every vapor product they stock, sell, or ship is:

1. **Federally authorized** under the FDA Pre-Market Tobacco Product Application (PMTA) program
2. **State-compliant** in every state where the product is distributed or sold — some states maintain their own authorized-product directories and independently impose flavor bans or enforcement actions

The challenge is that internal Point-of-Sale (POS) systems and STARS shipment data represent product names as highly abbreviated, inconsistent short-form strings (e.g. `"JOOL VT 5%"`, `"ELF BC5K BLBRY ICE DISP 50MG"`). These strings cannot be directly matched against FDA authorization lists or state directories without first being normalized into structured, canonical form.

This document defines the requirements for a framework that automatically:
- Normalizes abbreviated internal SKU names into structured product attributes
- Matches normalized SKUs to canonical product records
- Classifies each product×state pair as licit, illicit, grey-market, or review-required
- Produces a full, auditable decision trail for every classification

---

## 2. Business Context

### 2.1 Regulatory Background

The FDA Center for Tobacco Products (CTP) requires all vapor products sold in the US to have received a Marketing Granted Order (MGO) under PMTA. Products without a valid PMTA authorization are federally illegal to market. The FDA publishes and updates a list of authorized products.

Additionally, individual states may:
- Maintain their own authorized-product directories (formal whitelist/blacklist)
- Impose flavor bans (e.g., California, Massachusetts, New York, New Jersey ban all flavors except tobacco)
- Impose additional age-verification and packaging requirements
- Operate enforcement-only regimes (no directory, but selective prosecution)

A product that is federally authorized may still be state-restricted or state-banned. Conversely, a product on a state list may lack federal authorization. The framework must resolve both dimensions simultaneously.

### 2.2 Operational Pain Points

| Pain Point | Impact |
|------------|--------|
| POS/STARS data uses inconsistent abbreviations | Manual lookup is infeasible at scale |
| Same product has different names across retailers | Simple string matching fails |
| FDA list and state directories updated frequently | Stale snapshots cause compliance errors |
| No single canonical identifier across systems | No direct join between internal and regulatory data |
| Classification differs by state | Per-product rulings are insufficient |
| Regulatory changes happen mid-year | Temporal validity must be tracked |

### 2.3 Business Objectives

- **BO-01:** Eliminate manual compliance lookups for internal SKU data
- **BO-02:** Achieve ≥ 95% classification accuracy on known product records
- **BO-03:** Provide a full audit trail that can be submitted in a regulatory inquiry
- **BO-04:** Reduce analyst review queue to < 5% of total records
- **BO-05:** Support real-time single-record lookup via an API (< 2 seconds)
- **BO-06:** Support bulk batch processing (up to 100,000 records per run)
- **BO-07:** Enable analysts to override, correct, and feed back into the system

---

## 3. Stakeholders

| Role | Responsibility | Requirements Priority |
|------|---------------|----------------------|
| Compliance Officer | Reviews flagged records, signs off on regulatory submissions | Audit trail, override, explainability |
| Data Analyst | Runs batch jobs, investigates anomalies | Batch API, CSV output, review queue |
| Retailer / Distributor | Submits POS/STARS data for compliance check | REST API, webhook, fast response |
| Regulatory Counsel | Defends classifications in legal proceedings | Auditability, rule transparency |
| IT / DevOps | Deploys and operates the system | Configuration, health checks, monitoring |
| Product Manager | Prioritizes features and defines acceptance criteria | Business metrics, SLA |

---

## 4. Business Requirements

### BR-01 — Multi-State Classification
The system **must** classify every product against every state where it is observed in transaction data, producing a per-product, per-state compliance result.

### BR-02 — Non-Deterministic SKU Names
The system **must** handle raw SKU names that are abbreviated, misspelled, inconsistently formatted, or partially encoded (e.g. `"JOOL VT POD 5%"`, `"ELF BAR BC5K BLBRY ICE 50MG DISP"`).

### BR-03 — Explainable Decisions
Every compliance decision **must** include a human-readable reason string that references the specific data evidence used (FDA record, state record, flavor ban rule, confidence score).

### BR-04 — Temporal Validity
The system **must** respect the `effective_from` / `effective_to` dates on all reference records. A product authorized from 2023-01-01 to 2024-12-31 should not be classified as authorized for a snapshot date outside that window.

### BR-05 — Analyst Override
Compliance analysts **must** be able to manually override any automatic classification with a documented reason. Overrides must not be silently overwritten by subsequent re-runs.

### BR-06 — Dictionary-Driven Updates
Reference dictionaries (abbreviations, brand aliases, flavor synonyms) **must** be updatable without code changes or system restarts (via file reload or API endpoint).

### BR-07 — Confidence-Based Routing
Records where the system cannot achieve sufficient matching confidence **must** be automatically routed to a human review queue rather than auto-classified, preventing false compliance decisions.

---

## 5. Functional Requirements

### 5.1 Data Ingestion

| ID | Requirement |
|----|-------------|
| FR-ING-01 | Accept CSV files as input for batch processing of internal POS and STARS shipment data |
| FR-ING-02 | Accept PDF documents as input for state regulatory directory ingestion (with OCR fallback for scanned PDFs) |
| FR-ING-03 | Accept single-record JSON via REST API for real-time lookup |
| FR-ING-04 | Accept Excel (.xlsx) files as an alternative to CSV |
| FR-ING-05 | Support configurable column name mapping (source files use non-standard column headers) |
| FR-ING-06 | Detect and reject duplicate records (same SKU ID) within a single batch |
| FR-ING-07 | Preserve the original raw text in all output — never overwrite the source record |

### 5.2 SKU Normalization — Path B (Dictionary)

| ID | Requirement |
|----|-------------|
| FR-NORM-01 | Clean raw text: lowercase, unicode normalize, strip noise punctuation while preserving `%`, `/`, numeric values |
| FR-NORM-02 | Expand abbreviations token-by-token against a versioned abbreviation dictionary (e.g. `VT` → `Virginia Tobacco`, `MNTH` → `Menthol`) |
| FR-NORM-03 | Resolve brand names via an alias dictionary supporting multiple spelling variants per canonical brand |
| FR-NORM-04 | Resolve manufacturer names separately from brand (a brand may have multiple manufacturer parent companies) |
| FR-NORM-05 | Normalize all nicotine strength values to `mg/ml`: percent × 10 = mg/ml (5% → 50.0), bare `mg` assumed mg/ml |
| FR-NORM-06 | Normalize volume to ml (e.g. `1.5ml`, `1500µl` → `1.5`) |
| FR-NORM-07 | Extract puff count and pack count as separate integer fields |
| FR-NORM-08 | Map product type shorthand to canonical values: `pod`, `cart`, `disp` → `POD`, `CARTRIDGE`, `DISPOSABLE` |
| FR-NORM-09 | Extract flavor using a **position-aware residual token method**: strip brand, model code, unit, and product line tokens, then match n-grams from what remains against a flavor synonym dictionary |
| FR-NORM-10 | Flavor extraction must return: `(canonical_flavor, flavor_category, raw_span, confidence)` where confidence is 1.0 (multi-word residual match), 0.80 (single-word residual), 0.60 (full-text fallback), 0.40 (substring), or 0.0 (not found) |
| FR-NORM-11 | Assign a `normalization_confidence` score (0–1) with defined deductions per missing field |
| FR-NORM-12 | Tag every record with one or more `high_risk_flags` from a defined enumeration |
| FR-NORM-13 | Records with `normalization_confidence < 0.50` must be routed to the review queue before matching |

### 5.3 SKU Normalization — Path A (LLM-First)

| ID | Requirement |
|----|-------------|
| FR-LLM-01 | When `ANTHROPIC_API_KEY` is configured, attempt LLM-first extraction before the dictionary path |
| FR-LLM-02 | Call Claude API with raw SKU name; expect back a structured JSON object with all product attribute fields |
| FR-LLM-03 | LLM must be instructed to always convert nicotine strength to mg/ml (percent × 10 rule enforced in system prompt) |
| FR-LLM-04 | LLM must return a self-assessed `extraction_confidence` (0.0–1.0) and a list of `ambiguities` |
| FR-LLM-05 | Support batch LLM calls: up to 20 SKUs per API call to minimize latency and cost |
| FR-LLM-06 | Cache LLM results by MD5 hash of the raw name: identical SKU names must not trigger duplicate API calls |
| FR-LLM-07 | Retry failed LLM calls up to 2 times with exponential back-off (2s, 4s) before falling back to dictionary path |
| FR-LLM-08 | Any exception during LLM extraction must be caught and logged; dictionary path runs automatically as fallback |
| FR-LLM-09 | If overall confidence from LLM + validation is < 0.20, discard LLM result and use dictionary path |

### 5.4 Extraction Validation

| ID | Requirement |
|----|-------------|
| FR-VAL-01 | After LLM extraction, validate every field against versioned dictionaries with three possible outcomes per field: CONFIRMED, CORRECTION (dictionary wins), or UNVERIFIED (novel value flagged) |
| FR-VAL-02 | Brand validation: exact dictionary lookup → fuzzy (Jaro-Winkler ≥ 0.92) → UNVERIFIED |
| FR-VAL-03 | Flavor validation: exact canonical lookup → synonym lookup → UNVERIFIED |
| FR-VAL-04 | Nicotine validation: recompute from `nicotine_strength_raw` using deterministic converter; if LLM value differs by > 0.1 mg/ml, apply CORRECTION |
| FR-VAL-05 | Abbreviation validation: verify each `{abbr: expansion}` pair against the abbreviation dictionary; dictionary wins on conflicts |
| FR-VAL-06 | Form factor validation: check against allowed set {POD, DISPOSABLE, CARTRIDGE, MOD, E-LIQUID, DEVICE, TANK}; attempt partial match if not found |
| FR-VAL-07 | Compute `validation_confidence` as weighted sum of per-field scores (brand 0.30, flavor 0.25, nicotine 0.25, abbreviations 0.10, form factor 0.10) |
| FR-VAL-08 | `overall_confidence = 0.5 × llm_confidence + 0.5 × validation_confidence` |
| FR-VAL-09 | Record every CORRECTION in a `corrections` dict (`{field: {llm: x, dict: y}}`) for full auditability |

### 5.5 Entity Resolution (Matching)

| ID | Requirement |
|----|-------------|
| FR-MATCH-01 | Match normalized SKUs against a Canonical Product Group (CPG) registry using a 5-stage cascade |
| FR-MATCH-02 | **Stage 1 — Exact:** O(1) hash-map lookup on composite key `{brand}|{flavor}|{nicotine}|{form_factor}`; confidence = 1.0 on hit |
| FR-MATCH-03 | **Stage 2 — Fuzzy:** Field-weighted multi-metric scoring: Jaro-Winkler for brand (prefix-weighted), Token Set Ratio for flavor (word-order invariant), Damerau-Levenshtein for full name (handles transpositions); composite confidence from field weights |
| FR-MATCH-04 | Brand score < `BRAND_MIN_SCORE` (0.90) must cap composite confidence at 0.50 regardless of other field scores |
| FR-MATCH-05 | **Stage 3 — Semantic Ensemble:** Run TF-IDF, BM25, and embedding matchers **in parallel** (not sequential); collect individual predictions; vote by confidence-weighted sum |
| FR-MATCH-06 | Stage 3 ensemble voting: unanimous agreement adds +0.05 confidence boost; majority adds +0.02; disagreement (split) adds 0 |
| FR-MATCH-07 | **Stage 3a — TF-IDF:** Character 2–4-gram TF-IDF (char_wb analyzer) with cosine similarity; handles typos and partial abbreviation matches |
| FR-MATCH-08 | **Stage 3b — BM25:** IDF-weighted token retrieval with per-term importance; scores normalized to [0, 1] |
| FR-MATCH-09 | **Stage 3c — Embedding:** fastembed (BAAI/bge-small-en-v1.5, ONNX-based) + FAISS cosine index; model loaded once at startup, reference embeddings pre-computed at ingestion |
| FR-MATCH-10 | **Stage 4 — Behavioral Re-rank:** Boost or penalize top candidates based on behavioral signals: price deviation, retailer/state distribution overlap (Jaccard), product launch timing alignment |
| FR-MATCH-11 | **Stage 5 — LLM Adjudication:** For records in the confidence range [REVIEW_QUEUE_MIN, LLM_TRIGGER_MAX] (default 0.50–0.69), call Claude API with top-3 candidates and ask for a structured match decision |
| FR-MATCH-12 | Each stage attempted must be logged in `stage_scores`; the winning stage must be recorded as `match_stage` |
| FR-MATCH-13 | If no stage achieves `REVIEW_QUEUE_MIN` (0.50), classify as NO_MATCH and route to review queue |
| FR-MATCH-14 | Stage 3 `stage3_predictions` must record each method's `{cpg_id, confidence, predicted}` for audit |
| FR-MATCH-15 | All Stage 3 algorithms must be individually enable/disable-able via configuration (ENABLE_TFIDF, ENABLE_BM25, ENABLE_EMBEDDING) |

### 5.6 Compliance Classification

| ID | Requirement |
|----|-------------|
| FR-COMP-01 | For each matched SKU, query the FDA registry and each relevant state registry to retrieve `fda_match_flag` and `directory_match_flag` |
| FR-COMP-02 | Apply deterministic IF/THEN rules (no ML) to produce a `ComplianceStatus` per state |
| FR-COMP-03 | Classification rules (in priority order): |
| | `FDA=Y, State=Y` → **FEDERAL_LICIT** |
| | `FDA=Y, State=N, state_has_registry=True` → **GREY_MARKET** |
| | `FDA=Y, State=N, state_has_registry=False` → **FEDERAL_LICIT** (state has no formal directory) |
| | `FDA=N, State=Y` → **POTENTIAL_ILLICIT** |
| | `FDA=N, State=N` → **ILLICIT** |
| | `flavor_ban_applies=True` → **ILLICIT** (overrides any LICIT status) |
| | `confidence < REVIEW_QUEUE_MIN` → **REVIEW_REQUIRED** |
| | `no_match` → **UNKNOWN** |
| FR-COMP-04 | Flavor ban enforcement must be per-state (not global): California, Massachusetts, New York, and New Jersey currently ban MENTHOL, FRUIT, DESSERT, and SPICE categories |
| FR-COMP-05 | Flavor ban rules are data-driven (stored in `flavor_synonyms.json`), not hardcoded, to support future state additions |
| FR-COMP-06 | The `reason` field must reference the specific evidence: which FDA record matched, which state record matched or was absent, which flavor ban rule triggered |
| FR-COMP-07 | Respect temporal validity: apply only records where `snapshot_date` falls within `[effective_from, effective_to]` |
| FR-COMP-08 | State enforcement status must be checked: `ENJOINED` enforcement actions must not be treated as active bans |

### 5.7 Confidence Rollup

| ID | Requirement |
|----|-------------|
| FR-CONF-01 | Overall confidence = `0.40 × normalization_confidence + 0.40 × match_confidence + 0.20 × rule_confidence` |
| FR-CONF-02 | Weights must be configurable via environment variables (`ROLLUP_NORMALIZATION`, `ROLLUP_MATCH`, `ROLLUP_RULE`) |
| FR-CONF-03 | Records with overall confidence ≥ `AUTO_CLASSIFY_MIN` (0.75) are auto-classified without review |
| FR-CONF-04 | Records with overall confidence in [REVIEW_QUEUE_MIN, AUTO_CLASSIFY_MIN) (0.50–0.74) are auto-classified but flagged for optional review |
| FR-CONF-05 | Records with overall confidence < `REVIEW_QUEUE_MIN` (0.50) are assigned `REVIEW_REQUIRED` status and withheld from auto-classification |

### 5.8 Human Review & Override

| ID | Requirement |
|----|-------------|
| FR-REV-01 | Provide a review queue of all `REVIEW_REQUIRED` records |
| FR-REV-02 | Analysts must be able to: Confirm (accept auto-match), Reject (mark as wrong match), Link (specify correct CPG ID), Override status (change compliance classification) |
| FR-REV-03 | Every override must record: `analyst_id`, `decision`, `override_reason`, `timestamp` |
| FR-REV-04 | Overrides must persist across re-runs; subsequent pipeline runs on the same SKU must preserve the override |
| FR-REV-05 | Analyst corrections to abbreviation/brand expansions must be writable back to the corresponding dictionary JSON file |

### 5.9 Registry Management

| ID | Requirement |
|----|-------------|
| FR-REG-01 | Maintain a Canonical Product Group (CPG) registry with a unique `cpg_id` per distinct product variant (brand + flavor + nicotine + form factor) |
| FR-REG-02 | Maintain an FDA registry keyed by `cpg_id` with PMTA authorization status and validity dates |
| FR-REG-03 | Maintain per-state directories for all 50 US states, each keyed by `(cpg_id, state)` |
| FR-REG-04 | Support hot-reload of registry data without full system restart |
| FR-REG-05 | Support three state directory models: WHITELIST (explicit approved list), BLACKLIST (explicit banned list), ENFORCEMENT_ONLY (no formal directory) |

### 5.10 API

| ID | Requirement |
|----|-------------|
| FR-API-01 | `POST /compliance/lookup` — accept a single raw SKU JSON, return compliance results for all relevant states |
| FR-API-02 | `POST /compliance/batch` — accept a CSV file upload, return a CSV/JSON result file |
| FR-API-03 | `GET /algorithms/status` — return current enable/disable state and thresholds for all algorithm stages |
| FR-API-04 | `POST /algorithms/toggle` — enable or disable any algorithm stage at runtime without restart |
| FR-API-05 | `GET /algorithms/thresholds` — return all current confidence threshold values |
| FR-API-06 | All responses must include a `request_id` for log correlation |
| FR-API-07 | Return HTTP 200 with `confidence=0, status=UNKNOWN` rather than HTTP 4xx/5xx for unresolvable SKUs |

---

## 6. Non-Functional Requirements

### 6.1 Performance

| ID | Requirement |
|----|-------------|
| NFR-PERF-01 | Single-record API lookup (Path B, no LLM): ≤ 200ms p99 |
| NFR-PERF-02 | Single-record API lookup (Path A, LLM enabled): ≤ 2000ms p99 (dominated by API latency) |
| NFR-PERF-03 | Batch processing: ≥ 10,000 records/minute without LLM (Stages 1–4 only) |
| NFR-PERF-04 | Embedding index (FAISS) search for 100,000 candidate products: ≤ 50ms per query |
| NFR-PERF-05 | System startup (index build from CPG file): ≤ 60 seconds for 100,000 products |

### 6.2 Accuracy

| ID | Requirement |
|----|-------------|
| NFR-ACC-01 | Precision on JUUL/VUSE/NJOY/ELF BAR known products: ≥ 99% (brand confusion is unacceptable) |
| NFR-ACC-02 | Overall matching accuracy on labeled test set: ≥ 95% |
| NFR-ACC-03 | Auto-classification rate: ≥ 95% of records (review queue ≤ 5%) |
| NFR-ACC-04 | Nicotine unit conversion accuracy: 100% (deterministic — any error is a defect) |

### 6.3 Reliability

| ID | Requirement |
|----|-------------|
| NFR-REL-01 | The system must not crash or produce incorrect results if the LLM API is unavailable; Path B must run automatically |
| NFR-REL-02 | The system must not crash or produce incorrect results if the embedding model is unavailable; TF-IDF and BM25 must continue to operate |
| NFR-REL-03 | Any single-record failure must not abort a batch job; failed records are tagged and included in output with `status=UNKNOWN` |

### 6.4 Maintainability

| ID | Requirement |
|----|-------------|
| NFR-MAIN-01 | All reference data (abbreviations, brand aliases, flavor synonyms, unit mappings) must be in version-controlled JSON files, not in code |
| NFR-MAIN-02 | Adding a new state directory must require only data changes (CSV/JSON), not code changes |
| NFR-MAIN-03 | All algorithm stages must be individually enable/disable-able via a single configuration file (`.env`) |
| NFR-MAIN-04 | Algorithm thresholds must be adjustable via configuration without code changes |

### 6.5 Security

| ID | Requirement |
|----|-------------|
| NFR-SEC-01 | `ANTHROPIC_API_KEY` and `DATABASE_URL` must not be committed to source control; managed via `.env` file |
| NFR-SEC-02 | API endpoints must validate all inputs and reject malformed requests with HTTP 422 |
| NFR-SEC-03 | No PII (customer names, retailer account IDs) in log output at INFO level |

### 6.6 Observability

| ID | Requirement |
|----|-------------|
| NFR-OBS-01 | Every compliance decision must be logged at INFO level with: sku_id, cpg_id, status, confidence, method, state |
| NFR-OBS-02 | LLM API calls must be counted and rate-limited to `LLM_MAX_CALLS_PER_BATCH` per batch job |
| NFR-OBS-03 | System must expose a `GET /health` endpoint for readiness and liveness checks |

---

## 7. Data Requirements

### 7.1 Input Data

#### Internal POS / STARS Data
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sku_id` | string | Yes | Unique identifier from source system |
| `raw_name` | string | Yes | Product name as it appears in POS/STARS |
| `source` | string | Yes | `STARS`, `POS`, or `MANUAL` |
| `manufacturer` | string | No | Manufacturer if available in source |
| `state` | string | No | 2-letter state code of transaction |
| `price` | float | No | Unit price (used for behavioral matching) |
| `retailer_ids` | list[string] | No | Retailer identifiers (used for geo matching) |

#### FDA Authorization List
| Field | Type | Description |
|-------|------|-------------|
| `cpg_id` | string | Canonical product group identifier |
| `fda_match_flag` | bool | True if authorized |
| `pmta_order_number` | string | Official PMTA order reference |
| `authorized_manufacturer` | string | Manufacturer named in PMTA |
| `marketing_granted` | bool | Marketing Granted Order received |
| `effective_from` | date | Authorization start date |
| `effective_to` | date | Authorization end date (null = ongoing) |

#### State Directory Records
| Field | Type | Description |
|-------|------|-------------|
| `cpg_id` | string | Canonical product group identifier |
| `state` | string | 2-letter state code |
| `directory_match_flag` | bool | Listed in state directory |
| `state_has_registry` | bool | State maintains a formal directory |
| `directory_model` | enum | `WHITELIST`, `BLACKLIST`, `ENFORCEMENT_ONLY` |
| `enforcement_status` | enum | `IN_EFFECT`, `PENDING`, `ENJOINED` |
| `flavor_ban_applies` | bool | Flavor ban active for this product-state pair |
| `effective_from` / `effective_to` | date | Validity window |

### 7.2 Output Data

#### Per-Record Compliance Result
| Field | Description |
|-------|-------------|
| `sku_id` | Input record identifier |
| `raw_name` | Original SKU string (preserved) |
| `normalized_name` | Canonical reconstructed name |
| `brand` | Resolved brand |
| `flavor_canonical` | Canonical flavor name |
| `nicotine_mg_ml` | Normalized nicotine strength |
| `form_factor` | Product type |
| `matched_cpg_id` | Best-match CPG identifier |
| `match_stage` | Stage that produced the match |
| `match_confidence` | Match confidence score |
| `state` | Target state |
| `compliance_status` | Final classification |
| `confidence` | Overall confidence |
| `reason` | Human-readable explanation |
| `high_risk_flags` | Comma-separated list of flags |
| `normalization_method` | `dictionary`, `llm+validated`, etc. |
| `is_review_required` | Boolean |
| `snapshot_date` | Date of classification |

### 7.3 Reference Dictionaries

| File | Contents | Update Frequency |
|------|----------|-----------------|
| `abbreviations.json` | Token → full expansion | As new abbreviations are found |
| `brand_aliases.json` | Canonical brand → list of aliases | When new brand variants appear |
| `flavor_synonyms.json` | Canonical flavor, synonyms, categories, per-state bans | When new flavors or bans are enacted |
| `manufacturer_aliases.json` | Canonical manufacturer → aliases | Occasionally |
| `product_lines.json` | Brand-specific and generic product line keywords | When new product lines launch |
| `unit_mappings.json` | Unit conversion rules, product type map | Rarely |

---

## 8. Compliance & Regulatory Requirements

| ID | Requirement |
|----|-------------|
| CR-01 | Classification rules must be deterministic and reproducible: the same input + same reference data must always produce the same output |
| CR-02 | No machine learning model may make the final compliance classification decision; ML is permitted only as a scoring aid in matching stages |
| CR-03 | Every `ComplianceResult` must reference the specific FDA record ID and state record ID (or explicitly note their absence) that drove the decision |
| CR-04 | The system must retain the complete `reason` string for a minimum of 7 years to support regulatory inquiry |
| CR-05 | Override decisions must be separately flagged (`override_flag=True`) and include the analyst's identity and justification |
| CR-06 | Flavor ban rules must be traceable to a specific `rule_version` in the state directory data |
| CR-07 | The system must produce a `snapshot_date` on every record, indicating the regulatory reference data vintage used |
| CR-08 | Any change to the classification rule logic must be version-controlled, reviewed, and the change documented |

---

## 9. Integration Requirements

| ID | Requirement |
|----|-------------|
| IR-01 | The system must accept CSV input from STARS shipment data export (configurable column mapping) |
| IR-02 | The system must accept CSV input from POS system exports (configurable column mapping) |
| IR-03 | The system must write output to a PostgreSQL database for downstream reporting |
| IR-04 | The REST API must be consumable by a standard HTTP client with no special SDK required |
| IR-05 | The system must be deployable as a Docker container |
| IR-06 | The system must be operable without outbound internet access (LLM and embedding model optional) |

---

## 10. Audit & Traceability Requirements

| ID | Requirement |
|----|-------------|
| ATR-01 | Every `ComplianceResult` must include: `normalization_confidence`, `match_confidence`, `rule_confidence`, `overall_confidence` as separate fields |
| ATR-02 | The `high_risk_flags` field must enumerate every risk signal detected during normalization and matching |
| ATR-03 | The `stage_scores` field must show what confidence score each attempted matching stage produced |
| ATR-04 | LLM Path A results must include `corrections` dict showing every field the dictionary overrode |
| ATR-05 | Stage 3 ensemble results must include `stage3_predictions` showing each algorithm's individual prediction |
| ATR-06 | The `normalization_method` field must record which pipeline path produced the normalized record |
| ATR-07 | The complete input `raw_name` must be preserved verbatim in every output record |

---

## 11. User Interface Requirements

### 11.1 REST API (primary interface)
All requirements covered in FR-API section above.

### 11.2 Batch Job Output
- Output must be a CSV file with all fields listed in §7.2, plus one row per SKU × state combination
- A summary row must be appended: total records, auto-classified count, review queue count, count by status, count by state

### 11.3 Review Queue
- Must be queryable via `GET /review/queue?state=CA&min_date=2025-01-01`
- Must support filtering by: `compliance_status`, `state`, `is_review_required`, `normalization_method`, `match_stage`

---

## 12. Exclusions & Out-of-Scope

The following are explicitly excluded from this version:

| Exclusion | Rationale |
|-----------|-----------|
| Non-US markets | Regulatory framework is US FDA / state-specific |
| Cigarettes and smokeless tobacco (non-ENDS) | Separate regulatory pathway; different data sources |
| Hardware accessories (batteries, coils, cases) | Not subject to PMTA |
| Customer PII (shopper identities, loyalty data) | Out of scope; privacy risk |
| Real-time POS transaction streaming | Batch and API only; streaming infrastructure not in scope |
| UI / web application front-end | REST API only; UI is a downstream concern |
| Automated dictionary update from LLM feedback | Dictionary updates require human review and approval |

---

## 13. Assumptions & Dependencies

| # | Assumption / Dependency |
|---|------------------------|
| A-01 | FDA authorization list is available as a CSV export and is updated by the operations team at least monthly |
| A-02 | State directory data for all relevant states is available as CSV or PDF and refreshed at least quarterly |
| A-03 | Internal POS/STARS data exports are available in CSV format with a consistent schema (or configurable mapping) |
| A-04 | `ANTHROPIC_API_KEY` is provisioned and funded; LLM features are optional and degrade gracefully without it |
| A-05 | Python 3.11+ runtime environment |
| A-06 | PostgreSQL 14+ database for persistent storage (SQLite acceptable for development) |
| A-07 | fastembed and FAISS are installable in the deployment environment (embedding stage is optional) |
| A-08 | The Canonical Product Group registry is initially populated manually; ongoing updates are a business process concern |
| A-09 | Flavor ban rules are provided as structured data by the regulatory/legal team; the system enforces them but does not author them |

---

## 14. Glossary

| Term | Definition |
|------|-----------|
| **CPG** | Canonical Product Group — a unique product variant identified by brand + flavor + nicotine + form factor |
| **PMTA** | Pre-Market Tobacco Product Application — FDA process for authorizing new tobacco products |
| **MGO** | Marketing Granted Order — the specific FDA authorization that allows a product to be marketed |
| **ENDS** | Electronic Nicotine Delivery Systems — regulatory term for e-cigarettes, vapes, and similar devices |
| **POS** | Point of Sale — retail transaction system |
| **STARS** | Shipment Tracking and Reporting System — distributor/wholesaler shipment data system |
| **SKU** | Stock Keeping Unit — product identifier used in internal systems |
| **Canonical** | The authoritative, standardized form of a name or value |
| **GREY_MARKET** | FDA-authorized product listed in a state registry but not on the state's approved list |
| **LLM** | Large Language Model (Claude in this system) |
| **FAISS** | Facebook AI Similarity Search — vector index library for fast nearest-neighbor search |
| **BM25** | Best Match 25 — probabilistic information retrieval ranking function |
| **TF-IDF** | Term Frequency–Inverse Document Frequency — text similarity algorithm |
| **Jaro-Winkler** | String similarity metric optimized for short strings, prefix-weighted |
| **Damerau-Levenshtein** | Edit distance metric that also counts transpositions |
| **Residual Token Method** | Flavor extraction technique: strip non-flavor tokens first, then match n-grams on what remains |
| **Path A** | LLM-first normalization path (requires ANTHROPIC_API_KEY) |
| **Path B** | Dictionary-only normalization fallback path |
| **Flavor Ban** | State regulation prohibiting sale of vapor products in non-tobacco flavor categories |
