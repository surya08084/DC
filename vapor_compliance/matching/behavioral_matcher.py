from __future__ import annotations
import math
from typing import Optional
from ..models.sku import RawSKU, NormalizedSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage
from ..config import settings


class BehavioralMatcher:
    """
    Stage 4 — Re-ranker using behavioral/contextual signals.

    This stage does NOT produce new candidates. It adjusts the confidence
    of the best candidate from Stages 1-3 using auxiliary signals:

      - Price similarity     : |p1 - p2| / avg (requires internal + reference price)
      - Retailer overlap     : Jaccard similarity on retailer sets
      - Geographic overlap   : State-level Jaccard on distribution footprint
      - Launch timing        : Exponential decay on date proximity
      - Manufacturer match   : Exact / fuzzy manufacturer name agreement

    A boost of up to BEHAVIORAL_BOOST_MAX (default 0.10) is applied.
    Signals are independent — any signal can confirm or penalize.
    """

    def __init__(self, reference_signals: dict[str, dict] | None = None) -> None:
        # reference_signals: {cpg_id: {price, retailer_ids, states, launch_date, manufacturer}}
        self._signals: dict[str, dict] = reference_signals or {}

    def load_signals(self, signals: dict[str, dict]) -> None:
        self._signals = signals

    def _jaccard(self, set_a: set, set_b: set) -> float:
        if not set_a and not set_b:
            return 0.5   # neutral when no data
        union = set_a | set_b
        if not union:
            return 0.0
        return len(set_a & set_b) / len(union)

    def _price_similarity(self, price_a: Optional[float], price_b: Optional[float]) -> float:
        if price_a is None or price_b is None or price_b == 0:
            return 0.5   # neutral
        diff_ratio = abs(price_a - price_b) / ((price_a + price_b) / 2)
        # tolerance: within 15% → full score; > 50% → 0
        if diff_ratio <= 0.15:
            return 1.0
        if diff_ratio >= 0.50:
            return 0.0
        return 1.0 - ((diff_ratio - 0.15) / 0.35)

    def _launch_timing_score(self, raw_sku_date: Optional[str], ref_date: Optional[str]) -> float:
        """Exponential decay: same month → 1.0, 6+ months apart → ~0.5."""
        if not raw_sku_date or not ref_date:
            return 0.5
        from datetime import date
        try:
            d1 = date.fromisoformat(raw_sku_date)
            d2 = date.fromisoformat(ref_date)
            diff_days = abs((d1 - d2).days)
            return math.exp(-diff_days / 180)
        except Exception:
            return 0.5

    def adjust(
        self,
        result: MatchResult,
        query_sku: RawSKU,
        normalized_sku: NormalizedSKU,
    ) -> MatchResult:
        ref = self._signals.get(result.matched_cpg_id or "")
        if not ref:
            return result

        signal_scores: list[float] = []

        # Price
        ref_price = ref.get("price")
        if query_sku.price or ref_price:
            signal_scores.append(self._price_similarity(query_sku.price, ref_price))

        # Retailer overlap
        query_retailers = set(query_sku.retailer_ids)
        ref_retailers = set(ref.get("retailer_ids", []))
        if query_retailers or ref_retailers:
            signal_scores.append(self._jaccard(query_retailers, ref_retailers))

        # Geographic overlap (state distribution)
        query_states = {query_sku.state} if query_sku.state else set()
        ref_states = set(ref.get("states", []))
        if query_states or ref_states:
            signal_scores.append(self._jaccard(query_states, ref_states))

        # Manufacturer match
        if normalized_sku.manufacturer and ref.get("manufacturer"):
            from rapidfuzz.distance import JaroWinkler
            mfr_sim = JaroWinkler.normalized_similarity(
                normalized_sku.manufacturer.lower(), ref["manufacturer"].lower()
            )
            signal_scores.append(mfr_sim)

        if not signal_scores:
            return result

        avg_signal = sum(signal_scores) / len(signal_scores)
        # Boost is proportional to how good signals are, capped at BEHAVIORAL_BOOST_MAX
        boost = (avg_signal - 0.5) * 2 * settings.BEHAVIORAL_BOOST_MAX
        adjusted = min(1.0, max(0.0, result.confidence + boost))

        result = result.model_copy(deep=True)
        result.confidence = round(adjusted, 3)
        result.stage_scores["behavioral"] = round(avg_signal, 3)
        result.match_explanation += (
            f" | Behavioral boost={boost:+.3f} (signals={signal_scores})"
        )
        if result.match_stage != MatchStage.EXACT:
            result.match_stage = MatchStage.BEHAVIORAL
        return result
