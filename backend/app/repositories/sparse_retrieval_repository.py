from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import desc, func, literal_column, select
from sqlalchemy.orm import Session

from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument
from app.rag.retrieval import RetrievalFilters

SUPPORTED_POSTGRES_FTS_LANGUAGES = {"simple", "english"}


class SparseQuery(Protocol):
    @property
    def terms(self) -> tuple[str, ...]: ...

    @property
    def search_text(self) -> str: ...


@dataclass(frozen=True)
class SparseSearchCandidate:
    document_chunk_id: int
    sparse_score: float
    raw_score: float
    rank_order: int


class SparseRetrievalRepository:
    def search(
        self,
        db: Session,
        *,
        normalized_query: SparseQuery,
        limit: int,
        filters: RetrievalFilters,
        language: str,
    ) -> list[SparseSearchCandidate]:
        if limit < 1 or not normalized_query.terms:
            return []
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            return self._search_postgres_fts(
                db,
                normalized_query=normalized_query,
                limit=limit,
                filters=filters,
                language=language,
            )
        return self._search_in_app_bm25(
            db,
            normalized_query=normalized_query,
            limit=limit,
            filters=filters,
        )

    def _search_postgres_fts(
        self,
        db: Session,
        *,
        normalized_query: SparseQuery,
        limit: int,
        filters: RetrievalFilters,
        language: str,
    ) -> list[SparseSearchCandidate]:
        language_literal = _postgres_language_literal(language)
        vector = func.to_tsvector(language_literal, DocumentChunk.content_text)
        query = func.plainto_tsquery(language_literal, normalized_query.search_text)
        rank = func.ts_rank_cd(vector, query)
        statement = (
            select(DocumentChunk.document_chunk_id, rank.label("raw_score"))
            .where(
                DocumentChunk.modality == filters.modality,
                vector.op("@@")(query),
            )
            .order_by(desc("raw_score"), DocumentChunk.document_chunk_id.asc())
            .limit(limit)
        )
        if filters.logical_document_ids:
            statement = (
                statement.join(
                    DocumentVersion,
                    DocumentVersion.document_version_id == DocumentChunk.document_version_id,
                )
                .join(
                    LogicalDocument,
                    LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
                )
                .where(LogicalDocument.logical_document_id.in_(filters.logical_document_ids))
            )
        raw_candidates = [
            (int(row.document_chunk_id), float(row.raw_score))
            for row in db.execute(statement).all()
        ]
        return _normalize_scores(raw_candidates)

    def _search_in_app_bm25(
        self,
        db: Session,
        *,
        normalized_query: SparseQuery,
        limit: int,
        filters: RetrievalFilters,
    ) -> list[SparseSearchCandidate]:
        statement = select(DocumentChunk.document_chunk_id, DocumentChunk.content_text).where(
            DocumentChunk.modality == filters.modality
        )
        if filters.logical_document_ids:
            statement = (
                statement.join(
                    DocumentVersion,
                    DocumentVersion.document_version_id == DocumentChunk.document_version_id,
                )
                .join(
                    LogicalDocument,
                    LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
                )
                .where(LogicalDocument.logical_document_id.in_(filters.logical_document_ids))
            )
        rows = [
            (int(row.document_chunk_id), str(row.content_text)) for row in db.execute(statement)
        ]
        raw_candidates = _bm25_scores(rows, normalized_query.terms)
        return _normalize_scores(raw_candidates)[:limit]


def _bm25_scores(
    rows: list[tuple[int, str]], query_terms: tuple[str, ...]
) -> list[tuple[int, float]]:
    if not rows:
        return []
    tokenized = [(document_chunk_id, _token_counts(text)) for document_chunk_id, text in rows]
    doc_count = len(tokenized)
    avg_len = sum(sum(counts.values()) for _, counts in tokenized) / max(doc_count, 1)
    avg_len = max(avg_len, 1.0)
    document_frequency: Counter[str] = Counter()
    for _, counts in tokenized:
        for term in query_terms:
            if counts.get(term, 0) > 0:
                document_frequency[term] += 1

    k1 = 1.5
    b = 0.75
    scored: list[tuple[int, float]] = []
    for document_chunk_id, counts in tokenized:
        length = max(sum(counts.values()), 1)
        score = 0.0
        for term in query_terms:
            tf = counts.get(term, 0)
            if tf <= 0:
                continue
            df = document_frequency[term]
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            denominator = tf + k1 * (1 - b + b * length / avg_len)
            score += idf * (tf * (k1 + 1)) / denominator
        if score > 0:
            scored.append((document_chunk_id, score))
    return scored


def _token_counts(text: str) -> Counter[str]:
    from app.rag.sparse import TERM_RE

    tokens = [match.group(0).strip("_") for match in TERM_RE.finditer(text.lower())]
    return Counter(token for token in tokens if token)


def _postgres_language_literal(language: str) -> object:
    normalized = language.lower()
    if normalized not in SUPPORTED_POSTGRES_FTS_LANGUAGES:
        raise ValueError("unsupported sparse retrieval language")
    return literal_column(f"'{normalized}'")


def _normalize_scores(candidates: list[tuple[int, float]]) -> list[SparseSearchCandidate]:
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
