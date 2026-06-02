from __future__ import annotations
from pydantic import BaseModel
from typing import Optional
from enum import Enum


class MatchStage(str, Enum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    TFIDF = "tfidf"
    BM25 = "bm25"
    EMBEDDING = "embedding"
    BEHAVIORAL = "behavioral"
    LLM = "llm"
    NO_MATCH = "no_match"


class FieldScore(BaseModel):
    brand: float = 0.0
    flavor: float = 0.0
    nicotine: float = 0.0
    form_factor: float = 0.0
    product_line: float = 0.0
    composite: float = 0.0


class MatchResult(BaseModel):
    query_sku_id: str
    matched_cpg_id: Optional[str] = None
    match_stage: MatchStage = MatchStage.NO_MATCH
    confidence: float = 0.0
    field_scores: Optional[FieldScore] = None
    match_explanation: str = ""
    matched_source: str = ""
    is_review_required: bool = False
    candidates_considered: int = 0
    stage_scores: dict[str, float] = {}    # score at each stage attempted
