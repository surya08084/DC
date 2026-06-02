from __future__ import annotations
from pathlib import Path
import hashlib
import pandas as pd
from ..models.sku import RawSKU


class CSVParser:
    """Ingest internal POS/STARS shipment data from CSV/Excel files."""

    _HASH_STORE: dict[str, str] = {}   # path → last md5

    def load_raw_skus(self, path: str | Path, source: str = "POS") -> list[RawSKU]:
        path = Path(path)
        df = pd.read_csv(path) if path.suffix == ".csv" else pd.read_excel(path)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        skus: list[RawSKU] = []
        for _, row in df.iterrows():
            sku = RawSKU(
                sku_id=str(row.get("sku_id", row.get("id", ""))),
                source=str(row.get("source", source)),
                raw_name=str(row.get("raw_name", row.get("product_name", row.get("name", "")))),
                manufacturer=str(row["manufacturer"]) if pd.notna(row.get("manufacturer")) else None,
                state=str(row["state"]).upper() if pd.notna(row.get("state")) else None,
                price=float(row["price"]) if pd.notna(row.get("price")) else None,
                retailer_ids=[str(row["retailer_id"])] if pd.notna(row.get("retailer_id")) else [],
            )
            skus.append(sku)
        return skus

    def has_changed(self, path: str | Path) -> bool:
        path = Path(path)
        with open(path, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
        prev = self._HASH_STORE.get(str(path))
        self._HASH_STORE[str(path)] = md5
        return prev != md5
