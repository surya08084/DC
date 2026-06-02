from __future__ import annotations
from pathlib import Path
from typing import Optional
import pandas as pd
from ..models.compliance import StateRecord

# States known to have formal vapor product registries / directories
_STATES_WITH_REGISTRY: set[str] = {
    "CA", "CO", "CT", "IL", "MA", "MD", "MN", "MT", "NJ", "NY",
    "OR", "RI", "TX", "UT", "VT", "WA",
}


class StateRegistry:
    """
    State directory registry — one record per (CPG_ID, state).
    Supports 50-state coverage. States without a registry are flagged
    so the rules engine can handle them correctly (no registry ≠ illicit).
    """

    def __init__(self) -> None:
        # Key: (cpg_id, state_upper)
        self._store: dict[tuple[str, str], StateRecord] = {}
        self._versions: dict[str, str] = {}   # state → version
        self._known_states: set[str] = set(_STATES_WITH_REGISTRY)

    def load_from_csv(self, path: str | Path, state: str | None = None, version: str = "latest") -> int:
        df = pd.read_csv(path)
        count = 0
        for _, row in df.iterrows():
            s = state or str(row.get("state", "XX")).upper()
            rec = StateRecord(
                cpg_id=str(row["cpg_id"]),
                state=s,
                directory_match_flag=bool(row.get("directory_match_flag", False)),
                directory_match_id=str(row["directory_match_id"]) if pd.notna(row.get("directory_match_id")) else None,
                confidence=float(row.get("confidence", 1.0)),
                effective_from=pd.to_datetime(row["effective_from"]).date() if pd.notna(row.get("effective_from")) else None,
                effective_to=pd.to_datetime(row["effective_to"]).date() if pd.notna(row.get("effective_to")) else None,
                state_has_registry=s in self._known_states,
                directory_model=str(row.get("directory_model", "WHITELIST")),
                enforcement_status=str(row.get("enforcement_status", "IN_EFFECT")),
                flavor_ban_applies=bool(row.get("flavor_ban_applies", False)),
                rule_version=str(row.get("rule_version", version)),
            )
            self._store[(rec.cpg_id, s)] = rec
            self._known_states.add(s)
            count += 1
        if state:
            self._versions[state] = version
        return count

    def get(self, cpg_id: str, state: str) -> Optional[StateRecord]:
        return self._store.get((cpg_id, state.upper()))

    def upsert(self, record: StateRecord) -> None:
        self._store[(record.cpg_id, record.state.upper())] = record

    def all_states(self) -> list[str]:
        return sorted(self._known_states)

    def state_has_registry(self, state: str) -> bool:
        return state.upper() in _STATES_WITH_REGISTRY

    def count(self) -> int:
        return len(self._store)

    def to_df(self) -> pd.DataFrame:
        return pd.DataFrame([r.model_dump() for r in self._store.values()])
