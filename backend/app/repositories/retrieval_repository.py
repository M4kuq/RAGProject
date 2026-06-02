from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Citation,
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
)
from app.rag.retrieval import RetrievalFilters, VectorSearchCandidate
from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RetrievalSource


@dataclass(frozen=True)
class CheckedRetrievalCandidate:
    chunk: DocumentChunk
    document_version: DocumentVersion
    logical_document: LogicalDocument
    retrieval_score: float
    rank_order: int
    payload: dict[str, object]


@dataclass(frozen=True)
class RetrievalRunItemInput:
    document_chunk_id: int
    retrieval_score: Decimal
    rerank_score: Decimal | None
    rank_order: int
    rerank_order: int | None
    selected_flag: bool
    payload_snapshot: dict[str, object]
    retrieval_source: str | None = None
    score_breakdown_json: dict[str, object] | None = None


@dataclass(frozen=True)
class CitationInput:
    retrieval_run_id: int
    document_chunk_id: int
    snippet: str
    page_from: int | None
    page_to: int | None
    display_label: str
    rank_order: int
    source_type: str = "upload"
    source_url: str | None = None


@dataclass(frozen=True)
class CitationRecord:
    citation: Citation
    chunk: DocumentChunk
    document_version: DocumentVersion
    logical_document: LogicalDocument


