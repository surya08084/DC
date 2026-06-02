from __future__ import annotations
from typing import Optional
from ..models.sku import RawSKU, NormalizedSKU
from .text_cleaner import clean, tokenize
from .unit_normalizer import extract_all
from .abbreviation_expander import expander


class Normalizer:
    """
    Converts a RawSKU into a NormalizedSKU using deterministic rules.
    LLM-based expansion happens upstream (via Copilot) and feeds into
    this pipeline as a pre-expanded string; we validate it here.
    """

    def normalize(self, raw: RawSKU, pre_expanded_name: Optional[str] = None) -> NormalizedSKU:
        source_name = pre_expanded_name if pre_expanded_name else raw.raw_name
        cleaned = clean(source_name)

        # Abbreviation expansion
        expanded_text, expansions = expander.expand_text(cleaned)

        # Unit extraction from expanded text
        units = extract_all(expanded_text)

        # Brand + manufacturer resolution
        brand = expander.resolve_brand(expanded_text)
        manufacturer = expander.resolve_manufacturer(raw.manufacturer or expanded_text)

        # Flavor resolution
        flavor_canonical, flavor_category = expander.resolve_flavor(expanded_text)

        # High-risk flags
        flags: list[str] = []
        if not brand:
            flags.append("brand_not_in_dictionary")
        if flavor_canonical is None:
            flags.append("flavor_not_resolved")
        if units["nicotine_mg_ml"] is None:
            flags.append("nicotine_not_found")
        if pre_expanded_name and not expansions:
            flags.append("no_abbreviations_expanded")

        # Confidence: reduce per missing critical field
        confidence = 1.0
        if not brand:
            confidence -= 0.25
        if flavor_canonical is None:
            confidence -= 0.15
        if units["nicotine_mg_ml"] is None:
            confidence -= 0.15
        if units["product_type"] is None:
            confidence -= 0.10
        confidence = max(0.0, round(confidence, 2))

        normalized_name = self._build_normalized_name(
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

    def _build_normalized_name(
        self,
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
