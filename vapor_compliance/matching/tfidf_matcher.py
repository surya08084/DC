from __future__ import annotations
from typing import Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from ..models.sku import NormalizedSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage
from ..config import settings


class TFIDFMatcher:
    """
    Stage 3a — TF-IDF with character n-grams + cosine similarity.

    Character 2-4 grams handle:
      - Typos (Menhtol → Menthol)
      - Abbreviations that slipped through dictionary
      - Partial brand/flavor matches

    Used as a retrieval step (top-N candidates) before re-ranking.
    """

    def __init__(self) -> None:
        self._vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(2, 4),
            min_df=1,
            sublinear_tf=True,
        )
        self._matrix = None
        self._catalog: list[CanonicalProduct] = []
        self._corpus: list[str] = []

    def build_index(self, products: list[CanonicalProduct]) -> None:
        self._catalog = products
        self._corpus = [self._to_text(p) for p in products]
        if self._corpus:
            self._matrix = self._vectorizer.fit_transform(self._corpus)

    def _to_text(self, p: CanonicalProduct) -> str:
        parts = [
            p.brand,
            p.flavor_canonical,
            p.product_type,
            p.form_factor,
            f"{p.nicotine_mg_ml}mg" if p.nicotine_mg_ml is not None else "",
            p.canonical_name,
        ]
        return " ".join(x for x in parts if x)

    def _query_text(self, sku: NormalizedSKU) -> str:
        parts = [
            sku.brand or "",
            sku.flavor_canonical or sku.flavor or "",
            sku.product_type or "",
            sku.form_factor or "",
            f"{sku.nicotine_mg_ml}mg" if sku.nicotine_mg_ml is not None else "",
            sku.normalized_name,
        ]
        return " ".join(x for x in parts if x)

    def match(self, sku: NormalizedSKU, top_n: int = 5, threshold: float = None) -> Optional[MatchResult]:
        if self._matrix is None or not self._catalog:
            return None
        threshold = threshold if threshold is not None else settings.TFIDF_THRESHOLD

        query_vec = self._vectorizer.transform([self._query_text(sku)])
        scores = cosine_similarity(query_vec, self._matrix).flatten()

        top_idx = int(np.argmax(scores))
        top_score = float(scores[top_idx])

        if top_score >= threshold:
            candidate = self._catalog[top_idx]
            return MatchResult(
                query_sku_id=sku.raw_sku_id,
                matched_cpg_id=candidate.cpg_id,
                match_stage=MatchStage.TFIDF,
                confidence=round(top_score, 3),
                match_explanation=f"TF-IDF char n-gram cosine={top_score:.3f}",
                matched_source=candidate.source,
                is_review_required=top_score < settings.AUTO_CLASSIFY_MIN,
                candidates_considered=len(self._catalog),
                stage_scores={"tfidf": round(top_score, 3)},
            )
        return None

    def retrieve_candidates(self, sku: NormalizedSKU, top_n: int = 10) -> list[tuple[CanonicalProduct, float]]:
        """Return top-N candidates with scores for use as blocking step."""
        if self._matrix is None:
            return []
        query_vec = self._vectorizer.transform([self._query_text(sku)])
        scores = cosine_similarity(query_vec, self._matrix).flatten()
        top_indices = scores.argsort()[-top_n:][::-1]
        return [(self._catalog[i], float(scores[i])) for i in top_indices]
