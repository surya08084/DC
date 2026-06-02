from __future__ import annotations
from pathlib import Path
from typing import Optional
import pandas as pd
from ..models.compliance import FDARecord


class FDARegistry:
    """
    FDA authorized product registry (MGO list).
    Keyed by CPG ID. Supports temporal queries via effective_from/to.
    Versioned — each load records the source version.
    """

    def __init__(self) -> None:
        self._store: dict[str, FDARecord] = {}
        self._version: str = "unknown"

    def load_from_csv(self, path: str | Path, version: str = "latest") -> int:
        self._version = version
        df = pd.read_csv(path)
        count = 0
        for _, row in df.iterrows():
            rec = FDARecord(
                cpg_id=str(row["cpg_id"]),
                fda_match_flag=bool(row.get("fda_match_flag", False)),
                fda_match_id=str(row["fda_match_id"]) if pd.notna(row.get("fda_match_id")) else None,
                pmta_order_number=str(row["pmta_order_number"]) if pd.notna(row.get("pmta_order_number")) else None,
                authorized_manufacturer=str(row["authorized_manufacturer"]) if pd.notna(row.get("authorized_manufacturer")) else None,
                confidence=float(row.get("confidence", 1.0)),
                effective_from=pd.to_datetime(row["effective_from"]).date() if pd.notna(row.get("effective_from")) else None,
                effective_to=pd.to_datetime(row["effective_to"]).date() if pd.notna(row.get("effective_to")) else None,
                marketing_granted=bool(row.get("marketing_granted", False)),
            )
            self._store[rec.cpg_id] = rec
            count += 1
        return count

    def get(self, cpg_id: str) -> Optional[FDARecord]:
        return self._store.get(cpg_id)

    def is_authorized(self, cpg_id: str) -> bool:
        rec = self._store.get(cpg_id)
        return rec.fda_match_flag if rec else False

    def upsert(self, record: FDARecord) -> None:
        self._store[record.cpg_id] = record

    def count(self) -> int:
        return len(self._store)

    def version(self) -> str:
        return self._version

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame([r.model_dump() for r in self._store.values()])
