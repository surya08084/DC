from __future__ import annotations
from pydantic_settings import BaseSettings
from typing import Optional


class AlgorithmConfig(BaseSettings):
    # ── Stage toggles ────────────────────────────────────────────────────────
    ENABLE_EXACT: bool = True
    ENABLE_FUZZY: bool = True
    ENABLE_TFIDF: bool = True
    ENABLE_BM25: bool = True
    ENABLE_EMBEDDING: bool = True       # fastembed – loads once at startup
    ENABLE_BEHAVIORAL: bool = True
    ENABLE_LLM: bool = True             # Claude API – edge cases only

    # ── Per-stage confidence thresholds ──────────────────────────────────────
    FUZZY_THRESHOLD: float = 0.85
    TFIDF_THRESHOLD: float = 0.75
    BM25_THRESHOLD: float = 0.70
    EMBEDDING_THRESHOLD: float = 0.75
    BEHAVIORAL_BOOST_MAX: float = 0.10  # max score adjustment from Stage 4
    LLM_TRIGGER_MAX: float = 0.69       # only call LLM below this confidence

    # ── Overall confidence → action mapping ──────────────────────────────────
    AUTO_CLASSIFY_MIN: float = 0.75     # auto-classify above this
    REVIEW_QUEUE_MIN: float = 0.50      # human review between this and above

    # ── Field weights for composite fuzzy scoring ─────────────────────────────
    WEIGHT_BRAND: float = 1.0
    WEIGHT_NICOTINE: float = 0.9
    WEIGHT_FORM_FACTOR: float = 0.8
    WEIGHT_FLAVOR: float = 0.7
    WEIGHT_PRODUCT_LINE: float = 0.3

    # Brand is disqualifying – if brand score < this, cap overall at 0.50
    BRAND_MIN_SCORE: float = 0.90

    # ── Confidence rollup weights (must sum to 1.0) ───────────────────────────
    ROLLUP_NORMALIZATION: float = 0.40
    ROLLUP_MATCH: float = 0.40
    ROLLUP_RULE: float = 0.20

    # ── Embedding (fastembed) ─────────────────────────────────────────────────
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    FAISS_INDEX_PATH: str = "data/faiss_index.bin"
    FAISS_META_PATH: str = "data/faiss_meta.json"

    # ── LLM (Claude API) ─────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: Optional[str] = None
    LLM_MODEL: str = "claude-sonnet-4-6"
    LLM_MAX_CALLS_PER_BATCH: int = 500

    # ── Storage ───────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://localhost:5432/vapor_compliance"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = AlgorithmConfig()
