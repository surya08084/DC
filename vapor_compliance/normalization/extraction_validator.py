from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rapidfuzz.distance import JaroWinkler

from .llm_extractor import SKUExtractionResult
from .unit_normalizer import normalize_nicotine

logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent / "dictionaries"

with (_BASE / "abbreviations.json").open() as f:
    _ABBR: dict[str, str] = {k.upper(): v for k, v in json.load(f).items()}

with (_BASE / "brand_aliases.json").open() as f:
    _raw_brands = json.load(f)
    _BRAND_CANONICAL: dict[str, str] = {}  # alias_upper → canonical
    for _canonical, _aliases in _raw_brands.items():
        for _alias in _aliases:
            _BRAND_CANONICAL[_alias.upper()] = _canonical
        _BRAND_CANONICAL[_canonical.upper()] = _canonical
    _ALL_BRAND_CANONICALS: set[str] = set(_raw_brands.keys())

with (_BASE / "flavor_synonyms.json").open() as f:
    _flavor_data = json.load(f)
    _FLAVOR_CANONICALS: set[str] = set(_flavor_data["canonical_flavors"].keys())
    _FLAVOR_CATEGORIES: dict[str, str] = {}
    for _cat, _flavors in _flavor_data["flavor_categories"].items():
        for _fl in _flavors:
            _FLAVOR_CATEGORIES[_fl] = _cat
    _ALL_FLAVOR_SYNONYMS: dict[str, str] = {}  # synonym_upper → canonical
    for _canonical, _synonyms in _flavor_data["canonical_flavors"].items():
        for _syn in _synonyms:
            _ALL_FLAVOR_SYNONYMS[_syn.upper()] = _canonical


@dataclass
class ValidationResult:
    """
    Outcome of running the LLM extraction through dictionary validation.

    Fields that passed dictionary checks are kept as-is.
    Fields where the LLM disagreed with the dictionary are corrected and flagged.
    Fields the dictionary cannot confirm are kept with a warning flag.
    """
    # Validated / corrected values
    brand: Optional[str] = None
    flavor_canonical: Optional[str] = None
    flavor_category: Optional[str] = None
    nicotine_mg_ml: Optional[float] = None
    form_factor: Optional[str] = None
    normalized_sku: Optional[str] = None
    abbreviations_expanded: dict[str, str] = field(default_factory=dict)

    # Confidence breakdown
    llm_confidence: float = 0.0         # raw LLM self-assessed confidence
    validation_confidence: float = 0.0  # how much the dict agreed with LLM
    overall_confidence: float = 0.0     # combined

    # Audit flags
    flags: list[str] = field(default_factory=list)
    corrections: dict[str, dict] = field(default_factory=dict)  # {field: {llm: x, dict: y}}
    method: str = "llm+validated"

    @property
    def needs_review(self) -> bool:
        return any(
            f.startswith("CORRECTION") or f.startswith("CONFLICT")
            for f in self.flags
        )


