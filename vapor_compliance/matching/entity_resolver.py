from __future__ import annotations
import logging
from typing import Optional
from ..models.sku import NormalizedSKU, RawSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage
from ..config import settings
from .exact_matcher import ExactMatcher
from .fuzzy_matcher import FuzzyMatcher
from .tfidf_matcher import TFIDFMatcher
from .bm25_matcher import BM25Matcher
from .embedding_matcher import EmbeddingMatcher
from .behavioral_matcher import BehavioralMatcher
from .llm_adjudicator import LLMAdjudicator

logger = logging.getLogger(__name__)


class EntityResolver:
    """
    Orchestrates the 5-stage matching cascade.

    Cascade stops as soon as a stage returns confidence ≥ AUTO_CLASSIFY_MIN.
    Lower-confidence results continue to next stage for a second opinion.
    Behavioral stage (Stage 4) is always a re-ranker, not a standalone stage.
    LLM (Stage 5) is triggered only for edge cases below LLM_TRIGGER_MAX.

    Enable/disable each stage via settings flags — changes take effect
    without restart when settings are reloaded.
    """

    def __init__(self, behavioral_signals: dict[str, dict] | None = None) -> None:
        self.exact     = ExactMatcher()
        self.fuzzy     = FuzzyMatcher()
        self.tfidf     = TFIDFMatcher()
        self.bm25      = BM25Matcher()
        self.embedding = EmbeddingMatcher()
        self.behavioral = BehavioralMatcher(behavioral_signals)
        self.llm       = LLMAdjudicator()
        self._catalog: list[CanonicalProduct] = []

    def build_all_indexes(self, products: list[CanonicalProduct]) -> None:
        """Call once at startup (or after ingestion) to build all indexes."""
        self._catalog = products
        self.exact.build_index(products)
        self.fuzzy.build_index(products)
        self.tfidf.build_index(products)
        self.bm25.build_index(products)
        if settings.ENABLE_EMBEDDING:
            self.embedding.build_index(products)
        logger.info("EntityResolver: all indexes built for %d products", len(products))

    def _get_candidate_by_cpg_id(self, cpg_id: str) -> Optional[CanonicalProduct]:
        for p in self._catalog:
            if p.cpg_id == cpg_id:
                return p
        return None

    def resolve(self, sku: NormalizedSKU, raw_sku: RawSKU | None = None) -> MatchResult:
        best: Optional[MatchResult] = None

        # ── Stage 1: Exact ──────────────────────────────────────────────────
        if settings.ENABLE_EXACT:
            result = self.exact.match(sku)
            if result:
                best = result
                if best.confidence >= settings.AUTO_CLASSIFY_MIN:
                    return self._apply_behavioral(best, raw_sku, sku)

        # ── Stage 2: Fuzzy ──────────────────────────────────────────────────
        if settings.ENABLE_FUZZY:
            result = self.fuzzy.match(sku)
            if result and (best is None or result.confidence > best.confidence):
                best = result
            if best and best.confidence >= settings.AUTO_CLASSIFY_MIN:
                return self._apply_behavioral(best, raw_sku, sku)

        # ── Stage 3a: TF-IDF ────────────────────────────────────────────────
        if settings.ENABLE_TFIDF:
            result = self.tfidf.match(sku)
            if result and (best is None or result.confidence > best.confidence):
                best = result
            if best and best.confidence >= settings.AUTO_CLASSIFY_MIN:
                return self._apply_behavioral(best, raw_sku, sku)

        # ── Stage 3b: BM25 ──────────────────────────────────────────────────
        if settings.ENABLE_BM25:
            result = self.bm25.match(sku)
            if result and (best is None or result.confidence > best.confidence):
                best = result
            if best and best.confidence >= settings.AUTO_CLASSIFY_MIN:
                return self._apply_behavioral(best, raw_sku, sku)

        # ── Stage 3c: Embedding ─────────────────────────────────────────────
        if settings.ENABLE_EMBEDDING:
            result = self.embedding.match(sku)
            if result and (best is None or result.confidence > best.confidence):
                best = result
            if best and best.confidence >= settings.AUTO_CLASSIFY_MIN:
                return self._apply_behavioral(best, raw_sku, sku)

        # Apply behavioral re-ranking before LLM decision
        if best and settings.ENABLE_BEHAVIORAL and raw_sku:
            best = self.behavioral.adjust(best, raw_sku, sku)
            if best.confidence >= settings.AUTO_CLASSIFY_MIN:
                return best

        # ── Stage 5: LLM adjudication (edge cases) ──────────────────────────
        if (
            settings.ENABLE_LLM
            and best is not None
            and settings.REVIEW_QUEUE_MIN <= best.confidence <= settings.LLM_TRIGGER_MAX
        ):
            candidate = self._get_candidate_by_cpg_id(best.matched_cpg_id or "")
            if candidate:
                llm_result = self.llm.adjudicate(sku, candidate, best)
                if llm_result and (llm_result.confidence > (best.confidence if best else 0)):
                    best = llm_result

        # ── No match / REVIEW ───────────────────────────────────────────────
        if best is None:
            return MatchResult(
                query_sku_id=sku.raw_sku_id,
                match_stage=MatchStage.NO_MATCH,
                confidence=0.0,
                match_explanation="No match found across all enabled stages",
                is_review_required=True,
            )

        best.is_review_required = best.confidence < settings.AUTO_CLASSIFY_MIN
        return best

    def _apply_behavioral(
        self,
        result: MatchResult,
        raw_sku: RawSKU | None,
        normalized_sku: NormalizedSKU,
    ) -> MatchResult:
        if settings.ENABLE_BEHAVIORAL and raw_sku:
            return self.behavioral.adjust(result, raw_sku, normalized_sku)
        return result
