from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

from app.ingest.qdrant import InMemoryQdrantClient


class RetrievalError(RuntimeError):
    def __init__(
        self,
        error_code: str = "retrieval_failed",
        message: str = "Retrieval failed.",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class RetrievalFilters:
    logical_document_ids: tuple[int, ...] = ()
    modality: str = "text"


@dataclass(frozen=True)
class VectorSearchCandidate:
    document_chunk_id: int | None
    retrieval_score: float
    qdrant_order: int
    payload: dict[str, object]


class VectorSearchClient(Protocol):
    def search(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters,
    ) -> list[VectorSearchCandidate]: ...


class HttpQdrantSearchClient:
    def __init__(self, *, url: str, timeout_seconds: float) -> None:
        self.url = url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def search(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters,
    ) -> list[VectorSearchCandidate]:
        if limit < 1:
            raise RetrievalError()
        try:
            response = httpx.post(
                f"{self.url}/collections/{collection_name}/points/search",
                json={
                    "vector": [float(value) for value in query_vector],
                    "limit": limit,
                    "with_payload": True,
                    "filter": _qdrant_filter(filters),
                },
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise RetrievalError() from exc
        if response.status_code >= 400:
            raise RetrievalError()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RetrievalError() from exc
        return _parse_qdrant_results(payload)


class InMemoryVectorSearchClient:
    def __init__(self, qdrant_client: InMemoryQdrantClient) -> None:
        self.qdrant_client = qdrant_client

    def search(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters,
    ) -> list[VectorSearchCandidate]:
        if limit < 1:
            raise RetrievalError()
        points = list(self.qdrant_client.points.get(collection_name, {}).values())
        candidates: list[VectorSearchCandidate] = []
        for point in points:
            if not _payload_matches_filters(point.payload, filters):
                continue
            document_chunk_id = _candidate_document_chunk_id(
                point.payload,
                fallback_id=point.point_id,
            )
            candidates.append(
                VectorSearchCandidate(
                    document_chunk_id=document_chunk_id,
                    retrieval_score=_cosine_similarity(query_vector, point.vector),
                    qdrant_order=0,
                    payload=dict(point.payload),
                )
            )
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                candidate.retrieval_score,
                -(candidate.document_chunk_id or 0),
            ),
            reverse=True,
        )[:limit]
        return [
            VectorSearchCandidate(
                document_chunk_id=candidate.document_chunk_id,
                retrieval_score=candidate.retrieval_score,
                qdrant_order=index,
                payload=candidate.payload,
            )
            for index, candidate in enumerate(ranked, start=1)
        ]


def _qdrant_filter(filters: RetrievalFilters) -> dict[str, object]:
    must: list[dict[str, object]] = [
        {"key": "is_active", "match": {"value": True}},
        {"key": "document_version_status", "match": {"value": "ready"}},
        {"key": "logical_document_status", "match": {"value": "active"}},
        {"key": "modality", "match": {"value": filters.modality}},
    ]
    if filters.logical_document_ids:
        must.append(
            {
                "key": "logical_document_id",
                "match": {"any": list(filters.logical_document_ids)},
            }
        )
    return {"must": must}


def _parse_qdrant_results(payload: object) -> list[VectorSearchCandidate]:
    if not isinstance(payload, dict):
        raise RetrievalError()
    result = payload.get("result")
    if not isinstance(result, list):
        raise RetrievalError()
    candidates: list[VectorSearchCandidate] = []
    for index, item in enumerate(result, start=1):
        if not isinstance(item, dict):
            continue
        raw_payload = item.get("payload")
        if isinstance(raw_payload, dict):
            point_payload = dict(cast(dict[str, object], raw_payload))
        else:
            point_payload = {}
        document_chunk_id = _candidate_document_chunk_id(point_payload, fallback_id=item.get("id"))
        score = _finite_float(item.get("score"))
        if score is None:
            continue
        candidates.append(
            VectorSearchCandidate(
                document_chunk_id=document_chunk_id,
                retrieval_score=score,
                qdrant_order=index,
                payload=point_payload,
            )
        )
    return candidates


def _payload_matches_filters(payload: dict[str, object], filters: RetrievalFilters) -> bool:
    if payload.get("is_active") is not True:
        return False
    if payload.get("document_version_status") != "ready":
        return False
    if payload.get("logical_document_status") != "active":
        return False
    if payload.get("modality") != filters.modality:
        return False
    if filters.logical_document_ids:
        logical_document_id = payload.get("logical_document_id")
        if logical_document_id not in set(filters.logical_document_ids):
            return False
    return True


def _candidate_document_chunk_id(
    payload: dict[str, object],
    *,
    fallback_id: object,
) -> int | None:
    payload_id = payload.get("document_chunk_id")
    if isinstance(payload_id, int) and not isinstance(payload_id, bool) and payload_id > 0:
        return payload_id
    if isinstance(fallback_id, int) and not isinstance(fallback_id, bool) and fallback_id > 0:
        return fallback_id
    return None


def _finite_float(value: object) -> float | None:
    try:
        result = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise RetrievalError()
    left_norm = math.sqrt(sum(float(value) * float(value) for value in left))
    right_norm = math.sqrt(sum(float(value) * float(value) for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    return dot / (left_norm * right_norm)
