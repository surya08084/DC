from __future__ import annotations
import logging
from datetime import date
from typing import Optional
import pandas as pd

from ..models.sku import RawSKU, NormalizedSKU
from ..models.match import MatchResult
from ..models.compliance import ComplianceResult
from ..normalization.normalizer import Normalizer
from ..matching.entity_resolver import EntityResolver
from ..compliance.decision_engine import DecisionEngine
from ..registry.cpg_registry import CPGRegistry
from ..registry.fda_registry import FDARegistry
from ..registry.state_registry import StateRegistry

logger = logging.getLogger(__name__)


class CompliancePipeline:
    """
    End-to-end pipeline orchestrator.

    Flow per SKU:
      RawSKU → Normalize → EntityResolve → DecisionEngine → ComplianceResult[]

    Supports:
      - Single SKU lookup (real-time API)
      - Batch processing (POS scan file, STARS shipment file)
      - Multi-state expansion (one SKU → N state results)
    """

    def __init__(
        self,
        cpg_registry: CPGRegistry,
        fda_registry: FDARegistry,
        state_registry: StateRegistry,
        behavioral_signals: dict[str, dict] | None = None,
    ) -> None:
        self.normalizer = Normalizer()
        self.resolver = EntityResolver(behavioral_signals)
        self.decision_engine = DecisionEngine(fda_registry, state_registry)
        self.cpg_registry = cpg_registry
        self.fda_registry = fda_registry
        self.state_registry = state_registry

    def startup(self) -> None:
        """Build all in-memory indexes. Call once at service startup."""
        products = self.cpg_registry.all()
        self.resolver.build_all_indexes(products)
        logger.info("CompliancePipeline: startup complete — %d CPG products indexed", len(products))

    def process_sku(
        self,
        raw_sku: RawSKU,
        pre_expanded_name: Optional[str] = None,
        target_states: Optional[list[str]] = None,
        snapshot_date: Optional[date] = None,
    ) -> list[ComplianceResult]:
        """Process a single SKU → list of per-state compliance results."""
        normalized = self.normalizer.normalize(raw_sku, pre_expanded_name)
        match = self.resolver.resolve(normalized, raw_sku)
        return self.decision_engine.decide(
            raw_sku, normalized, match, target_states, snapshot_date
        )

    def process_batch(
        self,
        raw_skus: list[RawSKU],
        target_states: Optional[list[str]] = None,
        snapshot_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """Process a batch → flat DataFrame (SKU × State)."""
        self.resolver.llm.reset_call_count()

        all_results: list[dict] = []
        for raw in raw_skus:
            try:
                results = self.process_sku(raw, target_states=target_states, snapshot_date=snapshot_date)
                for r in results:
                    row = r.model_dump()
                    row.pop("fda_record", None)
                    row.pop("state_record", None)
                    all_results.append(row)
            except Exception as e:
                logger.error("Pipeline error on SKU %s: %s", raw.sku_id, e)
                all_results.append({
                    "sku_id": raw.sku_id,
                    "state": raw.state or "?",
                    "compliance_status": "UNKNOWN",
                    "reason": f"Processing error: {e}",
                    "confidence": 0.0,
                })

        df = pd.DataFrame(all_results)
        return df

    def process_from_csv(
        self,
        path: str,
        source: str = "POS",
        target_states: Optional[list[str]] = None,
        output_path: Optional[str] = None,
    ) -> pd.DataFrame:
        from ..ingestion.csv_parser import CSVParser
        parser = CSVParser()
        raw_skus = parser.load_raw_skus(path, source=source)
        logger.info("CompliancePipeline: loaded %d SKUs from %s", len(raw_skus), path)
        df = self.process_batch(raw_skus, target_states=target_states)
        if output_path:
            df.to_csv(output_path, index=False)
            logger.info("CompliancePipeline: results saved to %s", output_path)
        return df

    def get_summary(self, results_df: pd.DataFrame) -> dict:
        """Summarize compliance results by status and state."""
        if results_df.empty:
            return {}
        summary = {
            "total_skus": results_df["sku_id"].nunique(),
            "total_records": len(results_df),
            "by_status": results_df["compliance_status"].value_counts().to_dict(),
            "avg_confidence": round(results_df["confidence"].mean(), 3),
            "review_required": int((results_df["compliance_status"] == "REVIEW_REQUIRED").sum()),
            "by_state": (
                results_df.groupby("state")["compliance_status"]
                .value_counts()
                .unstack(fill_value=0)
                .to_dict()
            ),
        }
        return summary
