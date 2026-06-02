from __future__ import annotations
import re
import json
from pathlib import Path
from typing import Optional

_BASE = Path(__file__).parent.parent / "dictionaries"

# ── Load flavor synonyms ─────────────────────────────────────────────────────
with (_BASE / "flavor_synonyms.json").open() as f:
    _flavor_data = json.load(f)

# Build lookup: synonym_upper → canonical name
_FLAVOR_LOOKUP: dict[str, str] = {}
for _canonical, _synonyms in _flavor_data["canonical_flavors"].items():
    for _syn in _synonyms:
        _FLAVOR_LOOKUP[_syn.upper()] = _canonical

_FLAVOR_CATEGORIES: dict[str, str] = {}
for _category, _flavors in _flavor_data["flavor_categories"].items():
    for _flavor in _flavors:
        _FLAVOR_CATEGORIES[_flavor] = _category

# Sort by length descending so longer (more specific) synonyms match first
_SORTED_SYNONYMS: list[tuple[str, str]] = sorted(
    _FLAVOR_LOOKUP.items(), key=lambda x: -len(x[0])
)

# ── Load brand tokens ────────────────────────────────────────────────────────
with (_BASE / "brand_aliases.json").open() as f:
    _raw_brands = json.load(f)

_BRAND_TOKENS: set[str] = set()
for _canonical, _aliases in _raw_brands.items():
    for _token in (_canonical + " " + " ".join(_aliases)).lower().split():
        _BRAND_TOKENS.add(_token)

# ── Load product line tokens ─────────────────────────────────────────────────
with (_BASE / "product_lines.json").open() as f:
    _pl_data = json.load(f)

_PRODUCT_LINE_TOKENS: set[str] = set(_pl_data["generic"])
for _tokens in _pl_data["brand_specific"].values():
    _PRODUCT_LINE_TOKENS.update(t.lower() for t in _tokens)
_PRODUCT_LINE_TOKENS.update(t.lower() for t in _pl_data["size_keywords"])

# ── Load unit + product type tokens ─────────────────────────────────────────
with (_BASE / "unit_mappings.json").open() as f:
    _unit_data = json.load(f)

_PRODUCT_TYPE_TOKENS: set[str] = set(_unit_data["product_type_map"].keys())

# ── Regex patterns ────────────────────────────────────────────────────────────
# Model codes: alphanumeric like BC5000, NC600, P1, 10000, 3300
_MODEL_CODE_RE = re.compile(
    r'^(?:[A-Za-z]{1,4}\d{2,}|\d{2,}[A-Za-z]{0,4}|[A-Za-z]{1,2}\d[A-Za-z]*)$'
)
# Pure number token
_PURE_NUMBER_RE = re.compile(r'^\d+$')
# Unit suffixes: 5%, 50mg, 13ml, 5000puffs, 2ct, 4pk
_UNIT_TOKEN_RE = re.compile(
    r'^\d+(?:\.\d+)?(?:%|mg(?:/ml)?|ml|puff|puffs|hit|hits|ct|pk|pack|count)$',
    re.I,
)


