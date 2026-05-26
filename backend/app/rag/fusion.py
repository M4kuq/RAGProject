from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from app.rag.retrieval import VectorSearchCandidate
from app.rag.strategy import FusionMethod, RetrievalSource

MAX_FUSED_SCORE: Final = 1.0


@dataclass(frozen=True)
class FusionInput:
    document_chunk_id: int
    dense_score: float | None = None
    sparse_score: float | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None


def fuse_candidates(
    *,
    dense_candidates: list[VectorSearchCandidate],
    sparse_candidates: list[VectorSearchCandidate],
    method: FusionMethod,
    limit: int,
    rrf_k: int,
    dense_weight: float,
    sparse_weight: float,
) -> list[VectorSearchCandidate]:
    if limit < 1:
        return []

    inputs = _dedupe_candidates(
        dense_candidates=dense_candidates,
        sparse_candidates=sparse_candidates,
    )
    if not inputs:
        return []

    if method == FusionMethod.WEIGHTED:
        scored = _weighted_fusion(
            inputs,
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
        )
    else:
        scored = _rrf_fusion(
            inputs,
            rrf_k=rrf_k,
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
        )

    ranked = sorted(
        scored,
        key=lambda item: (
            -item[1],
            item[0].dense_rank if item[0].dense_rank is not None else math.inf,
            item[0].sparse_rank if item[0].sparse_rank is not None else math.inf,
            item[0].document_chunk_id,
        ),
    )[:limit]

    return [
        VectorSearchCandidate(
            document_chunk_id=input_item.document_chunk_id,
            retrieval_score=round(fused_score, 6),
            qdrant_order=index,
            payload={
                "retrieval_source": RetrievalSource.HYBRID.value,
                "fusion_method": method.value,
                "dense_score": _round_optional(input_item.dense_score),
                "sparse_score": _round_optional(input_item.sparse_score),
                "fused_score": round(fused_score, 6),
                "dense_rank": input_item.dense_rank,
                "sparse_rank": input_item.sparse_rank,
            },
        )
        for index, (input_item, fused_score) in enumerate(ranked, start=1)
    ]


def _dedupe_candidates(
    *,
    dense_candidates: list[VectorSearchCandidate],
    sparse_candidates: list[VectorSearchCandidate],
) -> list[FusionInput]:
    by_chunk_id: dict[int, FusionInput] = {}
    for index, candidate in enumerate(dense_candidates, start=1):
        document_chunk_id = candidate.document_chunk_id
        if document_chunk_id is None:
            continue
        existing = by_chunk_id.get(document_chunk_id)
        dense_rank = min(existing.dense_rank, index) if existing and existing.dense_rank else index
        dense_score = _max_optional_score(
            existing.dense_score if existing else None,
            _finite_score(candidate.retrieval_score),
        )
        by_chunk_id[document_chunk_id] = FusionInput(
            document_chunk_id=document_chunk_id,
            dense_score=dense_score,
            sparse_score=existing.sparse_score if existing else None,
            dense_rank=dense_rank,
            sparse_rank=existing.sparse_rank if existing else None,
        )

    for index, candidate in enumerate(sparse_candidates, start=1):
        document_chunk_id = candidate.document_chunk_id
        if document_chunk_id is None:
            continue
        existing = by_chunk_id.get(document_chunk_id)
        sparse_rank = (
            min(existing.sparse_rank, index) if existing and existing.sparse_rank else index
        )
        sparse_score = _max_optional_score(
            existing.sparse_score if existing else None,
            _finite_score(candidate.retrieval_score),
        )
        by_chunk_id[document_chunk_id] = FusionInput(
            document_chunk_id=document_chunk_id,
            dense_score=existing.dense_score if existing else None,
            sparse_score=sparse_score,
            dense_rank=existing.dense_rank if existing else None,
            sparse_rank=sparse_rank,
        )

    return list(by_chunk_id.values())


