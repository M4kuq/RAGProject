from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.rag.retrieval import RetrievalError, RetrievalFilters, VectorSearchCandidate
from app.repositories.sparse_retrieval_repository import (
    SparseRetrievalRepository,
    SparseSearchCandidate,
)

TERM_RE: Final = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class NormalizedSparseQuery:
    terms: tuple[str, ...]

    @property
    def search_text(self) -> str:
        return " ".join(self.terms)


class SparseRetrievalStrategy:
    def __init__(self, repository: SparseRetrievalRepository | None = None) -> None:
        self.repository = repository or SparseRetrievalRepository()

    def search(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        settings: Settings,
    ) -> list[VectorSearchCandidate]:
        normalized = normalize_sparse_query(
            query,
            max_terms=settings.sparse_max_query_terms,
        )
        if len(normalized.terms) < settings.sparse_min_query_terms:
            return []
        try:
            candidates = self.repository.search(
                db,
                normalized_query=normalized,
                limit=top_k,
                filters=filters,
                language=settings.sparse_language,
            )
        except SQLAlchemyError as exc:
            raise RetrievalError() from exc
        return [
            VectorSearchCandidate(
                document_chunk_id=candidate.document_chunk_id,
                retrieval_score=candidate.sparse_score,
                qdrant_order=candidate.rank_order,
                payload={"retrieval_source": "sparse"},
            )
            for candidate in candidates
        ]


def normalize_sparse_query(query: str, *, max_terms: int) -> NormalizedSparseQuery:
    terms: list[str] = []
    seen: set[str] = set()
    for match in TERM_RE.finditer(query.lower()):
        term = match.group(0).strip("_")
        if not term or term in seen:
            continue
        terms.append(term)
        seen.add(term)
        if len(terms) >= max_terms:
            break
    return NormalizedSparseQuery(terms=tuple(terms))


def normalize_sparse_scores(
    candidates: list[tuple[int, float]],
) -> list[SparseSearchCandidate]:
    finite_candidates = [
        (document_chunk_id, float(score))
        for document_chunk_id, score in candidates
        if math.isfinite(float(score)) and float(score) > 0
    ]
    if not finite_candidates:
        return []
    max_score = max(score for _, score in finite_candidates)
    if max_score <= 0:
        return []
    ranked = sorted(
        (
            SparseSearchCandidate(
                document_chunk_id=document_chunk_id,
                sparse_score=round(score / max_score, 6),
                raw_score=score,
                rank_order=0,
            )
            for document_chunk_id, score in finite_candidates
        ),
        key=lambda candidate: (-candidate.sparse_score, candidate.document_chunk_id),
    )
    return [
        SparseSearchCandidate(
            document_chunk_id=candidate.document_chunk_id,
            sparse_score=candidate.sparse_score,
            raw_score=candidate.raw_score,
            rank_order=index,
        )
        for index, candidate in enumerate(ranked, start=1)
    ]
