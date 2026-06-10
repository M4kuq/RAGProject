from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.db.graph_models import (
    GraphEntity,
    GraphEntityMention,
    GraphIndexRun,
    GraphRelation,
    GraphRetrievalPath,
)
from app.db.models import DocumentChunk, RetrievalRunItem
from app.schemas.graph import (
    GraphEntityCreate,
    GraphEntityMentionCreate,
    GraphIndexRunCreate,
    GraphRelationCreate,
    GraphRetrievalPathCreate,
    validate_safe_graph_label,
    validate_safe_graph_metadata,
)

_TERMINAL_GRAPH_INDEX_STATUSES = frozenset({"succeeded", "failed", "cancelled", "skipped"})


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

    def list_entities_by_keys(
        self,
        db: Session,
        *,
        keys: set[tuple[str, str]],
    ) -> dict[tuple[str, str], GraphEntity]:
        normalized_keys = {
            (canonical_name.strip().lower(), entity_type.strip())
            for canonical_name, entity_type in keys
            if canonical_name.strip() and entity_type.strip()
        }
        if not normalized_keys:
            return {}
        names = {canonical_name for canonical_name, _ in normalized_keys}
        entity_types = {entity_type for _, entity_type in normalized_keys}
        rows = db.scalars(
            select(GraphEntity).where(
                func.lower(GraphEntity.canonical_name).in_(names),
                GraphEntity.entity_type.in_(entity_types),
            )
        ).all()
        return {
            (row.canonical_name.strip().lower(), row.entity_type.strip()): row
            for row in rows
            if (row.canonical_name.strip().lower(), row.entity_type.strip()) in normalized_keys
        }

    def merge_entity_aliases(
        self,
        db: Session,
        *,
        entity: GraphEntity,
        aliases: tuple[str, ...],
    ) -> None:
        if not aliases:
            return
        existing_aliases = [
            validate_safe_graph_label(str(alias), field_name="aliases_json", max_length=120)
            for alias in (entity.aliases_json or [])
        ]
        seen = {entity.canonical_name.strip().lower()}
        merged: list[str] = []
        for alias in (*existing_aliases, *aliases):
            safe_alias = validate_safe_graph_label(
                str(alias),
                field_name="aliases_json",
                max_length=120,
            )
            dedupe_key = safe_alias.lower()
            if dedupe_key in seen:
                continue
            merged.append(safe_alias)
            seen.add(dedupe_key)
            if len(merged) >= 32:
                break
        if merged != existing_aliases:
            entity.aliases_json = merged
            entity.updated_at = datetime.now(UTC)
            db.flush()

    def create_relation(self, db: Session, data: GraphRelationCreate) -> GraphRelation:
        return self.create_relations(db, [data])[0]

    def create_relations(
        self,
        db: Session,
        items: Sequence[GraphRelationCreate],
    ) -> list[GraphRelation]:
        relations = [
            GraphRelation(
                source_entity_id=data.source_entity_id,
                target_entity_id=data.target_entity_id,
                relation_type=data.relation_type,
                relation_label=data.relation_label,
                confidence=data.confidence,
                source_document_chunk_id=data.source_document_chunk_id,
                evidence_text_hash=data.evidence_text_hash,
                metadata_json=validate_safe_graph_metadata(dict(data.metadata_json)),
            )
            for data in items
        ]
        if not relations:
            return []
        db.add_all(relations)
        db.flush()
        return relations

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
            ),
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
        return self.create_entity_mentions_for_version(
            db,
            document_version_id=data.document_version_id,
            items=[data],
        )[0]

    def create_entity_mentions_for_version(
        self,
        db: Session,
        *,
        document_version_id: int,
        items: Sequence[GraphEntityMentionCreate],
    ) -> list[GraphEntityMention]:
        mention_items = list(items)
        if not mention_items:
            return []

        chunk_ids = {item.document_chunk_id for item in mention_items}
        version_ids = {item.document_version_id for item in mention_items}
        if version_ids != {document_version_id}:
            raise ValueError("all entity mentions must belong to document_version_id")
        _assert_chunks_belong_to_version(
            db,
            document_chunk_ids=chunk_ids,
            document_version_id=document_version_id,
        )
        mentions = [
            GraphEntityMention(
                graph_entity_id=data.graph_entity_id,
                document_chunk_id=data.document_chunk_id,
                document_version_id=data.document_version_id,
                mention_text_hash=data.mention_text_hash,
                mention_offset_start=data.mention_offset_start,
                mention_offset_end=data.mention_offset_end,
                confidence=data.confidence,
                metadata_json=validate_safe_graph_metadata(dict(data.metadata_json)),
            )
            for data in mention_items
        ]
        db.add_all(mentions)
        db.flush()
        return mentions

    def list_chunks_for_graph_index(
        self,
        db: Session,
        *,
        document_version_id: int,
    ) -> list[DocumentChunk]:
        rows = db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == document_version_id)
            .order_by(DocumentChunk.chunk_index.asc(), DocumentChunk.document_chunk_id.asc())
        ).all()
        return list(rows)

    def delete_index_artifacts_for_document_version(
        self,
        db: Session,
        *,
        document_version_id: int,
    ) -> tuple[int, int]:
        chunk_ids = list(
            db.scalars(
                select(DocumentChunk.document_chunk_id).where(
                    DocumentChunk.document_version_id == document_version_id
                )
            ).all()
        )
        relation_count = 0
        if chunk_ids:
            relation_result = db.execute(
                delete(GraphRelation).where(GraphRelation.source_document_chunk_id.in_(chunk_ids))
            )
            relation_count = int(getattr(relation_result, "rowcount", 0) or 0)
        mention_result = db.execute(
            delete(GraphEntityMention).where(
                GraphEntityMention.document_version_id == document_version_id
            )
        )
        mention_count = int(getattr(mention_result, "rowcount", 0) or 0)
        db.flush()
        return relation_count, mention_count

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
        stmt = select(GraphIndexRun).where(
            GraphIndexRun.graph_index_run_id == graph_index_run_id,
        )
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
        _assert_graph_index_run_transition(
            run,
            allowed_statuses={"queued"},
            target_status="running",
        )
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
        _assert_graph_index_run_transition(
            run,
            allowed_statuses={"running"},
            target_status="succeeded",
        )
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
        _assert_graph_index_run_transition(
            run,
            allowed_statuses={"queued", "running"},
            target_status="failed",
        )
        safe_error_code = _safe_graph_failure_code(error_code)
        safe_error_message = _safe_graph_failure_message(error_message)
        now = finished_at or datetime.now(UTC)
        run.status = "failed"
        run.error_code = safe_error_code
        run.error_message = safe_error_message
        run.finished_at = _terminal_time(run, now)
        run.updated_at = now
        db.flush()

    def create_graph_retrieval_path(
        self,
        db: Session,
        data: GraphRetrievalPathCreate,
    ) -> GraphRetrievalPath:
        _assert_source_chunks_belong_to_retrieval_run(
            db,
            retrieval_run_id=data.retrieval_run_id,
            source_chunk_ids=list(data.source_chunk_ids_json),
        )
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
            .order_by(
                GraphRetrievalPath.created_at.asc(),
                GraphRetrievalPath.graph_retrieval_path_id.asc(),
            )
        )
        return list(db.scalars(statement).all())


