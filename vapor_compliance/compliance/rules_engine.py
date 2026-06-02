from __future__ import annotations
from datetime import date
from ..models.compliance import (
    ComplianceStatus, ComplianceResult, FDARecord, StateRecord,
)
from ..config import settings


class RulesEngine:
    """
    Deterministic IF/THEN compliance classification.

    Intentionally NOT machine-learning based — compliance decisions
    must be explainable and reproducible for regulatory audit.

    Decision matrix:
      FDA=Y  State=Y  → FEDERAL_LICIT (full compliance)
      FDA=Y  State=N  (registry exists) → GREY_MARKET
      FDA=Y  State=N  (no registry in state) → FEDERAL_LICIT
      FDA=N  State=Y  → POTENTIAL_ILLICIT
      FDA=N  State=N  → ILLICIT
      Match < AUTO_CLASSIFY_MIN → REVIEW_REQUIRED
      Flavor ban applies → STATE_ILLICIT (overrides everything at state level)
    """

    def classify(
        self,
        fda: FDARecord,
        state_rec: StateRecord,
        match_confidence: float,
        normalization_confidence: float,
        snapshot_date: date | None = None,
        sku_id: str = "",
        cpg_id: str | None = None,
    ) -> ComplianceResult:
        snap = snapshot_date or date.today()

        # Temporal validity checks
        fda_approved = self._is_temporally_valid(fda) and fda.fda_match_flag
        state_listed = self._is_temporally_valid(state_rec) and state_rec.directory_match_flag

        # Rule confidence: how certain is the regulatory data itself
        rule_confidence = min(fda.confidence, state_rec.confidence) if fda.confidence and state_rec.confidence else 0.5

        # Rolled-up overall confidence
        overall = (
            settings.ROLLUP_NORMALIZATION * normalization_confidence
            + settings.ROLLUP_MATCH * match_confidence
            + settings.ROLLUP_RULE * rule_confidence
        )
        overall = round(min(1.0, max(0.0, overall)), 3)

        # Low confidence → always REVIEW_REQUIRED regardless of data
        if overall < settings.REVIEW_QUEUE_MIN:
            return self._make_result(
                sku_id, cpg_id, state_rec.state, snap,
                ComplianceStatus.REVIEW_REQUIRED, overall,
                fda_approved, state_listed, state_rec.state_has_registry,
                fda, state_rec, normalization_confidence, match_confidence, rule_confidence,
                "Overall confidence below review threshold",
            )

        # Flavor ban overrides state compliance
        if state_rec.flavor_ban_applies and state_listed:
            return self._make_result(
                sku_id, cpg_id, state_rec.state, snap,
                ComplianceStatus.ILLICIT, overall,
                fda_approved, state_listed, state_rec.state_has_registry,
                fda, state_rec, normalization_confidence, match_confidence, rule_confidence,
                f"Flavor banned in state {state_rec.state}",
            )

        # Enforcement enjoined → treat as no enforcement
        if state_rec.enforcement_status == "ENJOINED":
            state_listed = True   # enjoined ban means enforcement paused → treat as listed

        # Core decision logic
        status, reason = self._classify_core(fda_approved, state_listed, state_rec)

        # Downgrade if match confidence is moderate (not low enough for REVIEW, but flag it)
        if match_confidence < settings.AUTO_CLASSIFY_MIN and status in (
            ComplianceStatus.FEDERAL_LICIT, ComplianceStatus.STATE_LICIT
        ):
            status = ComplianceStatus.REVIEW_REQUIRED
            reason += " [match confidence moderate — verify]"

        return self._make_result(
            sku_id, cpg_id, state_rec.state, snap,
            status, overall,
            fda_approved, state_listed, state_rec.state_has_registry,
            fda, state_rec, normalization_confidence, match_confidence, rule_confidence,
            reason,
        )

    def _classify_core(
        self, fda_approved: bool, state_listed: bool, state_rec: StateRecord
    ) -> tuple[ComplianceStatus, str]:
        if fda_approved and state_listed:
            return ComplianceStatus.FEDERAL_LICIT, "FDA authorized and state listed"

        if fda_approved and not state_listed:
            if not state_rec.state_has_registry:
                return ComplianceStatus.FEDERAL_LICIT, "FDA authorized; state has no registry"
            if state_rec.directory_model == "BLACKLIST":
                # Not on blacklist → licit in this state
                return ComplianceStatus.STATE_LICIT, "FDA authorized; not on state blacklist"
            return ComplianceStatus.GREY_MARKET, "FDA authorized but not in state registry"

        if not fda_approved and state_listed:
            return (
                ComplianceStatus.POTENTIAL_ILLICIT,
                "Not FDA authorized but present in state directory",
            )

        # not fda_approved and not state_listed
        if not state_rec.state_has_registry:
            return (
                ComplianceStatus.POTENTIAL_ILLICIT,
                "Not FDA authorized; state has no registry to confirm",
            )
        return ComplianceStatus.ILLICIT, "Not FDA authorized and not in state registry"

    def _is_temporally_valid(self, rec: FDARecord | StateRecord) -> bool:
        today = date.today()
        if rec.effective_from and today < rec.effective_from:
            return False
        if rec.effective_to and today > rec.effective_to:
            return False
        return True

    def _make_result(
        self,
        sku_id, cpg_id, state, snap, status, overall,
        fda_approved, state_listed, state_has_registry,
        fda, state_rec,
        norm_conf, match_conf, rule_conf,
        reason,
    ) -> ComplianceResult:
        from datetime import datetime
        return ComplianceResult(
            sku_id=sku_id,
            cpg_id=cpg_id,
            state=state,
            snapshot_date=snap,
            last_updated=datetime.utcnow(),
            fda_approved=fda_approved,
            state_listed=state_listed,
            state_has_registry=state_has_registry,
            compliance_status=status,
            confidence=overall,
            normalization_confidence=norm_conf,
            match_confidence=match_conf,
            rule_confidence=rule_conf,
            reason=reason,
            fda_record=fda,
            state_record=state_rec,
        )


rules_engine = RulesEngine()