class RetrievalRepository:
    def create_standalone_run(
        self,
        db: Session,
        *,
        top_k: int,
        query_hash: str,
        request_id: str | None,
        started_at: datetime,
        strategy_type: str = DEFAULT_RETRIEVAL_STRATEGY.value,
        query_plan_json: dict[str, object] | None = None,
        strategy_decision_json: dict[str, object] | None = None,
        latency_breakdown_json: dict[str, object] | None = None,
        retrieval_settings_json: dict[str, object] | None = None,
    ) -> RetrievalRun:
        run = RetrievalRun(
            chat_session_id=None,
            request_message_id=None,
            status="running",
            started_at=started_at,
            top_k=top_k,
            strategy_type=strategy_type,
            query_hash=query_hash,
            request_id=request_id,
            query_plan_json=query_plan_json,
            strategy_decision_json=strategy_decision_json,
            latency_breakdown_json=latency_breakdown_json,
            retrieval_settings_json=retrieval_settings_json,
        )
        db.add(run)
        db.flush()
        return run

    def create_chat_run(
        self,
        db: Session,
        *,
        chat_session_id: int,
        request_message_id: int,
        top_k: int,
        query_hash: str,
        request_id: str | None,
        started_at: datetime,
        strategy_type: str = DEFAULT_RETRIEVAL_STRATEGY.value,
        query_plan_json: dict[str, object] | None = None,
        strategy_decision_json: dict[str, object] | None = None,
        latency_breakdown_json: dict[str, object] | None = None,
        retrieval_settings_json: dict[str, object] | None = None,
    ) -> RetrievalRun:
        run = RetrievalRun(
            chat_session_id=chat_session_id,
            request_message_id=request_message_id,
            status="running",
            started_at=started_at,
            top_k=top_k,
            strategy_type=strategy_type,
            query_hash=query_hash,
            request_id=request_id,
            query_plan_json=query_plan_json,
            strategy_decision_json=strategy_decision_json,
            latency_breakdown_json=latency_breakdown_json,
            retrieval_settings_json=retrieval_settings_json,
        )
        db.add(run)
        db.flush()
        return run

    def get_run(self, db: Session, *, retrieval_run_id: int) -> RetrievalRun | None:
        return db.get(RetrievalRun, retrieval_run_id)

    def get_runs_by_ids(
        self,
        db: Session,
        *,
        retrieval_run_ids: list[int],
    ) -> dict[int, RetrievalRun]:
        run_ids = sorted(set(retrieval_run_ids))
        if not run_ids:
            return {}
        statement = select(RetrievalRun).where(RetrievalRun.retrieval_run_id.in_(run_ids))
        return {run.retrieval_run_id: run for run in db.scalars(statement).all()}

    def list_recent_runs(self, db: Session, *, limit: int) -> list[RetrievalRun]:
        statement = (
            select(RetrievalRun)
            .order_by(RetrievalRun.created_at.desc(), RetrievalRun.retrieval_run_id.desc())
            .limit(limit)
        )
        return list(db.scalars(statement).all())

    def list_items_for_run(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
    ) -> list[RetrievalRunItem]:
        statement = (
            select(RetrievalRunItem)
            .where(RetrievalRunItem.retrieval_run_id == retrieval_run_id)
            .order_by(
                RetrievalRunItem.rank_order.asc(),
                RetrievalRunItem.retrieval_run_item_id.asc(),
            )
        )
        return list(db.scalars(statement).all())

    def get_latest_run_for_request_message(
        self,
        db: Session,
        *,
        chat_session_id: int,
        request_message_id: int,
        for_update: bool = False,
    ) -> RetrievalRun | None:
        statement = (
            select(RetrievalRun)
            .where(
                RetrievalRun.chat_session_id == chat_session_id,
                RetrievalRun.request_message_id == request_message_id,
            )
            .order_by(RetrievalRun.created_at.desc(), RetrievalRun.retrieval_run_id.desc())
        )
        if for_update:
            statement = statement.with_for_update()
        return db.scalar(statement)

    def mark_succeeded(
        self,
        db: Session,
        *,
        run: RetrievalRun,
        retrieval_score_summary: dict[str, Any],
        rerank_score_top1: Decimal | None,
        finished_at: datetime,
        answer_confidence: Decimal | None = None,
        groundedness_score: Decimal | None = None,
        confidence_label: str | None = None,
        latency_breakdown_json: dict[str, object] | None = None,
    ) -> None:
        run.status = "succeeded"
        run.error_code = None
        run.retrieval_score_summary = retrieval_score_summary
        run.rerank_score_top1 = rerank_score_top1
        run.answer_confidence = answer_confidence
        run.groundedness_score = groundedness_score
        run.confidence_label = confidence_label
        if latency_breakdown_json is not None:
            run.latency_breakdown_json = latency_breakdown_json
        run.finished_at = _terminal_time(run, finished_at)
        db.flush()

    def mark_failed(
        self,
        db: Session,
        *,
        run: RetrievalRun,
        error_code: str,
        finished_at: datetime,
        latency_breakdown_json: dict[str, object] | None = None,
    ) -> None:
        run.status = "failed"
        run.error_code = error_code
        run.finished_at = _terminal_time(run, finished_at)
        if latency_breakdown_json is not None:
            run.latency_breakdown_json = latency_breakdown_json
        run.answer_confidence = None
        run.groundedness_score = None
        run.confidence_label = None
        db.flush()

    def update_retrieval_run_trace(
        self,
        db: Session,
        *,
        run: RetrievalRun,
        query_plan_json: dict[str, object] | None = None,
        strategy_decision_json: dict[str, object] | None = None,
        latency_breakdown_json: dict[str, object] | None = None,
        retrieval_settings_json: dict[str, object] | None = None,
        context_budget_json: dict[str, object] | None = None,
        context_compression_json: dict[str, object] | None = None,
        tool_result_compression_json: dict[str, object] | None = None,
    ) -> None:
        if query_plan_json is not None:
            run.query_plan_json = query_plan_json
        if strategy_decision_json is not None:
            run.strategy_decision_json = strategy_decision_json
        if latency_breakdown_json is not None:
            run.latency_breakdown_json = latency_breakdown_json
        if retrieval_settings_json is not None:
            run.retrieval_settings_json = retrieval_settings_json
        if context_budget_json is not None:
            run.context_budget_json = context_budget_json
        if context_compression_json is not None:
            run.context_compression_json = context_compression_json
        if tool_result_compression_json is not None:
            run.tool_result_compression_json = tool_result_compression_json
        db.flush()

    def update_context_selection(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        selected_item_ids: set[int],
    ) -> None:
        items = self.list_items_for_run(db, retrieval_run_id=retrieval_run_id)
        for item in items:
            selected = item.retrieval_run_item_id in selected_item_ids
            item.selected_flag = selected
            if item.score_breakdown_json is not None:
                score_breakdown = dict(item.score_breakdown_json)
                score_breakdown["selected_flag"] = selected
                item.score_breakdown_json = score_breakdown
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
                    payload=candidate_by_chunk_id[document_chunk_id].payload,
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
                retrieval_source=item.retrieval_source or RetrievalSource.DENSE.value,
                score_breakdown_json=item.score_breakdown_json,
            )
            for item in items
        ]
        db.add_all(rows)
        db.flush()
        return rows

    def save_citations(
        self,
        db: Session,
        *,
        citations: list[CitationInput],
    ) -> list[Citation]:
        rows = [
            Citation(
                retrieval_run_id=item.retrieval_run_id,
                document_chunk_id=item.document_chunk_id,
                snippet=item.snippet,
                page_from=item.page_from,
                page_to=item.page_to,
                source_type=item.source_type,
                source_url=item.source_url,
                display_label=item.display_label,
                rank_order=item.rank_order,
            )
            for item in citations
        ]
        db.add_all(rows)
        db.flush()
        return rows

    def list_citations_for_run(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
    ) -> list[CitationRecord]:
        return self.list_citations_for_runs(
            db,
            retrieval_run_ids=[retrieval_run_id],
        ).get(retrieval_run_id, [])

    def list_citations_for_runs(
        self,
        db: Session,
        *,
        retrieval_run_ids: list[int],
    ) -> dict[int, list[CitationRecord]]:
        run_ids = sorted(set(retrieval_run_ids))
        if not run_ids:
            return {}
        records_by_run_id: dict[int, list[CitationRecord]] = {run_id: [] for run_id in run_ids}
        statement = (
            select(Citation, DocumentChunk, DocumentVersion, LogicalDocument)
            .join(
                RetrievalRunItem,
                and_(
                    RetrievalRunItem.retrieval_run_id == Citation.retrieval_run_id,
                    RetrievalRunItem.document_chunk_id == Citation.document_chunk_id,
                ),
            )
            .join(DocumentChunk, DocumentChunk.document_chunk_id == Citation.document_chunk_id)
            .join(
                DocumentVersion,
                DocumentVersion.document_version_id == DocumentChunk.document_version_id,
            )
            .join(
                LogicalDocument,
                LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
            )
            .where(
                Citation.retrieval_run_id.in_(run_ids),
                RetrievalRunItem.selected_flag.is_(True),
            )
            .order_by(
                Citation.retrieval_run_id.asc(),
                Citation.rank_order.asc(),
                Citation.citation_id.asc(),
            )
        )
        for citation, chunk, version, document in db.execute(statement).all():
            records_by_run_id[citation.retrieval_run_id].append(
                CitationRecord(
                    citation=citation,
                    chunk=chunk,
                    document_version=version,
                    logical_document=document,
                )
            )
        return records_by_run_id


def _terminal_time(run: RetrievalRun, finished_at: datetime) -> datetime:
    if run.started_at is not None and _datetime_for_ordering(finished_at) < _datetime_for_ordering(
        run.started_at
    ):
        return run.started_at
    return finished_at


def _datetime_for_ordering(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
