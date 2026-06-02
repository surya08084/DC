from __future__ import annotations
import json
import logging
from typing import Optional
from ..models.sku import NormalizedSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage
from ..config import settings

logger = logging.getLogger(__name__)


class LLMAdjudicator:
    """
    Stage 5 — Claude API structured adjudication for edge cases only.

    Triggered when overall confidence is between REVIEW_QUEUE_MIN and LLM_TRIGGER_MAX.
    The LLM receives fully structured context (not raw text) and returns a
    machine-readable decision. This keeps it auditable.

    Volume: Expected < 5% of records. Enforced via LLM_MAX_CALLS_PER_BATCH.
    """

    def __init__(self) -> None:
        self._client = None
        self._call_count = 0
        self._init_client()

    def _init_client(self) -> None:
        if not settings.ANTHROPIC_API_KEY:
            logger.info("LLMAdjudicator: ANTHROPIC_API_KEY not set — Stage 5 disabled")
            return
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        except ImportError:
            logger.warning("LLMAdjudicator: anthropic package not installed")

    def reset_call_count(self) -> None:
        self._call_count = 0

    def adjudicate(
        self,
        sku: NormalizedSKU,
        candidate: CanonicalProduct,
        prior_result: MatchResult,
    ) -> Optional[MatchResult]:
        if self._client is None:
            return None
        if self._call_count >= settings.LLM_MAX_CALLS_PER_BATCH:
            logger.warning("LLMAdjudicator: batch call limit reached")
            return None

        context = {
            "query": {
                "brand": sku.brand,
                "flavor_canonical": sku.flavor_canonical,
                "nicotine_mg_ml": sku.nicotine_mg_ml,
                "form_factor": sku.form_factor,
                "product_type": sku.product_type,
                "normalized_name": sku.normalized_name,
                "raw_name": sku.raw_name,
                "high_risk_flags": sku.high_risk_flags,
            },
            "candidate": {
                "cpg_id": candidate.cpg_id,
                "brand": candidate.brand,
                "flavor_canonical": candidate.flavor_canonical,
                "nicotine_mg_ml": candidate.nicotine_mg_ml,
                "form_factor": candidate.form_factor,
                "canonical_name": candidate.canonical_name,
            },
            "prior_confidence": prior_result.confidence,
            "prior_stage": prior_result.match_stage,
            "stage_scores": prior_result.stage_scores,
        }

        prompt = f"""You are a vapor tobacco product compliance expert.
Your task is to determine if the QUERY product and the CANDIDATE reference product are the same regulated product.

CONTEXT (structured, pre-computed signals):
{json.dumps(context, indent=2)}

Rules:
1. Brand mismatch → NOT_SAME regardless of other fields.
2. Nicotine strength difference > 5mg/ml → NOT_SAME.
3. Form factor mismatch (pod vs disposable) → NOT_SAME.
4. Flavor variants of the same base flavor MAY be the same product family — use judgment.

Respond ONLY with valid JSON in this exact format:
{{
  "decision": "SAME" | "NOT_SAME" | "UNCERTAIN",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one concise sentence>"
}}"""

        try:
            response = self._client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            self._call_count += 1
            text = response.content[0].text.strip()
            parsed = json.loads(text)

            decision = parsed.get("decision", "UNCERTAIN")
            llm_confidence = float(parsed.get("confidence", 0.5))
            reasoning = parsed.get("reasoning", "")

            if decision == "SAME":
                return MatchResult(
                    query_sku_id=sku.raw_sku_id,
                    matched_cpg_id=candidate.cpg_id,
                    match_stage=MatchStage.LLM,
                    confidence=round(llm_confidence, 3),
                    match_explanation=f"LLM: {reasoning}",
                    matched_source=candidate.source,
                    is_review_required=llm_confidence < settings.AUTO_CLASSIFY_MIN,
                    candidates_considered=prior_result.candidates_considered,
                    stage_scores={**prior_result.stage_scores, "llm": round(llm_confidence, 3)},
                )
            elif decision == "NOT_SAME":
                return None
            else:
                # UNCERTAIN — pass through with lower confidence for human review
                result = prior_result.model_copy(deep=True)
                result.confidence = round(min(prior_result.confidence, llm_confidence), 3)
                result.is_review_required = True
                result.match_explanation += f" | LLM uncertain: {reasoning}"
                return result

        except Exception as e:
            logger.warning("LLMAdjudicator call failed: %s", e)
            return None
