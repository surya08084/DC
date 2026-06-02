from __future__ import annotations
from typing import Optional
import numpy as np
from rank_bm25 import BM25Okapi
from ..models.sku import NormalizedSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage
from ..config import settings


class BM25Matcher:
    """
    Stage 3b — BM25 token retrieval.

    BM25 is excellent for:
      - Token-level retrieval without model download
      - Handling extra words in one description vs another
      - IDF-weighted term importance (rare brand names matter more)

    Used as an alternative retrieval step when TF-IDF confidence is low,
    or as the primary retrieval step when ENABLE_TFIDF is off.
    """

    def __init__(self) -> None:
        self._bm25: Optional[BM25Okapi] = None
        self._catalog: list[CanonicalProduct] = []
        self._corpus_tokens: list[list[str]] = []

    def build_index(self, products: list[CanonicalProduct]) -> None:
        self._catalog = products
        self._corpus_tokens = [self._tokenize(p) for p in products]
        if self._corpus_tokens:
            self._bm25 = BM25Okapi(self._corpus_tokens)

    def _tokenize(self, p: CanonicalProduct) -> list[str]:
        parts = [
            p.brand,
            p.flavor_canonical,
            p.product_type,
            p.form_factor,
            f"{p.nicotine_mg_ml}mg" if p.nicotine_mg_ml is not None else "",
            p.canonical_name,
        ]
        text = " ".join(x for x in parts if x).lower()
        return text.split()

    def _query_tokens(self, sku: NormalizedSKU) -> list[str]:
        parts = [
            sku.brand or "",
            sku.flavor_canonical or sku.flavor or "",
            sku.product_type or "",
            sku.form_factor or "",
            f"{sku.nicotine_mg_ml}mg" if sku.nicotine_mg_ml is not None else "",
            sku.normalized_name,
        ]
        text = " ".join(x for x in parts if x).lower()
        return text.split()

    def _normalize_scores(self, scores: np.ndarray) -> np.ndarray:
        """Min-max normalize BM25 scores to [0, 1]."""
        max_s = scores.max()
        if max_s == 0:
            return scores
        return scores / max_s

    def match(self, sku: NormalizedSKU, threshold: float = None) -> Optional[MatchResult]:
        if self._bm25 is None or not self._catalog:
            return None
        threshold = threshold if threshold is not None else settings.BM25_THRESHOLD

        query_tokens = self._query_tokens(sku)
        raw_scores = np.array(self._bm25.get_scores(query_tokens))
        norm_scores = self._normalize_scores(raw_scores)

        top_idx = int(np.argmax(norm_scores))
        top_score = float(norm_scores[top_idx])

        if top_score >= threshold:
            candidate = self._catalog[top_idx]
            return MatchResult(
                query_sku_id=sku.raw_sku_id,
                matched_cpg_id=candidate.cpg_id,
                match_stage=MatchStage.BM25,
                confidence=round(top_score, 3),
                match_explanation=f"BM25 normalized score={top_score:.3f}",
                matched_source=candidate.source,
                is_review_required=top_score < settings.AUTO_CLASSIFY_MIN,
                candidates_considered=len(self._catalog),
                stage_scores={"bm25": round(top_score, 3)},
            )
        return None

    def retrieve_candidates(self, sku: NormalizedSKU, top_n: int = 10) -> list[tuple[CanonicalProduct, float]]:
        """Return top-N candidates for use as blocking before embedding re-rank."""
        if self._bm25 is None:
            return []
        query_tokens = self._query_tokens(sku)
        raw_scores = np.array(self._bm25.get_scores(query_tokens))
        norm_scores = self._normalize_scores(raw_scores)
        top_indices = norm_scores.argsort()[-top_n:][::-1]
        return [(self._catalog[i], float(norm_scores[i])) for i in top_indices]
