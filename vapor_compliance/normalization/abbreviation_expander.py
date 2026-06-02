from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).parent.parent / "dictionaries"

with (_BASE / "abbreviations.json").open() as f:
    _ABBR: dict[str, str] = {k.upper(): v for k, v in json.load(f).items()}

with (_BASE / "brand_aliases.json").open() as f:
    _raw_brands = json.load(f)
    _BRAND_LOOKUP: dict[str, str] = {}
    for canonical, aliases in _raw_brands.items():
        for alias in aliases:
            _BRAND_LOOKUP[alias.upper()] = canonical

with (_BASE / "manufacturer_aliases.json").open() as f:
    _raw_mfr = json.load(f)
    _MFR_LOOKUP: dict[str, str] = {}
    for canonical, aliases in _raw_mfr.items():
        for alias in aliases:
            _MFR_LOOKUP[alias.upper()] = canonical

with (_BASE / "flavor_synonyms.json").open() as f:
    _flavor_data = json.load(f)
    _FLAVOR_LOOKUP: dict[str, str] = {}
    for canonical, synonyms in _flavor_data["canonical_flavors"].items():
        for syn in synonyms:
            _FLAVOR_LOOKUP[syn.upper()] = canonical
    _FLAVOR_CATEGORIES: dict[str, str] = {}
    for category, flavors in _flavor_data["flavor_categories"].items():
        for flavor in flavors:
            _FLAVOR_CATEGORIES[flavor] = category
    _FLAVOR_BAN: dict[str, list[str]] = _flavor_data.get("flavor_ban_categories", {})


class AbbreviationExpander:
    def expand_token(self, token: str) -> tuple[str, bool]:
        """Return (expanded, was_expanded)."""
        upper = token.upper()
        if upper in _ABBR:
            return _ABBR[upper], True
        return token, False

    def expand_text(self, text: str) -> tuple[str, dict[str, str]]:
        """Return (expanded_text, expansions_applied)."""
        tokens = text.split()
        expanded_tokens = []
        expansions: dict[str, str] = {}
        for token in tokens:
            expanded, was_expanded = self.expand_token(token)
            expanded_tokens.append(expanded)
            if was_expanded:
                expansions[token] = expanded
        return " ".join(expanded_tokens), expansions

    def resolve_brand(self, text: str) -> Optional[str]:
        upper = text.upper()
        if upper in _BRAND_LOOKUP:
            return _BRAND_LOOKUP[upper]
        # Try partial match on each word
        for word in upper.split():
            if word in _BRAND_LOOKUP:
                return _BRAND_LOOKUP[word]
        # Direct match as canonical
        for canonical in _raw_brands:
            if canonical.upper() == upper:
                return canonical
        return None

    def resolve_manufacturer(self, text: str) -> Optional[str]:
        upper = text.upper()
        if upper in _MFR_LOOKUP:
            return _MFR_LOOKUP[upper]
        return None

    def resolve_flavor(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """Return (canonical_flavor, flavor_category)."""
        upper = text.upper()
        if upper in _FLAVOR_LOOKUP:
            canonical = _FLAVOR_LOOKUP[upper]
            category = _FLAVOR_CATEGORIES.get(canonical)
            return canonical, category
        # Try substring match
        for syn_upper, canonical in _FLAVOR_LOOKUP.items():
            if syn_upper in upper or upper in syn_upper:
                category = _FLAVOR_CATEGORIES.get(canonical)
                return canonical, category
        return None, None

    def is_flavor_banned(self, flavor_canonical: str, state: str) -> bool:
        category = _FLAVOR_CATEGORIES.get(flavor_canonical)
        if not category:
            return False
        banned_cats = _FLAVOR_BAN.get(state.upper(), [])
        return category in banned_cats


expander = AbbreviationExpander()