class FlavorExtractor:
    """
    Position-aware flavor extraction using the residual token method.

    Rather than scanning the full SKU string for flavor keywords (which
    gives false matches on model codes, product lines, and unit tokens),
    this extractor:

      1. Tokenizes the normalized text.
      2. Strips known non-flavor token classes in order of certainty:
           brand tokens > model codes > unit tokens > product type tokens
           > product line tokens > standalone numbers
      3. Generates n-grams from the residual (longest first).
      4. Matches each n-gram against the flavor synonym dictionary.
      5. Falls back to full-text n-gram scan if the residual is empty.

    Returns:
      (canonical_flavor, flavor_category, raw_flavor_span, confidence)

    Confidence:
      1.0  — extracted from clean residual with clear n-gram match
      0.80 — extracted from residual but single-token (may be ambiguous)
      0.60 — extracted via full-text fallback (residual was empty)
      0.40 — only a partial / uncertain match found
      0.0  — no flavor found
    """

    def extract(
        self,
        text: str,
        brand: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str], Optional[str], float]:
        """
        Returns (canonical_flavor, flavor_category, raw_span, confidence).
        raw_span is the actual text fragment that was matched.
        """
        tokens = text.lower().split()

        # ── Step 1: Build residual ────────────────────────────────────────────
        residual_tokens = self._strip_non_flavor(tokens, brand)

        # ── Step 2: Try n-gram match on residual ──────────────────────────────
        if residual_tokens:
            canonical, category, span = self._ngram_match(residual_tokens)
            if canonical:
                multi_word = len(span.split()) > 1
                conf = 1.0 if multi_word else 0.80
                return canonical, category, span, conf

        # ── Step 3: Fallback — full-text n-gram scan ──────────────────────────
        canonical, category, span = self._ngram_match(tokens)
        if canonical:
            return canonical, category, span, 0.60

        # ── Step 4: Last resort — longest substring scan ──────────────────────
        upper = text.upper()
        for syn_upper, canonical in _SORTED_SYNONYMS:
            if syn_upper in upper:
                category = _FLAVOR_CATEGORIES.get(canonical)
                return canonical, category, syn_upper.lower(), 0.40

        return None, None, None, 0.0

    # ── Residual builder ──────────────────────────────────────────────────────

    def _strip_non_flavor(self, tokens: list[str], brand: Optional[str]) -> list[str]:
        brand_words = set()
        if brand:
            brand_words = {w.lower() for w in brand.split()}

        residual: list[str] = []
        for token in tokens:
            if self._is_brand_token(token, brand_words):
                continue
            if self._is_model_code(token):
                continue
            if self._is_unit_token(token):
                continue
            if self._is_product_type(token):
                continue
            if self._is_product_line(token):
                continue
            if _PURE_NUMBER_RE.match(token):
                continue
            residual.append(token)

        return residual

    def _is_brand_token(self, token: str, brand_words: set[str]) -> bool:
        return token in _BRAND_TOKENS or token in brand_words

    def _is_model_code(self, token: str) -> bool:
        return bool(_MODEL_CODE_RE.match(token))

    def _is_unit_token(self, token: str) -> bool:
        return bool(_UNIT_TOKEN_RE.match(token))

    def _is_product_type(self, token: str) -> bool:
        return token in _PRODUCT_TYPE_TOKENS

    def _is_product_line(self, token: str) -> bool:
        return token in _PRODUCT_LINE_TOKENS

    # ── N-gram matcher ────────────────────────────────────────────────────────

    def _ngram_match(
        self, tokens: list[str]
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Slide a window from max_n down to 1 across tokens.
        Returns (canonical, category, matched_span) for the first (longest) hit.
        Longest-first ensures "Watermelon Ice" wins over just "Ice".
        """
        max_n = min(5, len(tokens))
        for n in range(max_n, 0, -1):
            for i in range(len(tokens) - n + 1):
                ngram = " ".join(tokens[i : i + n])
                canonical = _FLAVOR_LOOKUP.get(ngram.upper())
                if canonical:
                    category = _FLAVOR_CATEGORIES.get(canonical)
                    return canonical, category, ngram
        return None, None, None

    def debug(self, text: str, brand: Optional[str] = None) -> dict:
        """Return a detailed breakdown for inspection and debugging."""
        tokens = text.lower().split()
        residual = self._strip_non_flavor(tokens, brand)

        stripped: dict[str, list[str]] = {
            "brand": [], "model_code": [], "unit": [],
            "product_type": [], "product_line": [], "number": [],
        }
        brand_words = {w.lower() for w in brand.split()} if brand else set()
        for token in tokens:
            if self._is_brand_token(token, brand_words):
                stripped["brand"].append(token)
            elif self._is_model_code(token):
                stripped["model_code"].append(token)
            elif self._is_unit_token(token):
                stripped["unit"].append(token)
            elif self._is_product_type(token):
                stripped["product_type"].append(token)
            elif self._is_product_line(token):
                stripped["product_line"].append(token)
            elif _PURE_NUMBER_RE.match(token):
                stripped["number"].append(token)

        canonical, category, span, confidence = self.extract(text, brand)
        return {
            "input": text,
            "brand": brand,
            "tokens": tokens,
            "residual": residual,
            "stripped": stripped,
            "matched_span": span,
            "canonical_flavor": canonical,
            "flavor_category": category,
            "confidence": confidence,
        }


flavor_extractor = FlavorExtractor()
