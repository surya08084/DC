from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class RawSKU(BaseModel):
    sku_id: str
    source: str                          # STARS | POS | MANUAL
    raw_name: str
    manufacturer: Optional[str] = None
    state: Optional[str] = None          # 2-letter state code
    price: Optional[float] = None
    retailer_ids: list[str] = []
    load_timestamp: datetime = Field(default_factory=datetime.utcnow)
    source_version: str = "1.0"


class NormalizedSKU(BaseModel):
    raw_sku_id: str
    brand: Optional[str] = None
    manufacturer: Optional[str] = None
    product_type: Optional[str] = None   # DISPOSABLE | POD | CARTRIDGE | MOD
    flavor: Optional[str] = None
    flavor_canonical: Optional[str] = None
    flavor_category: Optional[str] = None  # MENTHOL | TOBACCO | FRUIT | OTHER
    nicotine_mg_ml: Optional[float] = None
    volume_ml: Optional[float] = None
    puff_count: Optional[int] = None
    pack_count: Optional[int] = None
    form_factor: Optional[str] = None
    raw_name: str
    normalized_name: str
    abbreviations_expanded: dict[str, str] = {}
    normalization_confidence: float = 0.0
    normalization_method: str = "dictionary"   # dictionary | llm | hybrid
    high_risk_flags: list[str] = []
    source: str = ""


class CanonicalProduct(BaseModel):
    cpg_id: str
    canonical_name: str
    brand: str
    manufacturer: str
    product_type: str
    flavor_canonical: str
    flavor_category: str = "OTHER"
    nicotine_mg_ml: float
    volume_ml: Optional[float] = None
    puff_count: Optional[int] = None
    pack_count: Optional[int] = None
    form_factor: str
    country_of_origin: Optional[str] = None
    source: str = ""
    embedding: Optional[list[float]] = None