def _rrf_fusion(
    inputs: list[FusionInput],
    *,
    rrf_k: int,
    dense_weight: float,
    sparse_weight: float,
) -> list[tuple[FusionInput, float]]:
    raw_scores: list[tuple[FusionInput, float]] = []
    for input_item in inputs:
        score = 0.0
        if input_item.dense_rank is not None:
            score += dense_weight / (rrf_k + input_item.dense_rank)
        if input_item.sparse_rank is not None:
            score += sparse_weight / (rrf_k + input_item.sparse_rank)
        raw_scores.append((input_item, score))
    return _normalize_fused_scores(raw_scores)


def _weighted_fusion(
    inputs: list[FusionInput],
    *,
    dense_weight: float,
    sparse_weight: float,
) -> list[tuple[FusionInput, float]]:
    total_weight = dense_weight + sparse_weight
    if total_weight <= 0:
        return []
    dense_bounds = _score_bounds(item.dense_score for item in inputs if item.dense_rank is not None)
    sparse_bounds = _score_bounds(
        item.sparse_score for item in inputs if item.sparse_rank is not None
    )
    raw_scores: list[tuple[FusionInput, float]] = []
    for input_item in inputs:
        if not _has_positive_weighted_source(
            input_item,
            dense_weight=dense_weight,
            sparse_weight=sparse_weight,
        ):
            continue
        dense_component = (
            _normalized_source_score(
                input_item.dense_score,
                dense_bounds,
            )
            * dense_weight
        )
        sparse_component = (
            _normalized_source_score(
                input_item.sparse_score,
                sparse_bounds,
            )
            * sparse_weight
        )
        raw_scores.append((input_item, (dense_component + sparse_component) / total_weight))
    return _normalize_shifted_scores(raw_scores)


def _normalize_fused_scores(
    raw_scores: list[tuple[FusionInput, float]],
) -> list[tuple[FusionInput, float]]:
    max_score = max((score for _, score in raw_scores), default=0.0)
    if max_score <= 0:
        return []
    return [
        (input_item, min(MAX_FUSED_SCORE, max(0.0, score / max_score)))
        for input_item, score in raw_scores
        if score > 0
    ]


def _normalize_shifted_scores(
    raw_scores: list[tuple[FusionInput, float]],
) -> list[tuple[FusionInput, float]]:
    finite_scores = [
        (input_item, score) for input_item, score in raw_scores if math.isfinite(score)
    ]
    if not finite_scores:
        return []
    min_score = min(score for _, score in finite_scores)
    max_score = max(score for _, score in finite_scores)
    if max_score == min_score:
        return [(input_item, MAX_FUSED_SCORE) for input_item, _ in finite_scores]
    score_range = max_score - min_score
    return [
        (input_item, min(MAX_FUSED_SCORE, max(0.0, (score - min_score) / score_range)))
        for input_item, score in finite_scores
    ]


def _normalized_source_score(
    score: float | None,
    bounds: tuple[float, float] | None,
) -> float:
    if score is None or bounds is None:
        return 0.0
    min_score, max_score = bounds
    if max_score == min_score:
        return MAX_FUSED_SCORE
    return min(MAX_FUSED_SCORE, max(0.0, (score - min_score) / (max_score - min_score)))


def _score_bounds(scores: Iterable[float | None]) -> tuple[float, float] | None:
    finite_scores = [
        float(score) for score in scores if score is not None and math.isfinite(float(score))
    ]
    if not finite_scores:
        return None
    return min(finite_scores), max(finite_scores)


def _has_positive_weighted_source(
    input_item: FusionInput,
    *,
    dense_weight: float,
    sparse_weight: float,
) -> bool:
    return (dense_weight > 0 and input_item.dense_score is not None) or (
        sparse_weight > 0 and input_item.sparse_score is not None
    )


def _max_optional_score(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _finite_score(value: float) -> float | None:
    score = float(value)
    if not math.isfinite(score):
        return None
    return score


def _round_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)
