from __future__ import annotations
from datetime import date
from typing import Optional
from ..models.sku import NormalizedSKU, RawSKU
from ..models.match import MatchResult
from ..models.compliance import ComplianceResult, FDARecord, StateRecord, ComplianceStatus
from ..registry.fda_registry import FDARegistry
from ..registry.state_registry import StateRegistry
from ..normalization.abbreviation_expander import expander
from .rules_engine import rules_engine


class DecisionEngine:
    """
    Combines entity match result + FDA registry + state registry
    → per-state compliance decision for each SKU.

    For each (SKU, state) pair:
      1. Look up FDA status for matched CPG
      2. Look up state directory status for matched CPG × state
      3. Apply flavor ban check
      4. Run rules engine → ComplianceResult
    """

    def __init__(self, fda_registry: FDARegistry, state_registry: StateRegistry) -> None:
        self._fda = fda_registry
        self._state = state_registry

    def decide(
        self,
        raw_sku: RawSKU,
        normalized_sku: NormalizedSKU,
        match_result: MatchResult,
        states: list[str] | None = None,
        snapshot_date: date | None = None,
    ) -> list[ComplianceResult]:
        """
        Returns one ComplianceResult per state.
        If states is None, uses raw_sku.state (single state) or all known states.
        """
        target_states = states or ([raw_sku.state] if raw_sku.state else self._state.all_states())

        if not match_result.matched_cpg_id:
            return [
                self._unknown_result(raw_sku.sku_id, s, match_result, normalized_sku)
                for s in target_states
            ]

        cpg_id = match_result.matched_cpg_id
        fda_rec = self._fda.get(cpg_id) or self._empty_fda(cpg_id)

        results: list[ComplianceResult] = []
        for state in target_states:
            state_rec = self._state.get(cpg_id, state) or self._empty_state(cpg_id, state)

            # Apply flavor ban from abbreviation expander dictionary
            if normalized_sku.flavor_canonical:
                flavor_banned = expander.is_flavor_banned(normalized_sku.flavor_canonical, state)
                if flavor_banned:
                    state_rec = state_rec.model_copy(update={"flavor_ban_applies": True})

            result = rules_engine.classify(
                fda=fda_rec,
                state_rec=state_rec,
                match_confidence=match_result.confidence,
                normalization_confidence=normalized_sku.normalization_confidence,
                snapshot_date=snapshot_date,
                sku_id=raw_sku.sku_id,
                cpg_id=cpg_id,
            )
            result.matched_fda_id = fda_rec.fda_match_id
            result.matched_state_id = state_rec.directory_match_id
            result.match_method = match_result.match_stage.value
            results.append(result)

        return results

    def _unknown_result(
        self, sku_id: str, state: str, match: MatchResult, sku: NormalizedSKU
    ) -> ComplianceResult:
        from datetime import datetime
        return ComplianceResult(
            sku_id=sku_id,
            cpg_id=None,
            state=state,
            compliance_status=ComplianceStatus.UNKNOWN,
            confidence=match.confidence,
            normalization_confidence=sku.normalization_confidence,
            match_confidence=match.confidence,
            rule_confidence=0.0,
            reason="No CPG match found — product identity unresolved",
            last_updated=datetime.utcnow(),
        )

    def _empty_fda(self, cpg_id: str) -> FDARecord:
        return FDARecord(cpg_id=cpg_id, fda_match_flag=False, confidence=0.0)

    def _empty_state(self, cpg_id: str, state: str) -> StateRecord:
        has_registry = self._state.state_has_registry(state)
        return StateRecord(
            cpg_id=cpg_id,
            state=state,
            directory_match_flag=False,
            confidence=0.0,
            state_has_registry=has_registry,
        )