class ExtractionValidator:
    """
    Validates LLM-extracted SKU attributes against versioned dictionaries.

    Validation is field-by-field with three possible outcomes per field:

      CONFIRMED  — LLM matches dictionary exactly → keep, full confidence
      CORRECTED  — LLM disagrees, dictionary is authoritative → use dict value,
                   flag the discrepancy, partial confidence penalty
      UNVERIFIED — field not in dictionary (novel brand/flavor) → keep LLM value,
                   flag for human review, small confidence penalty

    The validator NEVER silently discard a field. Every decision is recorded
    in ValidationResult.corrections for audit.

    Abbreviation check:
      For each entry in abbreviations_expanded {abbr: expansion},
      the validator checks whether our dict agrees:
        - Dict says VT → Virginia Tobacco, LLM says VT → Virginia Tobacco → CONFIRMED
        - Dict says VT → Virginia Tobacco, LLM says VT → Vermont Tobacco  → CORRECTION
        - Abbr not in dict → UNVERIFIED (LLM may have found a new abbreviation)
    """

    # Confidence weights
    _W_BRAND   = 0.30
    _W_FLAVOR  = 0.25
    _W_NIC     = 0.25
    _W_ABBR    = 0.10
    _W_FORM    = 0.10

    def validate(self, extraction: SKUExtractionResult) -> ValidationResult:
        result = ValidationResult(
            llm_confidence=extraction.extraction_confidence,
            abbreviations_expanded=dict(extraction.abbreviations_expanded),
        )
        field_scores: list[float] = []

        # ── Brand ────────────────────────────────────────────────────────────
        brand_score = self._validate_brand(extraction, result)
        field_scores.append(brand_score * self._W_BRAND)

        # ── Flavor ───────────────────────────────────────────────────────────
        flavor_score = self._validate_flavor(extraction, result)
        field_scores.append(flavor_score * self._W_FLAVOR)

        # ── Nicotine strength ────────────────────────────────────────────────
        nic_score = self._validate_nicotine(extraction, result)
        field_scores.append(nic_score * self._W_NIC)

        # ── Abbreviation expansions ───────────────────────────────────────────
        abbr_score = self._validate_abbreviations(extraction, result)
        field_scores.append(abbr_score * self._W_ABBR)

        # ── Form factor ───────────────────────────────────────────────────────
        form_score = self._validate_form_factor(extraction, result)
        field_scores.append(form_score * self._W_FORM)

        # ── Normalized SKU ────────────────────────────────────────────────────
        result.normalized_sku = self._build_normalized_sku(result, extraction)

        # ── Confidence rollup ─────────────────────────────────────────────────
        validation_conf = round(sum(field_scores), 3)
        result.validation_confidence = validation_conf
        result.overall_confidence = round(
            0.50 * extraction.extraction_confidence + 0.50 * validation_conf, 3
        )

        return result

    # ── Field validators ──────────────────────────────────────────────────────

    def _validate_brand(
        self, ext: SKUExtractionResult, result: ValidationResult
    ) -> float:
        if not ext.brand:
            result.flags.append("MISSING:brand_not_extracted")
            return 0.0

        llm_brand = ext.brand.strip()
        dict_brand = _BRAND_CANONICAL.get(llm_brand.upper())

        if dict_brand:
            if dict_brand != llm_brand:
                result.corrections["brand"] = {"llm": llm_brand, "dict": dict_brand}
                result.flags.append(f"CORRECTION:brand '{llm_brand}'→'{dict_brand}'")
            result.brand = dict_brand
            return 1.0

        # Not in dict — check fuzzy similarity to known brands
        best_brand, best_score = self._fuzzy_brand(llm_brand)
        if best_score >= 0.92:
            result.corrections["brand"] = {"llm": llm_brand, "dict": best_brand, "fuzzy": best_score}
            result.flags.append(f"CORRECTION:brand fuzzy '{llm_brand}'→'{best_brand}' ({best_score:.2f})")
            result.brand = best_brand
            return 0.85

        # Genuinely novel brand — trust LLM
        result.brand = llm_brand
        result.flags.append(f"UNVERIFIED:brand '{llm_brand}' not in dictionary")
        return 0.60

    def _validate_flavor(
        self, ext: SKUExtractionResult, result: ValidationResult
    ) -> float:
        if not ext.flavor_canonical:
            # LLM gave up — check flavor_raw against our synonym dict
            if ext.flavor_raw:
                syn_match = _ALL_FLAVOR_SYNONYMS.get(ext.flavor_raw.upper())
                if syn_match:
                    result.flavor_canonical = syn_match
                    result.flavor_category = _FLAVOR_CATEGORIES.get(syn_match)
                    result.flags.append(
                        f"CORRECTION:flavor_canonical from raw '{ext.flavor_raw}'→'{syn_match}'"
                    )
                    return 0.85
            result.flags.append("MISSING:flavor_canonical_not_extracted")
            return 0.0

        llm_flavor = ext.flavor_canonical.strip()

        # Exact match against canonical flavor names
        if llm_flavor in _FLAVOR_CANONICALS:
            result.flavor_canonical = llm_flavor
            result.flavor_category = _FLAVOR_CATEGORIES.get(llm_flavor)
            return 1.0

        # Check synonyms
        syn_match = _ALL_FLAVOR_SYNONYMS.get(llm_flavor.upper())
        if syn_match:
            result.corrections["flavor_canonical"] = {"llm": llm_flavor, "dict": syn_match}
            result.flags.append(f"CORRECTION:flavor '{llm_flavor}'→'{syn_match}'")
            result.flavor_canonical = syn_match
            result.flavor_category = _FLAVOR_CATEGORIES.get(syn_match)
            return 0.90

        # Not in dict — novel flavor, trust LLM
        result.flavor_canonical = llm_flavor
        result.flavor_category = ext.flavor_category
        result.flags.append(f"UNVERIFIED:flavor '{llm_flavor}' not in dictionary")
        return 0.60

    def _validate_nicotine(
        self, ext: SKUExtractionResult, result: ValidationResult
    ) -> float:
        # Recompute from raw string as ground truth (our converter is deterministic)
        dict_mg_ml: Optional[float] = None
        if ext.nicotine_strength_raw:
            dict_mg_ml = normalize_nicotine(ext.nicotine_strength_raw)

        if dict_mg_ml is None and ext.nicotine_mg_ml is None:
            result.flags.append("MISSING:nicotine_not_extracted")
            return 0.0

        if dict_mg_ml is not None and ext.nicotine_mg_ml is not None:
            if abs(dict_mg_ml - ext.nicotine_mg_ml) > 0.1:
                # LLM got the conversion wrong — dictionary wins
                result.corrections["nicotine_mg_ml"] = {
                    "llm": ext.nicotine_mg_ml, "dict": dict_mg_ml,
                    "raw": ext.nicotine_strength_raw,
                }
                result.flags.append(
                    f"CORRECTION:nicotine {ext.nicotine_mg_ml}→{dict_mg_ml} "
                    f"(from '{ext.nicotine_strength_raw}')"
                )
                result.nicotine_mg_ml = dict_mg_ml
                return 0.80
            result.nicotine_mg_ml = dict_mg_ml
            return 1.0

        # Only one source available
        result.nicotine_mg_ml = dict_mg_ml or ext.nicotine_mg_ml
        return 0.75

    def _validate_abbreviations(
        self, ext: SKUExtractionResult, result: ValidationResult
    ) -> float:
        if not ext.abbreviations_expanded:
            return 0.80   # neutral — LLM may not have found any abbreviations

        scores: list[float] = []
        validated_abbr: dict[str, str] = {}

        for abbr, expansion in ext.abbreviations_expanded.items():
            abbr_upper = abbr.upper()
            dict_expansion = _ABBR.get(abbr_upper)

            if dict_expansion is None:
                # New abbreviation the LLM found — keep it but flag
                validated_abbr[abbr] = expansion
                result.flags.append(f"UNVERIFIED:abbreviation '{abbr}'→'{expansion}' not in dict")
                scores.append(0.70)
                continue

            if dict_expansion.lower() == expansion.lower():
                validated_abbr[abbr] = dict_expansion
                scores.append(1.0)
            else:
                # Dictionary is authoritative
                result.corrections[f"abbr:{abbr}"] = {
                    "llm": expansion, "dict": dict_expansion
                }
                result.flags.append(
                    f"CORRECTION:abbreviation '{abbr}' "
                    f"LLM='{expansion}' dict='{dict_expansion}'"
                )
                validated_abbr[abbr] = dict_expansion
                scores.append(0.60)

        result.abbreviations_expanded = validated_abbr
        return round(sum(scores) / len(scores), 3) if scores else 0.80

    def _validate_form_factor(
        self, ext: SKUExtractionResult, result: ValidationResult
    ) -> float:
        valid = {"POD", "DISPOSABLE", "CARTRIDGE", "MOD", "E-LIQUID", "DEVICE", "TANK"}
        if not ext.form_factor:
            result.flags.append("MISSING:form_factor_not_extracted")
            return 0.0
        upper = ext.form_factor.upper()
        if upper in valid:
            result.form_factor = upper
            return 1.0
        # Try partial match
        for v in valid:
            if v in upper or upper in v:
                result.corrections["form_factor"] = {"llm": ext.form_factor, "dict": v}
                result.flags.append(f"CORRECTION:form_factor '{ext.form_factor}'→'{v}'")
                result.form_factor = v
                return 0.85
        result.form_factor = ext.form_factor.upper()
        result.flags.append(f"UNVERIFIED:form_factor '{ext.form_factor}'")
        return 0.60

    def _build_normalized_sku(
        self, result: ValidationResult, ext: SKUExtractionResult
    ) -> str:
        """
        Build canonical SKU name from validated fields.
        Falls back to LLM-provided normalized_sku if validated fields are sparse.
        """
        parts: list[str] = []
        if result.brand:
            parts.append(result.brand)
        if result.flavor_canonical:
            parts.append(result.flavor_canonical)
        if result.nicotine_mg_ml is not None:
            parts.append(f"{result.nicotine_mg_ml}mg/ml")
        if result.form_factor:
            parts.append(result.form_factor.title())
        if parts:
            return " ".join(parts)
        return ext.normalized_sku or ext.raw_name

    # ── Fuzzy brand helper ─────────────────────────────────────────────────────

    @staticmethod
    def _fuzzy_brand(name: str) -> tuple[str, float]:
        best_score = 0.0
        best_brand = name
        name_lower = name.lower()
        for canonical in _ALL_BRAND_CANONICALS:
            score = JaroWinkler.normalized_similarity(name_lower, canonical.lower())
            if score > best_score:
                best_score = score
                best_brand = canonical
        return best_brand, best_score


validator = ExtractionValidator()