def _assert_chunk_belongs_to_version(
    db: Session,
    *,
    document_chunk_id: int,
    document_version_id: int,
) -> None:
    _assert_chunks_belong_to_version(
        db,
        document_chunk_ids={document_chunk_id},
        document_version_id=document_version_id,
    )


def _assert_chunks_belong_to_version(
    db: Session,
    *,
    document_chunk_ids: set[int],
    document_version_id: int,
) -> None:
    if not document_chunk_ids:
        return
    actual_chunk_ids = set(
        db.scalars(
            select(DocumentChunk.document_chunk_id).where(
                DocumentChunk.document_version_id == document_version_id,
                DocumentChunk.document_chunk_id.in_(document_chunk_ids),
            )
        ).all()
    )
    if actual_chunk_ids != document_chunk_ids:
        raise ValueError("document_chunk_id must belong to document_version_id")


def _assert_source_chunks_belong_to_retrieval_run(
    db: Session,
    *,
    retrieval_run_id: int,
    source_chunk_ids: list[int],
) -> None:
    if not source_chunk_ids:
        return
    expected_chunk_ids = set(source_chunk_ids)
    actual_chunk_ids = set(
        db.scalars(
            select(RetrievalRunItem.document_chunk_id).where(
                RetrievalRunItem.retrieval_run_id == retrieval_run_id,
                RetrievalRunItem.document_chunk_id.in_(expected_chunk_ids),
            )
        ).all()
    )
    if actual_chunk_ids != expected_chunk_ids:
        raise ValueError("source_chunk_ids_json must reference chunks selected by retrieval_run_id")


def _assert_graph_index_run_transition(
    run: GraphIndexRun,
    *,
    allowed_statuses: set[str],
    target_status: str,
) -> None:
    if run.status in _TERMINAL_GRAPH_INDEX_STATUSES or run.status not in allowed_statuses:
        raise ValueError(f"cannot transition graph_index_run from {run.status} to {target_status}")


def _safe_graph_failure_code(error_code: str) -> str:
    return validate_safe_graph_label(error_code, field_name="error_code", max_length=80)


def _safe_graph_failure_message(error_message: str | None) -> str:
    message = "Job failed with a redacted error." if error_message else "Job failed."
    return validate_safe_graph_label(message, field_name="error_message", max_length=240)


def _terminal_time(run: GraphIndexRun, finished_at: datetime) -> datetime:
    if run.started_at is not None and finished_at < run.started_at:
        return run.started_at
    return finished_at
