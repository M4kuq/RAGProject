from __future__ import annotations

from app.rag.fusion import fuse_candidates
from app.rag.retrieval import VectorSearchCandidate
from app.rag.strategy import FusionMethod


class HybridRetrievalStrategy:
    def fuse(
        self,
        *,
        dense_candidates: list[VectorSearchCandidate],
        sparse_candidates: list[VectorSearchCandidate],
        fusion_method: FusionMethod,
        limit: int,
        rrf_k: int,
        dense_weight: float,
        sparse_weight: float,
    ) -> list[VectorSearchCandidate]:
        return fuse_candidates(
            dense_candidates=dense_candidates,
            sparse_candidates=sparse_candidates,
            method=fusion_method,
            limit=limit,
            rrf_k=rrf_k,
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
        )
