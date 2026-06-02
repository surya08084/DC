from __future__ import annotations
import hashlib
import json
import logging
import time
from typing import Optional

from pydantic import BaseModel, Field

from ..config import settings

logger = logging.getLogger(__name__)

# ── Output schema that the LLM must return ────────────────────────────────────

class SKUExtractionResult(BaseModel):
    """Structured attributes extracted by the LLM for one raw SKU."""
    raw_name: str
    brand: Optional[str] = None
    manufacturer: Optional[str] = None
    product_line: Optional[str] = None          # ACE, Alto, BC5000, Pulse …
    flavor_raw: Optional[str] = None            # exactly as the LLM reads it
    flavor_canonical: Optional[str] = None      # mapped to known canonical
    flavor_category: Optional[str] = None       # TOBACCO|MENTHOL|FRUIT|DESSERT|SPICE|OTHER
    nicotine_strength_raw: Optional[str] = None  # "5%", "50mg", "3%"
    nicotine_mg_ml: Optional[float] = None      # always in mg/ml (5% → 50.0)
    form_factor: Optional[str] = None           # POD|DISPOSABLE|CARTRIDGE|MOD|E-LIQUID
    volume_ml: Optional[float] = None
    puff_count: Optional[int] = None
    pack_count: Optional[int] = None
    normalized_sku: Optional[str] = None        # "JUUL Virginia Tobacco 50mg Pod"
    abbreviations_expanded: dict[str, str] = Field(default_factory=dict)
    extraction_confidence: float = 0.0          # LLM self-assessed 0.0–1.0
    ambiguities: list[str] = Field(default_factory=list)


# ── Prompt templates ──────────────────────────────────────────────────────────

_SYSTEM = """\
You are an expert in US vapor/tobacco product data standardization.

Your task: parse raw SKU names from retail POS and shipment systems into \
structured JSON. These names are highly abbreviated and inconsistent.

Rules:
- brand: canonical brand name (JUUL, NJOY, VUSE, ELF BAR, GEEK BAR …)
- product_line: sub-model name that is NOT the flavor (ACE, Alto, BC5000, Pulse …)
- flavor_raw: the flavor text exactly as it appears after stripping brand/line/units
- flavor_canonical: map to one of these canonical flavors:
    Virginia Tobacco, Menthol, Mango, Strawberry, Blueberry, Watermelon,
    Peach, Grape, Lemon, Vanilla, Caramel, Cinnamon, Bubble Gum, Tropical,
    Cream, Berry, Cucumber, RY4, Golden Tobacco
  Use null if the flavor is genuinely unknown.
- flavor_category: TOBACCO | MENTHOL | FRUIT | DESSERT | SPICE | OTHER
- nicotine_mg_ml: ALWAYS convert to mg/ml. Rule: percent × 10 = mg/ml (5% = 50.0)
- form_factor: POD | DISPOSABLE | CARTRIDGE | MOD | E-LIQUID
- normalized_sku: reconstruct a clean full name: "<Brand> <Flavor> <NicMgMl>mg/ml <FormFactor>"
- abbreviations_expanded: dict of every abbreviation you expanded {"VT":"Virginia Tobacco"}
- extraction_confidence: your honest confidence 0.0–1.0
- ambiguities: list anything you were unsure about

Respond with ONLY valid JSON — no markdown, no explanation."""

_BATCH_USER_TEMPLATE = """\
Extract structured attributes for each of these {n} SKU names.
Return a JSON array of {n} objects, one per SKU, in the same order.

SKUs:
{sku_list}"""

_SINGLE_USER_TEMPLATE = """\
Extract structured attributes for this SKU name:
"{raw_name}"

Return a single JSON object."""


