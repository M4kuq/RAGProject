from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from app.core.config import Settings


class RerankError(RuntimeError):
    def __init__(
        self,
        error_code: str = "rerank_failed",
        message: str = "Rerank failed.",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class RerankCandidate:
    document_chunk_id: int
    text: str
    retrieval_score: float


@dataclass(frozen=True)
class RerankResult:
    document_chunk_id: int
    rerank_score: float
    rerank_order: int


class RerankerClient(Protocol):
    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankResult]: ...


class FakeRerankerClient:
    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankResult]:
        scored = [
            (
                _fake_rerank_score(query, candidate),
                candidate.document_chunk_id,
            )
            for candidate in candidates
        ]
        ranked = sorted(scored, key=lambda item: (item[0], -item[1]), reverse=True)
        return [
            RerankResult(
                document_chunk_id=document_chunk_id,
                rerank_score=score,
                rerank_order=index,
            )
            for index, (score, document_chunk_id) in enumerate(ranked, start=1)
        ]


class NoopRerankerClient:
    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankResult]:
        return [
            RerankResult(
                document_chunk_id=candidate.document_chunk_id,
                rerank_score=min(1.0, max(0.0, candidate.retrieval_score)),
                rerank_order=index,
            )
            for index, candidate in enumerate(candidates, start=1)
        ]


class LocalRerankerClient:
    def __init__(self, *, model_name: str, score_min: float, score_max: float) -> None:
        self.model_name = model_name
        self.score_min = score_min
        self.score_max = score_max
        # Instance-level lazy cache of the loaded CrossEncoder: the heavy model is
        # loaded at most once per LocalRerankerClient instance, not per rerank call.
        # The cache lifetime therefore matches the owning RagService instance. In the
        # API path RagService is built per request (rag_search_service dependency ->
        # create_rag_service -> create_reranker), so for the local reranker to load
        # the model only once the RagService (and thus this client) is expected to be
        # constructed as a process-singleton rather than per request; deployments that
        # use the "local" reranker should wire RagService that way.
        self._model: object | None = None

    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankResult]:
        if not candidates:
            return []
        model = self._load_model()
        pairs = [(query, candidate.text) for candidate in candidates]
        try:
            raw_scores = model.predict(pairs)  # type: ignore[attr-defined]
        except Exception as exc:
            raise RerankError() from exc
        scores = _to_score_list(raw_scores, expected_count=len(candidates))
        scored = [
            (
                normalize_rerank_score(score, score_min=self.score_min, score_max=self.score_max),
                candidate.document_chunk_id,
            )
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        ranked = sorted(scored, key=lambda item: (item[0], -item[1]), reverse=True)
        return [
            RerankResult(
                document_chunk_id=document_chunk_id,
                rerank_score=score,
                rerank_order=index,
            )
            for index, (score, document_chunk_id) in enumerate(ranked, start=1)
        ]

    def _load_model(self) -> object:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder
        except Exception as exc:
            raise RerankError() from exc
        self._model = CrossEncoder(self.model_name)
        return self._model


def create_reranker(settings: Settings) -> RerankerClient:
    if settings.rerank_provider == "none":
        return NoopRerankerClient()
    if settings.rerank_provider == "fake":
        return FakeRerankerClient()
    if settings.rerank_provider == "local":
        return LocalRerankerClient(
            model_name=settings.reranker_model,
            score_min=settings.rerank_score_min,
            score_max=settings.rerank_score_max,
        )
    raise RerankError()


def normalize_rerank_score(value: float, *, score_min: float, score_max: float) -> float:
    if not math.isfinite(value):
        return 0.0
    if score_max <= score_min:
        return 0.0
    normalized = (value - score_min) / (score_max - score_min)
    return min(1.0, max(0.0, normalized))


def _fake_rerank_score(query: str, candidate: RerankCandidate) -> float:
    query_terms = _expanded_query_terms(query)
    candidate_text = candidate.text.lower()
    overlap = 0.0
    if query_terms:
        overlap = sum(1 for term in query_terms if term in candidate_text) / len(query_terms)
    digest = hashlib.sha256(
        f"{query}\0{candidate.document_chunk_id}\0{candidate.text[:256]}".encode()
    ).digest()
    tie_breaker = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    retrieval_component = min(1.0, max(0.0, (candidate.retrieval_score + 1.0) / 2.0))
    return round((overlap * 0.65) + (retrieval_component * 0.25) + (tie_breaker * 0.10), 6)


def _expanded_query_terms(query: str) -> set[str]:
    normalized = query.lower()
    terms = {term for term in normalized.split() if term}
    if any(term in normalized for term in ("技術スタック", "技術構成", "システム構成")):
        terms.update(
            {
                "phase1",
                "rag",
                "qdrant",
                "vector",
                "database",
                "postgresql",
                "docker",
                "compose",
                "fastapi",
                "react",
                "backend",
                "frontend",
                "worker",
                "citation",
                "confidence",
                "mcp",
            }
        )
    if "ベクトル" in normalized or "vector database" in normalized:
        terms.update({"qdrant", "vector", "database", "retrieval"})
    if "引用" in normalized:
        terms.update({"citation", "citations"})
    if "信頼度" in normalized or "confidence" in normalized:
        terms.add("confidence")
    return terms


def _to_score_list(value: object, *, expected_count: int) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()  # type: ignore[no-untyped-call]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        raise RerankError()
    scores: list[float] = []
    for item in value:
        try:
            score = float(cast(Any, item))
        except (TypeError, ValueError) as exc:
            raise RerankError() from exc
        if not math.isfinite(score):
            raise RerankError()
        scores.append(score)
    if len(scores) != expected_count:
        raise RerankError()
    return scores
