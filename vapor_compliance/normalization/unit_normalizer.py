from __future__ import annotations
import re
import json
from pathlib import Path
from typing import Optional

_DICT_PATH = Path(__file__).parent.parent / "dictionaries" / "unit_mappings.json"
with _DICT_PATH.open() as f:
    _UNIT_MAP = json.load(f)

_NIC_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_NIC_MG_ML   = re.compile(r"(\d+(?:\.\d+)?)\s*mg\s*/?\s*ml", re.I)
_NIC_MG_ONLY = re.compile(r"(\d+(?:\.\d+)?)\s*mg(?!\s*/?\s*ml)(?!\s*ml)", re.I)
_VOLUME_ML   = re.compile(r"(\d+(?:\.\d+)?)\s*ml", re.I)
_PUFF_COUNT  = re.compile(r"(\d[\d,]*)\s*(?:puff|puffs|hit|hits|draw|draws)", re.I)
_PACK_COUNT  = re.compile(r"(\d+)\s*(?:pk|pack|ct|count)", re.I)

_NIC_LOOKUP  = {k.lower(): v for k, v in _UNIT_MAP["nicotine_to_mg_ml"].items()}
_VOL_LOOKUP  = {k.lower(): v for k, v in _UNIT_MAP["volume_to_ml"].items()}
_PROD_MAP    = {k.lower(): v for k, v in _UNIT_MAP["product_type_map"].items()}


def normalize_nicotine(text: str) -> Optional[float]:
    """Return canonical mg/ml value. Percent × 10 = mg/ml for aqueous solutions."""
    t = text.lower().strip()
    if t in _NIC_LOOKUP:
        return _NIC_LOOKUP[t]
    m = _NIC_PERCENT.search(t)
    if m:
        return round(float(m.group(1)) * 10, 2)
    m = _NIC_MG_ML.search(t)
    if m:
        return float(m.group(1))
    m = _NIC_MG_ONLY.search(t)
    if m:
        val = float(m.group(1))
        # freebase nicotine typically < 36mg, salt nic up to 60mg
        return val
    if "zero" in t or "0nic" in t:
        return 0.0
    return None


def normalize_volume(text: str) -> Optional[float]:
    t = text.lower().strip()
    if t in _VOL_LOOKUP:
        return _VOL_LOOKUP[t]
    m = _VOLUME_ML.search(t)
    if m:
        return float(m.group(1))
    return None


def extract_puff_count(text: str) -> Optional[int]:
    m = _PUFF_COUNT.search(text)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def extract_pack_count(text: str) -> Optional[int]:
    m = _PACK_COUNT.search(text, re.I)
    if m:
        return int(m.group(1))
    return None


def normalize_product_type(text: str) -> Optional[str]:
    t = text.lower()
    for key, val in sorted(_PROD_MAP.items(), key=lambda x: -len(x[0])):
        if key in t:
            return val
    return None


def extract_all(text: str) -> dict:
    return {
        "nicotine_mg_ml": normalize_nicotine(text),
        "volume_ml":      normalize_volume(text),
        "puff_count":     extract_puff_count(text),
        "pack_count":     extract_pack_count(text),
        "product_type":   normalize_product_type(text),
    }