class LLMExtractor:
    """
    Calls Claude API to extract structured product attributes from raw SKU names.

    Key design principles:
    - Batch up to BATCH_SIZE SKUs per API call to minimise cost and latency.
    - Cache results by MD5 of the raw name — same SKU never calls the API twice.
    - Graceful failure: returns None on any error so the pipeline can fall back
      to the dictionary/residual method.
    - Self-assessed confidence returned by the LLM feeds into validation.
    """

    BATCH_SIZE = 20         # max SKUs per API call
    MAX_RETRIES = 2
    RETRY_DELAY = 2.0       # seconds

    def __init__(self) -> None:
        self._client = None
        self._cache: dict[str, SKUExtractionResult] = {}
        self._init_client()

    def _init_client(self) -> None:
        if not settings.ANTHROPIC_API_KEY:
            logger.info("LLMExtractor: ANTHROPIC_API_KEY not set — LLM extraction disabled")
            return
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            logger.info("LLMExtractor: client ready (model=%s)", settings.LLM_MODEL)
        except ImportError:
            logger.warning("LLMExtractor: anthropic package not installed")

    @property
    def available(self) -> bool:
        return self._client is not None

    # ── Public API ────────────────────────────────────────────────────────────

    def extract_one(self, raw_name: str) -> Optional[SKUExtractionResult]:
        """Extract attributes for a single SKU. Uses cache."""
        key = self._hash(raw_name)
        if key in self._cache:
            return self._cache[key]

        if not self.available:
            return None

        result = self._call_single(raw_name)
        if result:
            self._cache[key] = result
        return result

    def extract_batch(
        self, raw_names: list[str]
    ) -> dict[str, Optional[SKUExtractionResult]]:
        """
        Extract attributes for a list of SKU names.
        Returns a dict keyed by raw_name.
        Deduplicates by cache — only uncached names reach the API.
        """
        output: dict[str, Optional[SKUExtractionResult]] = {}
        uncached: list[str] = []

        for name in raw_names:
            key = self._hash(name)
            if key in self._cache:
                output[name] = self._cache[key]
            else:
                uncached.append(name)

        if not uncached or not self.available:
            for name in uncached:
                output[name] = None
            return output

        # Process in batches
        for i in range(0, len(uncached), self.BATCH_SIZE):
            chunk = uncached[i : i + self.BATCH_SIZE]
            results = self._call_batch(chunk)
            for name, result in zip(chunk, results):
                output[name] = result
                if result:
                    self._cache[self._hash(name)] = result

        return output

    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()

    # ── Internal API calls ────────────────────────────────────────────────────

    def _call_single(self, raw_name: str) -> Optional[SKUExtractionResult]:
        prompt = _SINGLE_USER_TEMPLATE.format(raw_name=raw_name)
        text = self._llm_call(prompt)
        if not text:
            return None
        obj = self._parse_single(text, raw_name)
        if obj:
            obj.raw_name = raw_name
        return obj

    def _call_batch(
        self, raw_names: list[str]
    ) -> list[Optional[SKUExtractionResult]]:
        sku_list = "\n".join(f'{i+1}. "{name}"' for i, name in enumerate(raw_names))
        prompt = _BATCH_USER_TEMPLATE.format(n=len(raw_names), sku_list=sku_list)
        text = self._llm_call(prompt)
        if not text:
            return [None] * len(raw_names)
        return self._parse_batch(text, raw_names)

    def _llm_call(self, user_prompt: str) -> Optional[str]:
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self._client.messages.create(
                    model=settings.LLM_MODEL,
                    max_tokens=4096,
                    system=_SYSTEM,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return response.content[0].text.strip()
            except Exception as e:
                logger.warning("LLMExtractor: attempt %d failed — %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY * (2 ** attempt))
        return None

    # ── JSON parsers ──────────────────────────────────────────────────────────

    def _parse_single(
        self, text: str, raw_name: str
    ) -> Optional[SKUExtractionResult]:
        try:
            text = self._strip_markdown(text)
            data = json.loads(text)
            data["raw_name"] = raw_name
            return SKUExtractionResult(**data)
        except Exception as e:
            logger.warning("LLMExtractor: parse error for '%s': %s", raw_name, e)
            return None

    def _parse_batch(
        self, text: str, raw_names: list[str]
    ) -> list[Optional[SKUExtractionResult]]:
        results: list[Optional[SKUExtractionResult]] = []
        try:
            text = self._strip_markdown(text)
            items = json.loads(text)
            if not isinstance(items, list):
                items = [items]
            for i, (name, item) in enumerate(zip(raw_names, items)):
                try:
                    item["raw_name"] = name
                    results.append(SKUExtractionResult(**item))
                except Exception as e:
                    logger.warning("LLMExtractor: parse error item %d '%s': %s", i, name, e)
                    results.append(None)
            # Pad if LLM returned fewer items than expected
            while len(results) < len(raw_names):
                results.append(None)
        except Exception as e:
            logger.warning("LLMExtractor: batch parse error: %s", e)
            return [None] * len(raw_names)
        return results

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove ```json ... ``` fences if present."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return text.strip()

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.md5(text.lower().strip().encode()).hexdigest()


llm_extractor = LLMExtractor()
