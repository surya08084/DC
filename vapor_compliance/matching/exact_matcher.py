from __future__ import annotations
from typing import Optional
from ..models.sku import NormalizedSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage


class ExactMatcher:
    """
    Stage 1 — O(1) hash-map lookup on composite canonical key.
    Key: brand|flavor_canonical|nicotine_mg_ml|form_factor
    Hits ~60% of clean, consistent data.
    """

    def __init__(self) -> None:
        self._index: dict[str, CanonicalProduct] = {}

    def build_index(self, products: list[CanonicalProduct]) -> None:
        self._index.clear()
        for p in products:
            key = self._make_key(p.brand, p.flavor_canonical, p.nicotine_mg_ml, p.form_factor)
            self._index[key] = p

    def _make_key(
        self,
        brand: Optional[str],
        flavor: Optional[str],
        nicotine: Optional[float],
        form_factor: Optional[str],
    ) -> str:
        b = (brand or "").lower().strip()
        f = (flavor or "").lower().strip()
        n = str(round(nicotine, 1)) if nicotine is not None else "?"
        ff = (form_factor or "").lower().strip()
        return f"{b}|{f}|{n}|{ff}"

    def match(self, sku: NormalizedSKU) -> Optional[MatchResult]:
        key = self._make_key(sku.brand, sku.flavor_canonical, sku.nicotine_mg_ml, sku.form_factor)
        hit = self._index.get(key)
        if hit:
            return MatchResult(
                query_sku_id=sku.raw_sku_id,
                matched_cpg_id=hit.cpg_id,
                match_stage=MatchStage.EXACT,
                confidence=1.0,
                match_explanation=f"Exact key match: {key}",
                matched_source=hit.source,
                is_review_required=False,
                candidates_considered=1,
                stage_scores={"exact": 1.0},
            )
        return None
