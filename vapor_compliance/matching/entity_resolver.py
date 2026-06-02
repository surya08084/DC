from __future__ import annotations
import logging
from typing import Optional
from ..models.sku import NormalizedSKU, RawSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage
from ..config import settings
from .exact_matcher import ExactMatcher
from .fuzzy_matcher import FuzzyMatcher
from .semantic_ensemble import SemanticEnsemble
from .behavioral_matcher import BehavioralMatcher
from .llm_adjudicator import LLMAdjudicator

logger = logging.getLogger(__name__)


class EntityResolver:
    """
    5-stage matching cascade.

    Stage 1  Exact match         — hash map, O(1)
    Stage 2  Fuzzy match         — Jaro-Winkler + Damerau-Levenshtein + Token Set Ratio
    Stage 3  Semantic Ensemble   — TF-IDF + BM25 + fastembed run IN PARALLEL,
                                   results combined via confidence-weighted voting.
                                   All three methods report their individual prediction;
                                   the ensemble winner is the consensus or highest-scorer.
    Stage 4  Behavioral re-rank  — Jaccard / price / geography signals adjust Stage 3 confidence
    Stage 5  LLM adjudication    — Claude API for edge cases only (< 5% of volume)

    Stage 3 is always an ensemble when two or more of ENABLE_TFIDF, ENABLE_BM25,
    ENABLE_EMBEDDING are True. When only one is enabled it degrades to solo mode
    and the match_stage label reflects which method ran.
    """

    def __init__(self, behavioral_signals: dict[str, dict] | None = None) -> None:
        self.exact      = ExactMatcher()
        self.fuzzy      = FuzzyMatcher()
        self.stage3     = SemanticEnsemble()     # replaces sequential 3a/3b/3c
        self.behavioral = BehavioralMatcher(behavioral_signals)
        self.llm        = LLMAdjudicator()
        self._catalog: list[CanonicalProduct] = []

    def build_all_indexes(self, products: list[CanonicalProduct]) -> None:
        """Call once at startup or after each ingestion run."""
        self._catalog = products
        self.exact.build_index(products)
        self.fuzzy.build_index(products)
        self.stage3.build_index(products)        # builds tfidf + bm25 + embedding indexes
        logger.info("EntityResolver: all indexes built for %d products", len(products))

    def _get_candidate(self, cpg_id: str) -> Optional[CanonicalProduct]:
        for p in self._catalog:
            if p.cpg_id == cpg_id:
                return p
        return None

    def resolve(self, sku: NormalizedSKU, raw_sku: RawSKU | None = None) -> MatchResult:
        best: Optional[MatchResult] = None

        # ── Stage 1: Exact ────────────────────────────────────────────────────
        if settings.ENABLE_EXACT:
            result = self.exact.match(sku)
            if result:
                best = result
                if best.confidence >= settings.AUTO_CLASSIFY_MIN:
                    return self._apply_behavioral(best, raw_sku, sku)

        # ── Stage 2: Fuzzy ────────────────────────────────────────────────────
        if settings.ENABLE_FUZZY:
            result = self.fuzzy.match(sku)
            if result and (best is None or result.confidence > best.confidence):
                best = result
            if best and best.confidence >= settings.AUTO_CLASSIFY_MIN:
                return self._apply_behavioral(best, raw_sku, sku)

        # ── Stage 3: Semantic Ensemble (TF-IDF + BM25 + Embedding) ───────────
        #   All enabled methods run in parallel. Their predictions are combined
        #   via confidence-weighted voting. The result carries a per-method
        #   breakdown in stage3_predictions so you can see:
        #     "tfidf→CP0001(0.87), bm25→CP0001(0.91), embedding→CP0001(0.89)"
        any_stage3_enabled = settings.ENABLE_TFIDF or settings.ENABLE_BM25 or settings.ENABLE_EMBEDDING
        if any_stage3_enabled:
            result = self.stage3.match(sku)
            if result and (best is None or result.confidence > best.confidence):
                best = result
            if best and best.confidence >= settings.AUTO_CLASSIFY_MIN:
                return self._apply_behavioral(best, raw_sku, sku)

        # ── Stage 4: Behavioral re-ranker ─────────────────────────────────────
        if best and settings.ENABLE_BEHAVIORAL and raw_sku:
            best = self.behavioral.adjust(best, raw_sku, sku)
            if best.confidence >= settings.AUTO_CLASSIFY_MIN:
                return best

        # ── Stage 5: LLM adjudication (edge cases) ───────────────────────────
        if (
            settings.ENABLE_LLM
            and best is not None
            and settings.REVIEW_QUEUE_MIN <= best.confidence <= settings.LLM_TRIGGER_MAX
        ):
            candidate = self._get_candidate(best.matched_cpg_id or "")
            if candidate:
                llm_result = self.llm.adjudicate(sku, candidate, best)
                if llm_result and llm_result.confidence > best.confidence:
                    best = llm_result

        # ── No match ──────────────────────────────────────────────────────────
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
