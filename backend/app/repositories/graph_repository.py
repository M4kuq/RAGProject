from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.job_utils import redact_error_message
from app.db.graph_models import (
    GraphEntity,
    GraphEntityMention,
    GraphIndexRun,
    GraphRelation,
    GraphRetrievalPath,
)
from app.schemas.graph import (
    GraphEntityCreate,
    GraphEntityMentionCreate,
    GraphIndexRunCreate,
    GraphRelationCreate,
    GraphRetrievalPathCreate,
    validate_safe_graph_metadata,
)


class GraphRepository:
    def create_entity(self, db: Session, data: GraphEntityCreate) -> GraphEntity:
        entity = GraphEntity(
            canonical_name=data.canonical_name,
            entity_type=data.entity_type,
            aliases_json=list(data.aliases_json),
            description=data.description,
            metadata_json=validate_safe_graph_metadata(dict(data.metadata_json)),
        )
        db.add(entity)
        db.flush()
        return entity

    def get_entity(self, db: Session, graph_entity_id: int) -> GraphEntity | None:
        return db.get(GraphEntity, graph_entity_id)

    def find_entity_by_canonical_name(
        self,
        db: Session,
        *,
        canonical_name: str,
        entity_type: str,
    ) -> GraphEntity | None:
        return db.scalar(
            select(GraphEntity).where(
                func.lower(GraphEntity.canonical_name) == canonical_name.strip().lower(),
                GraphEntity.entity_type == entity_type.strip(),
            )
        )

    def create_relation(self, db: Session, data: GraphRelationCreate) -> GraphRelation:
        relation = GraphRelation(
            source_entity_id=data.source_entity_id,
            target_entity_id=data.target_entity_id,
            relation_type=data.relation_type,
            relation_label=data.relation_label,
            confidence=data.confidence,
            source_document_chunk_id=data.source_document_chunk_id,
            evidence_text_hash=data.evidence_text_hash,
            metadata_json=validate_safe_graph_metadata(dict(data.metadata_json)),
        )
        db.add(relation)
        db.flush()
        return relation

    def list_relations_for_entity(
        self,
        db: Session,
        *,
        graph_entity_id: int,
        relation_type: str | None = None,
    ) -> list[GraphRelation]:
        conditions: list[Any] = [
            or_(
                GraphRelation.source_entity_id == graph_entity_id,
                GraphRelation.target_entity_id == graph_entity_id,
            )
        ]
        if relation_type is not None:
            conditions.append(GraphRelation.relation_type == relation_type)
        statement = (
            select(GraphRelation)
            .where(*conditions)
            .order_by(GraphRelation.created_at.asc(), GraphRelation.graph_relation_id.asc())
        )
        return list(db.scalars(statement).all())

    def create_entity_mention(
        self,
        db: Session,
        data: GraphEntityMentionCreate,
    ) -> GraphEntityMention:
        mention = GraphEntityMention(
            graph_entity_id=data.graph_entity_id,
            document_chunk_id=data.document_chunk_id,
            document_version_id=data.document_version_id,
            mention_text_hash=data.mention_text_hash,
            mention_offset_start=data.mention_offset_start,
            mention_offset_end=data.mention_offset_end,
            confidence=data.confidence,
            metadata_json=validate_safe_graph_metadata(dict(data.metadata_json)),
        )
        db.add(mention)
        db.flush()
        return mention

    def create_graph_index_run(self, db: Session, data: GraphIndexRunCreate) -> GraphIndexRun:
        run = GraphIndexRun(
            document_version_id=data.document_version_id,
            job_id=data.job_id,
            status="queued",
            extractor_type=data.extractor_type,
            extractor_version=data.extractor_version,
            metadata_json=validate_safe_graph_metadata(dict(data.metadata_json)),
        )
        db.add(run)
        db.flush()
        return run

    def get_graph_index_run(
        self,
        db: Session,
        graph_index_run_id: int,
        *,
        for_update: bool = False,
    ) -> GraphIndexRun | None:
        stmt = select(GraphIndexRun).where(GraphIndexRun.graph_index_run_id == graph_index_run_id)
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def mark_graph_index_run_running(
        self,
        db: Session,
        *,
        run: GraphIndexRun,
        started_at: datetime | None = None,
    ) -> None:
        now = started_at or datetime.now(UTC)
        run.status = "running"
        run.started_at = now
        run.finished_at = None
        run.error_code = None
        run.error_message = None
        run.updated_at = now
        db.flush()

    def mark_graph_index_run_succeeded(
        self,
        db: Session,
        *,
        run: GraphIndexRun,
        entity_count: int,
        relation_count: int,
        mention_count: int,
        finished_at: datetime | None = None,
    ) -> None:
        now = finished_at or datetime.now(UTC)
        run.status = "succeeded"
        run.entity_count = entity_count
        run.relation_count = relation_count
        run.mention_count = mention_count
        run.error_code = None
        run.error_message = None
        run.finished_at = _terminal_time(run, now)
        run.updated_at = now
        db.flush()

    def mark_graph_index_run_failed(
        self,
        db: Session,
        *,
        run: GraphIndexRun,
        error_code: str,
        error_message: str | None,
        finished_at: datetime | None = None,
    ) -> None:
        now = finished_at or datetime.now(UTC)
        run.status = "failed"
        run.error_code = error_code
        run.error_message = redact_error_message(error_message)
        run.finished_at = _terminal_time(run, now)
        run.updated_at = now
        db.flush()

    def create_graph_retrieval_path(
        self,
        db: Session,
        data: GraphRetrievalPathCreate,
    ) -> GraphRetrievalPath:
        path = GraphRetrievalPath(
            retrieval_run_id=data.retrieval_run_id,
            path_json=validate_safe_graph_metadata(dict(data.path_json)),
            score_breakdown_json=validate_safe_graph_metadata(dict(data.score_breakdown_json)),
            source_chunk_ids_json=list(data.source_chunk_ids_json),
        )
        db.add(path)
        db.flush()
        return path

    def list_graph_retrieval_paths_by_retrieval_run(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
    ) -> list[GraphRetrievalPath]:
        statement = (
            select(GraphRetrievalPath)
            .where(GraphRetrievalPath.retrieval_run_id == retrieval_run_id)
            .order_by(GraphRetrievalPath.created_at.asc(), GraphRetrievalPath.graph_retrieval_path_id.asc())
        )
        return list(db.scalars(statement).all())


def _terminal_time(run: GraphIndexRun, finished_at: datetime) -> datetime:
    if run.started_at is not None and finished_at < run.started_at:
        return run.started_at
    return finished_at
