from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import date, datetime


class ComplianceStatus(str, Enum):
    FEDERAL_LICIT = "FEDERAL_LICIT"       # FDA approved + state listed
    STATE_LICIT = "STATE_LICIT"           # FDA approved + state listed (all conditions met)
    GREY_MARKET = "GREY_MARKET"           # FDA approved + state has registry but not listed
    POTENTIAL_ILLICIT = "POTENTIAL_ILLICIT"  # Not FDA approved + state listed
    ILLICIT = "ILLICIT"                   # Not FDA approved + not state listed
    REVIEW_REQUIRED = "REVIEW_REQUIRED"   # Low confidence, needs human
    UNKNOWN = "UNKNOWN"                   # Insufficient data
    EXCLUDED = "EXCLUDED"                 # Out of scope (accessories, non-vape)


class FDARecord(BaseModel):
    cpg_id: str
    fda_match_flag: bool = False
    fda_match_id: Optional[str] = None
    pmta_order_number: Optional[str] = None
    authorized_manufacturer: Optional[str] = None
    confidence: float = 0.0
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    marketing_granted: bool = False


class StateRecord(BaseModel):
    cpg_id: str
    state: str
    directory_match_flag: bool = False
    directory_match_id: Optional[str] = None
    confidence: float = 0.0
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    state_has_registry: bool = True
    directory_model: str = "WHITELIST"        # WHITELIST | BLACKLIST | ENFORCEMENT_ONLY
    enforcement_status: str = "IN_EFFECT"     # IN_EFFECT | PENDING | ENJOINED
    flavor_ban_applies: bool = False
    rule_version: str = "1.0"


class ComplianceResult(BaseModel):
    sku_id: str
    cpg_id: Optional[str] = None
    state: str
    snapshot_date: date = Field(default_factory=date.today)
    last_updated: datetime = Field(default_factory=datetime.utcnow)

    # Evidence flags
    fda_approved: bool = False
    state_listed: bool = False
    state_has_registry: bool = True

    # Final classification
    compliance_status: ComplianceStatus = ComplianceStatus.UNKNOWN
    confidence: float = 0.0

    # Component confidences
    normalization_confidence: float = 0.0
    match_confidence: float = 0.0
    rule_confidence: float = 0.0

    reason: str = ""

    # Source records
    fda_record: Optional[FDARecord] = None
    state_record: Optional[StateRecord] = None

    # Audit trail
    matched_fda_id: Optional[str] = None
    matched_state_id: Optional[str] = None
    match_method: str = ""
    dictionary_updates_applied: bool = False
    analyst_id: Optional[str] = None
    override_flag: bool = False
    override_reason: Optional[str] = None
