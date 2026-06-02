from __future__ import annotations
from typing import Optional
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler, DamerauLevenshtein
from ..models.sku import NormalizedSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage, FieldScore
from ..config import settings


class FuzzyMatcher:
    """
    Stage 2 — Field-weighted multi-algorithm fuzzy matching.

    Per-field algorithm choice:
      brand       → Jaro-Winkler  (prefix-weighted, short strings)
      flavor      → Token Set Ratio (word-order invariant)
      full name   → Damerau-Levenshtein (handles transpositions)
      nicotine    → Exact-only after unit normalization (legally significant)
      form_factor → Exact-only (pod ≠ disposable)
    """

    def __init__(self, products: list[CanonicalProduct] | None = None) -> None:
        self._catalog: list[CanonicalProduct] = products or []

    def build_index(self, products: list[CanonicalProduct]) -> None:
        self._catalog = products

    def _brand_score(self, a: Optional[str], b: Optional[str]) -> float:
        if not a or not b:
            return 0.0
        return JaroWinkler.normalized_similarity(a.lower(), b.lower())

    def _flavor_score(self, a: Optional[str], b: Optional[str]) -> float:
        if not a or not b:
            return 0.0
        # Token Set Ratio is word-order invariant
        return fuzz.token_set_ratio(a.lower(), b.lower()) / 100.0

    def _full_name_score(self, a: str, b: str) -> float:
        # Damerau-Levenshtein normalized (handles transpositions)
        return DamerauLevenshtein.normalized_similarity(a.lower(), b.lower())

    def _nicotine_score(self, a: Optional[float], b: Optional[float]) -> float:
        """Exact-only — different nicotine strength is a different product."""
        if a is None or b is None:
            return 0.5  # unknown → neutral, not penalizing
        return 1.0 if abs(a - b) < 0.1 else 0.0

    def _form_factor_score(self, a: Optional[str], b: Optional[str]) -> float:
        if not a or not b:
            return 0.5  # neutral if unknown
        return 1.0 if a.lower() == b.lower() else 0.0

    def _composite_score(self, sku: NormalizedSKU, candidate: CanonicalProduct) -> tuple[float, FieldScore]:
        brand_s = self._brand_score(sku.brand, candidate.brand)
        flavor_s = self._flavor_score(sku.flavor_canonical, candidate.flavor_canonical)
        nic_s = self._nicotine_score(sku.nicotine_mg_ml, candidate.nicotine_mg_ml)
        ff_s = self._form_factor_score(sku.form_factor, candidate.form_factor)

        field_scores = FieldScore(
            brand=round(brand_s, 3),
            flavor=round(flavor_s, 3),
            nicotine=round(nic_s, 3),
            form_factor=round(ff_s, 3),
        )

        # Brand is disqualifying
        if brand_s < settings.BRAND_MIN_SCORE:
            composite = brand_s * 0.5   # cap at 0.5 max
        else:
            w = settings
            total_w = w.WEIGHT_BRAND + w.WEIGHT_NICOTINE + w.WEIGHT_FORM_FACTOR + w.WEIGHT_FLAVOR
            composite = (
                brand_s * w.WEIGHT_BRAND
                + nic_s * w.WEIGHT_NICOTINE
                + ff_s * w.WEIGHT_FORM_FACTOR
                + flavor_s * w.WEIGHT_FLAVOR
            ) / total_w

        field_scores.composite = round(composite, 3)
        return composite, field_scores

    def match(self, sku: NormalizedSKU, threshold: float = None) -> Optional[MatchResult]:
        if not self._catalog:
            return None
        threshold = threshold if threshold is not None else settings.FUZZY_THRESHOLD

        best_score = 0.0
        best_candidate: Optional[CanonicalProduct] = None
        best_fields: Optional[FieldScore] = None

        for candidate in self._catalog:
            score, fields = self._composite_score(sku, candidate)
            if score > best_score:
                best_score = score
                best_candidate = candidate
                best_fields = fields

        if best_candidate and best_score >= threshold:
            return MatchResult(
                query_sku_id=sku.raw_sku_id,
                matched_cpg_id=best_candidate.cpg_id,
                match_stage=MatchStage.FUZZY,
                confidence=round(best_score, 3),
                field_scores=best_fields,
                match_explanation=(
                    f"Fuzzy: brand={best_fields.brand:.2f}, "
                    f"flavor={best_fields.flavor:.2f}, "
                    f"nic={best_fields.nicotine:.2f}, "
                    f"ff={best_fields.form_factor:.2f}"
                ),
                matched_source=best_candidate.source,
                is_review_required=best_score < settings.AUTO_CLASSIFY_MIN,
                candidates_considered=len(self._catalog),
                stage_scores={"fuzzy": round(best_score, 3)},
            )
        return None
