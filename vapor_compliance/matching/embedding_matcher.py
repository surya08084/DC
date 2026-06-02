from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional
import numpy as np
from ..models.sku import NormalizedSKU, CanonicalProduct
from ..models.match import MatchResult, MatchStage
from ..config import settings

logger = logging.getLogger(__name__)


class EmbeddingMatcher:
    """
    Stage 3c — fastembed (BAAI/bge-small-en-v1.5) + FAISS vector search.

    Design:
      - Model is loaded ONCE at service startup and kept in memory.
      - Reference corpus embeddings are pre-computed at ingestion time,
        stored in FAISS index on disk, and loaded at startup.
      - At match time, only the incoming SKU is embedded (single vector).
      - No re-download after first initialization.

    Disabled by default if fastembed or faiss not installed —
    falls back gracefully.
    """

    _instance: Optional["EmbeddingMatcher"] = None

    def __init__(self) -> None:
        self._model = None
        self._faiss_index = None
        self._meta: list[dict] = []   # [{cpg_id, canonical_name, source}]
        self._catalog: list[CanonicalProduct] = []
        self._ready = False
        self._load_model()

    def _load_model(self) -> None:
        try:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(settings.EMBEDDING_MODEL)
            logger.info("EmbeddingMatcher: fastembed model loaded — %s", settings.EMBEDDING_MODEL)
        except ImportError:
            logger.warning("EmbeddingMatcher: fastembed not installed — Stage 3c disabled")
            return
        except Exception as e:
            logger.warning("EmbeddingMatcher: model load failed — %s", e)
            return
        self._try_load_faiss()

    def _try_load_faiss(self) -> None:
        idx_path = Path(settings.FAISS_INDEX_PATH)
        meta_path = Path(settings.FAISS_META_PATH)
        if idx_path.exists() and meta_path.exists():
            try:
                import faiss
                self._faiss_index = faiss.read_index(str(idx_path))
                with meta_path.open() as f:
                    self._meta = json.load(f)
                self._ready = True
                logger.info("EmbeddingMatcher: FAISS index loaded (%d vectors)", self._faiss_index.ntotal)
            except Exception as e:
                logger.warning("EmbeddingMatcher: FAISS load failed — %s", e)

    def build_index(self, products: list[CanonicalProduct]) -> None:
        """Pre-compute embeddings for all reference products. Call at ingestion time."""
        if self._model is None:
            return
        try:
            import faiss
        except ImportError:
            logger.warning("EmbeddingMatcher: faiss not installed — skipping index build")
            return

        self._catalog = products
        texts = [self._to_text(p) for p in products]
        embeddings = np.array(list(self._model.embed(texts)), dtype=np.float32)

        # Normalize for cosine similarity via inner product
        faiss.normalize_L2(embeddings)
        dim = embeddings.shape[1]
        self._faiss_index = faiss.IndexFlatIP(dim)
        self._faiss_index.add(embeddings)

        self._meta = [
            {"cpg_id": p.cpg_id, "canonical_name": p.canonical_name, "source": p.source}
            for p in products
        ]

        # Persist to disk
        Path(settings.FAISS_INDEX_PATH).parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._faiss_index, settings.FAISS_INDEX_PATH)
        with open(settings.FAISS_META_PATH, "w") as f:
            json.dump(self._meta, f)

        self._ready = True
        logger.info("EmbeddingMatcher: built FAISS index with %d vectors", len(products))

    def _to_text(self, p: CanonicalProduct) -> str:
        return (
            f"{p.brand} {p.flavor_canonical} {p.nicotine_mg_ml}mg "
            f"{p.form_factor} {p.product_type} {p.canonical_name}"
        )

    def _sku_text(self, sku: NormalizedSKU) -> str:
        return (
            f"{sku.brand or ''} {sku.flavor_canonical or sku.flavor or ''} "
            f"{sku.nicotine_mg_ml or ''}mg {sku.form_factor or ''} "
            f"{sku.product_type or ''} {sku.normalized_name}"
        )

    def match(self, sku: NormalizedSKU, threshold: float = None) -> Optional[MatchResult]:
        if not self._ready or self._model is None:
            return None
        threshold = threshold if threshold is not None else settings.EMBEDDING_THRESHOLD

        try:
            import faiss
            query_vec = np.array(list(self._model.embed([self._sku_text(sku)])), dtype=np.float32)
            faiss.normalize_L2(query_vec)
            scores, indices = self._faiss_index.search(query_vec, k=1)
            top_score = float(scores[0][0])
            top_idx = int(indices[0][0])

            if top_score >= threshold and top_idx >= 0:
                meta = self._meta[top_idx]
                return MatchResult(
                    query_sku_id=sku.raw_sku_id,
                    matched_cpg_id=meta["cpg_id"],
                    match_stage=MatchStage.EMBEDDING,
                    confidence=round(top_score, 3),
                    match_explanation=f"fastembed cosine similarity={top_score:.3f}",
                    matched_source=meta["source"],
                    is_review_required=top_score < settings.AUTO_CLASSIFY_MIN,
                    candidates_considered=self._faiss_index.ntotal,
                    stage_scores={"embedding": round(top_score, 3)},
                )
        except Exception as e:
            logger.warning("EmbeddingMatcher.match failed: %s", e)
        return None

    def retrieve_candidates(self, sku: NormalizedSKU, top_n: int = 10) -> list[tuple[str, float]]:
        """Return top-N (cpg_id, score) for re-ranking by later stages."""
        if not self._ready or self._model is None:
            return []
        try:
            import faiss
            query_vec = np.array(list(self._model.embed([self._sku_text(sku)])), dtype=np.float32)
            faiss.normalize_L2(query_vec)
            scores, indices = self._faiss_index.search(query_vec, k=top_n)
            return [
                (self._meta[int(idx)]["cpg_id"], float(score))
                for idx, score in zip(indices[0], scores[0])
                if idx >= 0
            ]
        except Exception as e:
            logger.warning("EmbeddingMatcher.retrieve_candidates failed: %s", e)
            return []
