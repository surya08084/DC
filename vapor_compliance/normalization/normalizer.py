from __future__ import annotations
import logging
from typing import Optional

from ..models.sku import RawSKU, NormalizedSKU
from .text_cleaner import clean
from .unit_normalizer import extract_all
from .abbreviation_expander import expander
from .flavor_extractor import flavor_extractor
from .llm_extractor import llm_extractor, SKUExtractionResult
from .extraction_validator import validator, ValidationResult

logger = logging.getLogger(__name__)


class Normalizer:
    """
    Converts a RawSKU → NormalizedSKU via a two-path pipeline:

    PATH A — LLM-first (when ANTHROPIC_API_KEY is set)
    ────────────────────────────────────────────────────
      1. Call Claude API with the raw SKU name.
         Returns structured JSON: brand, flavor, nicotine_mg_ml, form_factor,
         normalized_sku, abbreviations_expanded, extraction_confidence.
      2. Validate every field against our versioned dictionaries.
         Dictionary wins on conflicts; LLM wins on novel values (flagged).
      3. Compute overall confidence from LLM confidence + validation agreement.

    PATH B — Dictionary-only (fallback)
    ────────────────────────────────────────────────────
      1. Abbreviation expansion via abbreviations.json.
      2. Unit normalization (5% → 50mg/ml).
      3. Brand / manufacturer resolution via brand_aliases.json.
      4. Flavor extraction via residual token method (position-aware).

    The method field on NormalizedSKU records which path ran:
      "llm+validated"   — Path A, all fields confirmed by dictionary
      "llm+corrections" — Path A, some LLM values corrected by dictionary
      "llm+unverified"  — Path A, some novel values kept from LLM
      "dictionary"      — Path B only
      "hybrid"          — Path B with a pre-expanded name hint passed in

    Path A is tried first. If the LLM is unavailable, returns low confidence,
    or raises an exception, Path B runs automatically.
    """

    def normalize(
        self,
        raw: RawSKU,
        pre_expanded_name: Optional[str] = None,
        use_llm: bool = True,
    ) -> NormalizedSKU:
        # ── Path A: LLM extraction + validation ──────────────────────────────
        if use_llm and llm_extractor.available:
            result = self._normalize_via_llm(raw)
            if result is not None:
                return result
            logger.debug("Normalizer: LLM path failed for '%s', falling back", raw.raw_name)

        # ── Path B: Dictionary / residual fallback ────────────────────────────
        return self._normalize_via_dictionary(raw, pre_expanded_name)

    def normalize_batch(
        self,
        raws: list[RawSKU],
        use_llm: bool = True,
    ) -> list[NormalizedSKU]:
        """
        Batch normalize — sends all SKUs in one LLM call (up to BATCH_SIZE chunks).
        More efficient than calling normalize() in a loop when LLM is enabled.
        """
        if not (use_llm and llm_extractor.available):
            return [self._normalize_via_dictionary(r, None) for r in raws]

        raw_names = [r.raw_name for r in raws]
        extractions = llm_extractor.extract_batch(raw_names)

        results: list[NormalizedSKU] = []
        for raw in raws:
            ext = extractions.get(raw.raw_name)
            if ext:
                validated = validator.validate(ext)
                norm = self._build_from_validated(raw, ext, validated)
                if norm:
                    results.append(norm)
                    continue
            results.append(self._normalize_via_dictionary(raw, None))
        return results

    # ── Path A ────────────────────────────────────────────────────────────────

    def _normalize_via_llm(self, raw: RawSKU) -> Optional[NormalizedSKU]:
        try:
            ext = llm_extractor.extract_one(raw.raw_name)
            if ext is None:
                return None
            validated = validator.validate(ext)
            return self._build_from_validated(raw, ext, validated)
        except Exception as e:
            logger.warning("Normalizer: LLM path exception for '%s': %s", raw.raw_name, e)
            return None

    def _build_from_validated(
        self,
        raw: RawSKU,
        ext: SKUExtractionResult,
        v: ValidationResult,
    ) -> Optional[NormalizedSKU]:
        if v.overall_confidence < 0.20:
            logger.debug("Normalizer: LLM result discarded (conf=%.2f) for '%s'", v.overall_confidence, raw.raw_name)
            return None

        # Determine normalization method label
        has_corrections = any(f.startswith("CORRECTION") for f in v.flags)
        has_unverified  = any(f.startswith("UNVERIFIED")  for f in v.flags)
        if has_corrections and has_unverified:
            method = "llm+corrections+unverified"
        elif has_corrections:
            method = "llm+corrections"
        elif has_unverified:
            method = "llm+unverified"
        else:
            method = "llm+validated"

        # Also run unit extractor for any fields the LLM missed
        units = extract_all(raw.raw_name)
        nicotine = v.nicotine_mg_ml or units["nicotine_mg_ml"]
        volume   = ext.volume_ml    or units["volume_ml"]
        puffs    = ext.puff_count   or units["puff_count"]
        packs    = ext.pack_count   or units["pack_count"]
        form     = v.form_factor    or units["product_type"]

        flags = list(v.flags)
        if v.needs_review:
            flags.append("llm_validation_conflicts_present")

        return NormalizedSKU(
            raw_sku_id=raw.sku_id,
            brand=v.brand,
            manufacturer=ext.manufacturer or expander.resolve_manufacturer(v.brand or ""),
            product_type=form,
            flavor=ext.flavor_raw,
            flavor_canonical=v.flavor_canonical,
            flavor_category=v.flavor_category,
            nicotine_mg_ml=nicotine,
            volume_ml=volume,
            puff_count=puffs,
            pack_count=packs,
            form_factor=form,
            raw_name=raw.raw_name,
            normalized_name=v.normalized_sku or raw.raw_name,
            abbreviations_expanded=v.abbreviations_expanded,
            normalization_confidence=v.overall_confidence,
            normalization_method=method,
            high_risk_flags=flags,
            source=raw.source,
        )

    # ── Path B ────────────────────────────────────────────────────────────────

    def _normalize_via_dictionary(
        self, raw: RawSKU, pre_expanded_name: Optional[str]
    ) -> NormalizedSKU:
        source_name = pre_expanded_name or raw.raw_name
        cleaned = clean(source_name)

        expanded_text, expansions = expander.expand_text(cleaned)
        units = extract_all(expanded_text)
        brand = expander.resolve_brand(expanded_text)
        manufacturer = expander.resolve_manufacturer(raw.manufacturer or expanded_text)

        flavor_canonical, flavor_category, flavor_span, flavor_conf = \
            flavor_extractor.extract(expanded_text, brand=brand)

        flags: list[str] = []
        if not brand:
            flags.append("brand_not_in_dictionary")
        if flavor_canonical is None:
            flags.append("flavor_not_resolved")
        elif flavor_conf < 0.60:
            flags.append("flavor_low_confidence_extraction")
        elif flavor_conf < 0.80:
            flags.append("flavor_fulltext_fallback")
        if units["nicotine_mg_ml"] is None:
            flags.append("nicotine_not_found")
        if pre_expanded_name and not expansions:
            flags.append("no_abbreviations_expanded")

        confidence = 1.0
        if not brand:
            confidence -= 0.25
        if flavor_canonical is None:
            confidence -= 0.15
        elif flavor_conf < 1.0:
            confidence -= 0.15 * (1.0 - flavor_conf)
        if units["nicotine_mg_ml"] is None:
            confidence -= 0.15
        if units["product_type"] is None:
            confidence -= 0.10
        confidence = max(0.0, round(confidence, 2))

        normalized_name = self._build_name(
            brand, flavor_canonical, units["nicotine_mg_ml"],
            units["product_type"], units["volume_ml"], units["puff_count"]
        ) or expanded_text

        return NormalizedSKU(
            raw_sku_id=raw.sku_id,
            brand=brand,
            manufacturer=manufacturer,
            product_type=units["product_type"],
            flavor=expanded_text,
            flavor_canonical=flavor_canonical,
            flavor_category=flavor_category,
            nicotine_mg_ml=units["nicotine_mg_ml"],
            volume_ml=units["volume_ml"],
            puff_count=units["puff_count"],
            pack_count=units["pack_count"],
            form_factor=units["product_type"],
            raw_name=raw.raw_name,
            normalized_name=normalized_name,
            abbreviations_expanded=expansions,
            normalization_confidence=confidence,
            normalization_method="hybrid" if pre_expanded_name else "dictionary",
            high_risk_flags=flags,
            source=raw.source,
        )

    @staticmethod
    def _build_name(
        brand: Optional[str],
        flavor: Optional[str],
        nic: Optional[float],
        ptype: Optional[str],
        vol: Optional[float],
        puffs: Optional[int],
    ) -> Optional[str]:
        parts = []
        if brand:
            parts.append(brand)
        if flavor:
            parts.append(flavor)
        if nic is not None:
            parts.append(f"{nic}mg/ml")
        if ptype:
            parts.append(ptype.title())
        if vol:
            parts.append(f"{vol}ml")
        if puffs:
            parts.append(f"{puffs}puffs")
        return " ".join(parts) if parts else None


normalizer = Normalizer()
