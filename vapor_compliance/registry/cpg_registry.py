from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
import pandas as pd
from ..models.sku import CanonicalProduct


class CPGRegistry:
    """
    Canonical Product Group registry.
    In-memory store of all reference products (CPGs).
    Loaded from CSV/JSON at startup and rebuilt on each ingestion run.
    """

    def __init__(self) -> None:
        self._store: dict[str, CanonicalProduct] = {}

    def load_from_csv(self, path: str | Path) -> int:
        df = pd.read_csv(path)
        count = 0
        for _, row in df.iterrows():
            p = CanonicalProduct(
                cpg_id=str(row["cpg_id"]),
                canonical_name=str(row["canonical_name"]),
                brand=str(row["brand"]),
                manufacturer=str(row.get("manufacturer", "")),
                product_type=str(row.get("product_type", "")),
                flavor_canonical=str(row.get("flavor_canonical", "")),
                flavor_category=str(row.get("flavor_category", "OTHER")),
                nicotine_mg_ml=float(row.get("nicotine_mg_ml", 0)),
                volume_ml=float(row["volume_ml"]) if pd.notna(row.get("volume_ml")) else None,
                puff_count=int(row["puff_count"]) if pd.notna(row.get("puff_count")) else None,
                pack_count=int(row["pack_count"]) if pd.notna(row.get("pack_count")) else None,
                form_factor=str(row.get("form_factor", "")),
                country_of_origin=str(row["country_of_origin"]) if pd.notna(row.get("country_of_origin")) else None,
                source=str(row.get("source", "cpg_csv")),
            )
            self._store[p.cpg_id] = p
            count += 1
        return count

    def load_from_list(self, products: list[CanonicalProduct]) -> None:
        for p in products:
            self._store[p.cpg_id] = p

    def get(self, cpg_id: str) -> Optional[CanonicalProduct]:
        return self._store.get(cpg_id)

    def all(self) -> list[CanonicalProduct]:
        return list(self._store.values())

    def upsert(self, product: CanonicalProduct) -> None:
        self._store[product.cpg_id] = product

    def count(self) -> int:
        return len(self._store)

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame([p.model_dump(exclude={"embedding"}) for p in self._store.values()])
