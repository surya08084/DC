from __future__ import annotations
import logging
from collections import Counter
from typing import Optional

from ..models.sku import NormalizedSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage, Stage3Prediction
from ..config import settings
from .tfidf_matcher import TFIDFMatcher
from .bm25_matcher import BM25Matcher
from .embedding_matcher import EmbeddingMatcher

logger = logging.getLogger(__name__)

# Consensus bonus applied when all enabled methods agree on the same CPG
_CONSENSUS_ALL_BOOST = 0.05
_CONSENSUS_MAJORITY_BOOST = 0.02


class SemanticEnsemble:
    """
    Stage 3 — Parallel ensemble of TF-IDF, BM25, and fastembed.

    All three enabled methods run independently on the same query.
    Their predictions are combined via weighted voting:

      Agreement type     → Effect on confidence
      ─────────────────────────────────────────
      All agree (3/3)    → weighted avg + 0.05 boost
      Majority (2/3)     → weighted avg of agreeing pair + 0.02 boost
      Split (all differ) → highest individual score, no boost, review flagged

    The MatchResult carries a stage3_predictions breakdown so downstream
    consumers can see exactly what each method predicted:

      stage3_predictions = {
          "tfidf":     Stage3Prediction(cpg_id="CP0001", confidence=0.87, predicted=True),
          "bm25":      Stage3Prediction(cpg_id="CP0001", confidence=0.91, predicted=True),
          "embedding": Stage3Prediction(cpg_id="CP0001", confidence=0.89, predicted=True),
      }
      stage3_agreement = "all_agree"
      match_stage       = MatchStage.SEMANTIC_ENSEMBLE

    When only one method is enabled the stage degrades gracefully:
      stage3_agreement = "single"
      match_stage       = MatchStage.TFIDF | BM25 | EMBEDDING (solo label)
    """

    def __init__(self) -> None:
        self.tfidf     = TFIDFMatcher()
        self.bm25      = BM25Matcher()
        self.embedding = EmbeddingMatcher()

    def build_index(self, products: list[CanonicalProduct]) -> None:
        if settings.ENABLE_TFIDF:
            self.tfidf.build_index(products)
        if settings.ENABLE_BM25:
            self.bm25.build_index(products)
        if settings.ENABLE_EMBEDDING:
            self.embedding.build_index(products)

    def match(self, sku: NormalizedSKU) -> Optional[MatchResult]:
        """
        Run all enabled Stage 3 methods and return an ensemble MatchResult.
        Returns None if no method produces a result above its threshold.
        """
        raw_predictions: dict[str, tuple[Optional[str], float]] = {}

        # ── Collect predictions from each enabled method ──────────────────
        if settings.ENABLE_TFIDF:
            r = self.tfidf.match(sku, threshold=settings.TFIDF_THRESHOLD)
            raw_predictions["tfidf"] = (r.matched_cpg_id, r.confidence) if r else (None, 0.0)

        if settings.ENABLE_BM25:
            r = self.bm25.match(sku, threshold=settings.BM25_THRESHOLD)
            raw_predictions["bm25"] = (r.matched_cpg_id, r.confidence) if r else (None, 0.0)

        if settings.ENABLE_EMBEDDING:
            r = self.embedding.match(sku, threshold=settings.EMBEDDING_THRESHOLD)
            raw_predictions["embedding"] = (r.matched_cpg_id, r.confidence) if r else (None, 0.0)

        # Filter to methods that actually produced a result
        active = {m: (cpg, sc) for m, (cpg, sc) in raw_predictions.items() if cpg is not None}

        if not active:
            return None

        # ── Build Stage3Prediction breakdown (all methods, including misses) ──
        stage3_breakdown: dict[str, Stage3Prediction] = {}
        for method, (cpg, sc) in raw_predictions.items():
            stage3_breakdown[method] = Stage3Prediction(
                cpg_id=cpg,
                confidence=round(sc, 3),
                predicted=cpg is not None,
            )

        # ── Ensemble voting ───────────────────────────────────────────────────
        winner_cpg, ensemble_confidence, agreement, methods_used = self._vote(active)

        # ── Determine stage label ─────────────────────────────────────────────
        if len(active) > 1:
            stage = MatchStage.SEMANTIC_ENSEMBLE
        else:
            solo_method = next(iter(active))
            stage = {"tfidf": MatchStage.TFIDF, "bm25": MatchStage.BM25,
                     "embedding": MatchStage.EMBEDDING}.get(solo_method, MatchStage.SEMANTIC_ENSEMBLE)

        # ── Build human-readable explanation ──────────────────────────────────
        parts = []
        for m, pred in stage3_breakdown.items():
            if pred.predicted:
                parts.append(f"{m}→{pred.cpg_id}({pred.confidence:.2f})")
            else:
                parts.append(f"{m}→miss")
        explanation = (
            f"Stage3 [{agreement}] "
            + ", ".join(parts)
            + f" → winner={winner_cpg} conf={ensemble_confidence:.3f}"
        )

        return MatchResult(
            query_sku_id=sku.raw_sku_id,
            matched_cpg_id=winner_cpg,
            match_stage=stage,
            confidence=round(ensemble_confidence, 3),
            match_explanation=explanation,
            matched_source="ensemble",
            is_review_required=(
                ensemble_confidence < settings.AUTO_CLASSIFY_MIN
                or agreement == "split"
            ),
            candidates_considered=len(active),
            stage_scores={m: sc for m, (_, sc) in active.items()},
            stage3_predictions=stage3_breakdown,
            stage3_agreement=agreement,
        )

    # ── Voting logic ──────────────────────────────────────────────────────────

    def _vote(
        self, active: dict[str, tuple[str, float]]
    ) -> tuple[str, float, str, list[str]]:
        """
        Returns (winner_cpg, ensemble_confidence, agreement_label, methods_used).

        Weighting: each method's vote is weighted by its confidence score,
        not just a count. This prevents a low-confidence method from overriding
        two high-confidence methods that agree.
        """
        if len(active) == 1:
            m, (cpg, sc) = next(iter(active.items()))
            return cpg, sc, "single", [m]

        # Accumulate weighted confidence per CPG
        cpg_weights: dict[str, float] = {}
        cpg_voters: dict[str, list[str]] = {}
        for method, (cpg, sc) in active.items():
            cpg_weights[cpg] = cpg_weights.get(cpg, 0.0) + sc
            cpg_voters.setdefault(cpg, []).append(method)

        winner_cpg = max(cpg_weights, key=lambda c: cpg_weights[c])
        voters_for_winner = cpg_voters[winner_cpg]
        n_active = len(active)
        n_agree = len(voters_for_winner)

        # Average confidence of methods that voted for winner
        avg_conf = cpg_weights[winner_cpg] / n_agree

        if n_agree == n_active:
            agreement = "all_agree"
            boost = _CONSENSUS_ALL_BOOST
        elif n_agree > n_active / 2:
            agreement = "majority"
            boost = _CONSENSUS_MAJORITY_BOOST
        else:
            agreement = "split"
            boost = 0.0

        ensemble_confidence = min(1.0, avg_conf + boost)
        return winner_cpg, ensemble_confidence, agreement, voters_for_winner
