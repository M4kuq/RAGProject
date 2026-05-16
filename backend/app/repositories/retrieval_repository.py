from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
)
from app.rag.retrieval import RetrievalFilters, VectorSearchCandidate


@dataclass(frozen=True)
class CheckedRetrievalCandidate:
    chunk: DocumentChunk
    document_version: DocumentVersion
    logical_document: LogicalDocument
    retrieval_score: float
    rank_order: int


@dataclass(frozen=True)
class RetrievalRunItemInput:
    document_chunk_id: int
    retrieval_score: Decimal
    rerank_score: Decimal
    rank_order: int
    rerank_order: int
    selected_flag: bool
    payload_snapshot: dict[str, object]


class RetrievalRepository:
    def create_standalone_run(
        self,
        db: Session,
        *,
        top_k: int,
        query_hash: str,
        request_id: str | None,
        started_at: datetime,
    ) -> RetrievalRun:
        run = RetrievalRun(
            chat_session_id=None,
            request_message_id=None,
            status="running",
            started_at=started_at,
            top_k=top_k,
            query_hash=query_hash,
            request_id=request_id,
        )
        db.add(run)
        db.flush()
        return run

    def get_run(self, db: Session, *, retrieval_run_id: int) -> RetrievalRun | None:
        return db.get(RetrievalRun, retrieval_run_id)

    def mark_succeeded(
        self,
        db: Session,
        *,
        run: RetrievalRun,
        retrieval_score_summary: dict[str, Any],
        rerank_score_top1: Decimal | None,
        finished_at: datetime,
    ) -> None:
        run.status = "succeeded"
        run.error_code = None
        run.retrieval_score_summary = retrieval_score_summary
        run.rerank_score_top1 = rerank_score_top1
        run.finished_at = finished_at
        db.flush()

    def mark_failed(
        self,
        db: Session,
        *,
        run: RetrievalRun,
        error_code: str,
        finished_at: datetime,
    ) -> None:
        run.status = "failed"
        run.error_code = error_code
        run.finished_at = finished_at
        run.answer_confidence = None
        run.groundedness_score = None
        run.confidence_label = None
        db.flush()

    def final_check_candidates(
        self,
        db: Session,
        *,
        candidates: list[VectorSearchCandidate],
        filters: RetrievalFilters,
    ) -> list[CheckedRetrievalCandidate]:
        ordered_ids: list[int] = []
        candidate_by_chunk_id: dict[int, VectorSearchCandidate] = {}
        for candidate in candidates:
            document_chunk_id = candidate.document_chunk_id
            if document_chunk_id is None or document_chunk_id in candidate_by_chunk_id:
                continue
            ordered_ids.append(document_chunk_id)
            candidate_by_chunk_id[document_chunk_id] = candidate
        if not ordered_ids:
            return []

        statement = (
            select(DocumentChunk, DocumentVersion, LogicalDocument)
            .join(
                DocumentVersion,
                DocumentVersion.document_version_id == DocumentChunk.document_version_id,
            )
            .join(
                LogicalDocument,
                LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
            )
            .where(
                DocumentChunk.document_chunk_id.in_(ordered_ids),
                DocumentChunk.modality == filters.modality,
                DocumentVersion.status == "ready",
                DocumentVersion.is_active.is_(True),
                LogicalDocument.status == "active",
            )
        )
        if filters.logical_document_ids:
            statement = statement.where(
                LogicalDocument.logical_document_id.in_(filters.logical_document_ids)
            )
        rows = db.execute(statement).all()
        row_by_chunk_id = {
            chunk.document_chunk_id: (chunk, version, document) for chunk, version, document in rows
        }

        checked: list[CheckedRetrievalCandidate] = []
        for document_chunk_id in ordered_ids:
            row = row_by_chunk_id.get(document_chunk_id)
            if row is None:
                continue
            chunk, version, document = row
            checked.append(
                CheckedRetrievalCandidate(
                    chunk=chunk,
                    document_version=version,
                    logical_document=document,
                    retrieval_score=candidate_by_chunk_id[document_chunk_id].retrieval_score,
                    rank_order=len(checked) + 1,
                )
            )
        return checked

    def save_items(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        items: list[RetrievalRunItemInput],
    ) -> list[RetrievalRunItem]:
        rows = [
            RetrievalRunItem(
                retrieval_run_id=retrieval_run_id,
                document_chunk_id=item.document_chunk_id,
                retrieval_score=item.retrieval_score,
                rerank_score=item.rerank_score,
                rank_order=item.rank_order,
                rerank_order=item.rerank_order,
                selected_flag=item.selected_flag,
                payload_snapshot=item.payload_snapshot,
            )
            for item in items
        ]
        db.add_all(rows)
        db.flush()
        return rows
