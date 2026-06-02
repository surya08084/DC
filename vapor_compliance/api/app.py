from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import tempfile, shutil, os

from ..config import settings
from ..models.sku import RawSKU
from ..models.compliance import ComplianceResult
from ..registry.cpg_registry import CPGRegistry
from ..registry.fda_registry import FDARegistry
from ..registry.state_registry import StateRegistry
from ..pipeline.orchestrator import CompliancePipeline

logger = logging.getLogger(__name__)

# ── Global pipeline singleton ─────────────────────────────────────────────────
_pipeline: Optional[CompliancePipeline] = None


def get_pipeline() -> CompliancePipeline:
    if _pipeline is None:
        raise HTTPException(503, "Pipeline not initialized")
    return _pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline
    logger.info("Starting compliance pipeline…")
    cpg = CPGRegistry()
    fda = FDARegistry()
    state = StateRegistry()
    _pipeline = CompliancePipeline(cpg, fda, state)
    _pipeline.startup()
    logger.info("Pipeline ready")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Vapor Tobacco Compliance API",
    description="SKU-level regulatory resolution across FDA and 50 state directories",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Request / Response models ─────────────────────────────────────────────────

class SKULookupRequest(BaseModel):
    sku_id: str
    raw_name: str
    source: str = "POS"
    manufacturer: Optional[str] = None
    state: Optional[str] = None
    price: Optional[float] = None
    retailer_ids: list[str] = []
    pre_expanded_name: Optional[str] = None
    target_states: Optional[list[str]] = None
    snapshot_date: Optional[date] = None


class AlgorithmStatusResponse(BaseModel):
    ENABLE_EXACT: bool
    ENABLE_FUZZY: bool
    ENABLE_TFIDF: bool
    ENABLE_BM25: bool
    ENABLE_EMBEDDING: bool
    ENABLE_BEHAVIORAL: bool
    ENABLE_LLM: bool


class AlgorithmToggleRequest(BaseModel):
    stage: str   # exact | fuzzy | tfidf | bm25 | embedding | behavioral | llm
    enabled: bool


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "pipeline_ready": _pipeline is not None}


@app.post("/compliance/lookup", response_model=list[ComplianceResult])
def lookup_sku(req: SKULookupRequest):
    """Real-time single-SKU compliance lookup."""
    pipeline = get_pipeline()
    raw = RawSKU(
        sku_id=req.sku_id,
        source=req.source,
        raw_name=req.raw_name,
        manufacturer=req.manufacturer,
        state=req.state,
        price=req.price,
        retailer_ids=req.retailer_ids,
    )
    results = pipeline.process_sku(
        raw,
        pre_expanded_name=req.pre_expanded_name,
        target_states=req.target_states,
        snapshot_date=req.snapshot_date,
    )
    return results


@app.post("/compliance/batch")
def batch_upload(
    file: UploadFile = File(...),
    source: str = Query("POS"),
    states: Optional[str] = Query(None, description="Comma-separated state codes, e.g. TX,CA,NY"),
):
    """Bulk CSV upload → compliance results as JSON."""
    pipeline = get_pipeline()
    target_states = [s.strip().upper() for s in states.split(",")] if states else None

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        df = pipeline.process_from_csv(tmp_path, source=source, target_states=target_states)
        summary = pipeline.get_summary(df)
        return JSONResponse({
            "summary": summary,
            "records": df.to_dict(orient="records"),
        })
    finally:
        os.unlink(tmp_path)


@app.get("/compliance/summary")
def get_summary(
    state: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """Placeholder — wire to your data warehouse in production."""
    return {"message": "Connect to your compliance results table for live summaries"}


@app.get("/algorithms/status", response_model=AlgorithmStatusResponse)
def algorithm_status():
    """
    Show which matching stages are enabled.

    Stage 3 (Semantic Ensemble) runs whichever of tfidf/bm25/embedding are enabled
    IN PARALLEL and combines via voting. Toggle individual Stage 3 members to control
    which methods participate in the ensemble.
    """
    return AlgorithmStatusResponse(
        ENABLE_EXACT=settings.ENABLE_EXACT,
        ENABLE_FUZZY=settings.ENABLE_FUZZY,
        ENABLE_TFIDF=settings.ENABLE_TFIDF,
        ENABLE_BM25=settings.ENABLE_BM25,
        ENABLE_EMBEDDING=settings.ENABLE_EMBEDDING,
        ENABLE_BEHAVIORAL=settings.ENABLE_BEHAVIORAL,
        ENABLE_LLM=settings.ENABLE_LLM,
    )


@app.post("/algorithms/toggle")
def toggle_algorithm(req: AlgorithmToggleRequest):
    """Enable or disable a matching stage at runtime without restart."""
    stage_map = {
        "exact": "ENABLE_EXACT",
        "fuzzy": "ENABLE_FUZZY",
        "tfidf": "ENABLE_TFIDF",
        "bm25": "ENABLE_BM25",
        "embedding": "ENABLE_EMBEDDING",
        "behavioral": "ENABLE_BEHAVIORAL",
        "llm": "ENABLE_LLM",
    }
    attr = stage_map.get(req.stage.lower())
    if not attr:
        raise HTTPException(400, f"Unknown stage '{req.stage}'. Valid: {list(stage_map)}")
    setattr(settings, attr, req.enabled)
    return {"stage": req.stage, "enabled": req.enabled, "message": "Updated in-process (not persisted)"}


@app.get("/algorithms/thresholds")
def get_thresholds():
    return {
        "FUZZY_THRESHOLD": settings.FUZZY_THRESHOLD,
        "TFIDF_THRESHOLD": settings.TFIDF_THRESHOLD,
        "BM25_THRESHOLD": settings.BM25_THRESHOLD,
        "EMBEDDING_THRESHOLD": settings.EMBEDDING_THRESHOLD,
        "AUTO_CLASSIFY_MIN": settings.AUTO_CLASSIFY_MIN,
        "REVIEW_QUEUE_MIN": settings.REVIEW_QUEUE_MIN,
        "LLM_TRIGGER_MAX": settings.LLM_TRIGGER_MAX,
        "BRAND_MIN_SCORE": settings.BRAND_MIN_SCORE,
    }
