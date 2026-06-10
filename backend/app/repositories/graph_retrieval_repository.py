from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import String, func, or_, select
from sqlalchemy.orm import Session

from app.db.graph_models import (
    GraphEntity,
    GraphEntityMention,
    GraphRelation,
    GraphRetrievalPath,
)
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument
from app.rag.retrieval import RetrievalFilters
from app.repositories.graph_repository import GraphRepository
from app.schemas.graph import GraphRetrievalPathCreate, validate_safe_graph_label


@dataclass(frozen=True)
class GraphEntityLookupResult:
    entity: GraphEntity
    match_score: float
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class GraphRelationRow:
    relation: GraphRelation
    source_entity: GraphEntity
    target_entity: GraphEntity


@dataclass(frozen=True)
class GraphChunkRow:
    chunk: DocumentChunk
    document_version: DocumentVersion
    logical_document: LogicalDocument
    graph_entity_id: int | None = None


class GraphRetrievalRepository:
    def __init__(self, *, graph_repository: GraphRepository | None = None) -> None:
        self.graph_repository = graph_repository or GraphRepository()

    def lookup_entities(
        self,
        db: Session,
        *,
        query_terms: tuple[str, ...],
        limit: int,
        min_match_score: float,
    ) -> list[GraphEntityLookupResult]:
        safe_terms = _safe_terms(query_terms)
        if not safe_terms or limit < 1:
            return []
        conditions = []
        for term in safe_terms:
            like_term = f"%{term.lower()}%"
            conditions.append(func.lower(GraphEntity.canonical_name).like(like_term))
            conditions.append(
                func.lower(func.coalesce(GraphEntity.entity_type, "")).like(like_term)
            )
            conditions.append(
                func.lower(func.coalesce(GraphEntity.aliases_json.cast(String), "")).like(
                    like_term
                )
            )
        rows = db.scalars(
            select(GraphEntity)
            .where(or_(*conditions))
            .order_by(GraphEntity.updated_at.desc(), GraphEntity.graph_entity_id.asc())
            .limit(max(limit * 8, limit))
        ).all()
        results: list[GraphEntityLookupResult] = []
        for entity in rows:
            matched_terms = _matched_terms(entity, safe_terms)
            if not matched_terms:
                continue
            score = _entity_match_score(
                entity,
                safe_terms=safe_terms,
                matched_terms=matched_terms,
            )
            if score < min_match_score:
                continue
            results.append(
                GraphEntityLookupResult(
                    entity=entity,
                    match_score=round(score, 6),
                    matched_terms=matched_terms,
                )
            )
        results.sort(
            key=lambda item: (item.match_score, -item.entity.graph_entity_id),
            reverse=True,
        )
        return results[:limit]

    def has_active_graph_sources(
        self,
        db: Session,
        *,
        filters: RetrievalFilters,
    ) -> bool:
        statement = (
            select(GraphEntityMention.graph_entity_mention_id)
            .join(
                DocumentChunk,
                DocumentChunk.document_chunk_id == GraphEntityMention.document_chunk_id,
            )
            .join(
                DocumentVersion,
                DocumentVersion.document_version_id == DocumentChunk.document_version_id,
            )
            .join(
                LogicalDocument,
                LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
            )
            .where(
                DocumentChunk.modality == filters.modality,
                DocumentVersion.status == "ready",
                DocumentVersion.is_active.is_(True),
                LogicalDocument.status == "active",
            )
            .limit(1)
        )
        if filters.logical_document_ids:
            statement = statement.where(
                LogicalDocument.logical_document_id.in_(filters.logical_document_ids)
            )
        return db.scalar(statement) is not None

    def list_relations_for_entity_ids(
        self,
        db: Session,
        *,
        entity_ids: set[int],
        max_relations_per_entity: int,
        filters: RetrievalFilters | None = None,
    ) -> list[GraphRelationRow]:
        safe_ids = {entity_id for entity_id in entity_ids if entity_id > 0}
        if not safe_ids or max_relations_per_entity < 1:
            return []
        limit = min(500, max_relations_per_entity * len(safe_ids) * 2)
        relation_rows = db.scalars(
            select(GraphRelation)
            .where(
                or_(
                    GraphRelation.source_entity_id.in_(safe_ids),
                    GraphRelation.target_entity_id.in_(safe_ids),
                )
            )
            .order_by(
                func.coalesce(GraphRelation.confidence, 0).desc(),
                GraphRelation.graph_relation_id.asc(),
            )
            .limit(limit)
        ).all()
        if filters is not None:
            relation_rows = self._relations_matching_filters(
                db,
                relation_rows,
                filters=filters,
            )
        related_entity_ids = {
            entity_id
            for relation in relation_rows
            for entity_id in (relation.source_entity_id, relation.target_entity_id)
        }
        entities = self.get_entities_by_ids(db, entity_ids=related_entity_ids)
        per_entity_counts: dict[int, int] = defaultdict(int)
        bounded: list[GraphRelationRow] = []
        for relation in relation_rows:
            touched_ids = (relation.source_entity_id, relation.target_entity_id)
            if any(
                per_entity_counts[entity_id] >= max_relations_per_entity
                for entity_id in touched_ids
            ):
                continue
            source_entity = entities.get(relation.source_entity_id)
            target_entity = entities.get(relation.target_entity_id)
            if source_entity is None or target_entity is None:
                continue
            for entity_id in touched_ids:
                per_entity_counts[entity_id] += 1
            bounded.append(
                GraphRelationRow(
                    relation=relation,
                    source_entity=source_entity,
                    target_entity=target_entity,
                )
            )
        return bounded

    def _relations_matching_filters(
        self,
        db: Session,
        relation_rows: list[GraphRelation],
        *,
        filters: RetrievalFilters,
    ) -> list[GraphRelation]:
        chunk_ids = {
            relation.source_document_chunk_id
            for relation in relation_rows
            if relation.source_document_chunk_id is not None
        }
        if not chunk_ids:
            return relation_rows
        valid_chunk_ids = set(
            self.list_chunks_by_ids(
                db,
                document_chunk_ids=chunk_ids,
                filters=filters,
            ).keys()
        )
        return [
            relation
            for relation in relation_rows
            if relation.source_document_chunk_id is None
            or relation.source_document_chunk_id in valid_chunk_ids
        ]

    def get_entities_by_ids(
        self, db: Session, *, entity_ids: set[int]
    ) -> dict[int, GraphEntity]:
        safe_ids = {entity_id for entity_id in entity_ids if entity_id > 0}
        if not safe_ids:
            return {}
        rows = db.scalars(
            select(GraphEntity).where(GraphEntity.graph_entity_id.in_(safe_ids))
        ).all()
        return {row.graph_entity_id: row for row in rows}

    def list_mentions_for_entity_ids(
        self,
        db: Session,
        *,
        entity_ids: set[int],
        filters: RetrievalFilters,
        max_source_chunks: int,
    ) -> list[GraphChunkRow]:
        safe_ids = {entity_id for entity_id in entity_ids if entity_id > 0}
        if not safe_ids or max_source_chunks < 1:
            return []
        statement = (
            select(
                DocumentChunk,
                DocumentVersion,
                LogicalDocument,
                GraphEntityMention.graph_entity_id,
            )
            .join(
                GraphEntityMention,
                GraphEntityMention.document_chunk_id == DocumentChunk.document_chunk_id,
            )
            .join(
                DocumentVersion,
                DocumentVersion.document_version_id == DocumentChunk.document_version_id,
            )
            .join(
                LogicalDocument,
                LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
            )
            .where(
                GraphEntityMention.graph_entity_id.in_(safe_ids),
                DocumentChunk.modality == filters.modality,
                DocumentVersion.status == "ready",
                DocumentVersion.is_active.is_(True),
                LogicalDocument.status == "active",
            )
            .order_by(
                GraphEntityMention.graph_entity_id.asc(),
                DocumentChunk.document_chunk_id.asc(),
            )
            .limit(max_source_chunks)
        )
        if filters.logical_document_ids:
            statement = statement.where(
                LogicalDocument.logical_document_id.in_(filters.logical_document_ids)
            )
        rows = db.execute(statement).all()
        seen: set[tuple[int, int]] = set()
        results: list[GraphChunkRow] = []
        for chunk, version, document, graph_entity_id in rows:
            dedupe_key = (int(graph_entity_id), chunk.document_chunk_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            results.append(
                GraphChunkRow(
                    chunk=chunk,
                    document_version=version,
                    logical_document=document,
                    graph_entity_id=int(graph_entity_id),
                )
            )
        return results

    def list_chunks_by_ids(
        self,
        db: Session,
        *,
        document_chunk_ids: set[int],
        filters: RetrievalFilters,
    ) -> dict[int, GraphChunkRow]:
        safe_ids = {chunk_id for chunk_id in document_chunk_ids if chunk_id > 0}
        if not safe_ids:
            return {}
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
                DocumentChunk.document_chunk_id.in_(safe_ids),
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
        return {
            chunk.document_chunk_id: GraphChunkRow(
                chunk=chunk,
                document_version=version,
                logical_document=document,
            )
            for chunk, version, document in rows
        }

    def save_graph_retrieval_paths(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        paths: list[GraphRetrievalPathCreate],
    ) -> list[GraphRetrievalPath]:
        saved: list[GraphRetrievalPath] = []
        for path in paths:
            if path.retrieval_run_id != retrieval_run_id:
                raise ValueError("graph path retrieval_run_id mismatch")
            saved.append(self.graph_repository.create_graph_retrieval_path(db, path))
        return saved

    def list_graph_retrieval_paths(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
    ) -> list[GraphRetrievalPath]:
        return self.graph_repository.list_graph_retrieval_paths_by_retrieval_run(
            db,
            retrieval_run_id=retrieval_run_id,
        )


def _safe_terms(query_terms: tuple[str, ...]) -> tuple[str, ...]:
    safe: list[str] = []
    seen: set[str] = set()
    for term in query_terms:
        normalized = " ".join(str(term).split()).strip().lower()
        if len(normalized) < 2 or len(normalized) > 80 or normalized in seen:
            continue
        try:
            validate_safe_graph_label(
                normalized,
                field_name="query_term",
                max_length=80,
            )
        except ValueError:
            continue
        safe.append(normalized)
        seen.add(normalized)
        if len(safe) >= 32:
            break
    return tuple(safe)


def _matched_terms(
    entity: GraphEntity,
    query_terms: tuple[str, ...],
) -> tuple[str, ...]:
    haystack_parts = [entity.canonical_name, entity.entity_type]
    haystack_parts.extend(str(alias) for alias in (entity.aliases_json or []))
    haystack = " ".join(haystack_parts).lower()
    return tuple(term for term in query_terms if term in haystack)


def _entity_match_score(
    entity: GraphEntity,
    *,
    safe_terms: tuple[str, ...],
    matched_terms: tuple[str, ...],
) -> float:
    query_text = " ".join(safe_terms)
    if _exact_entity_phrase_match(entity, query_text):
        return 1.0
    name_terms = _entity_name_terms(entity)
    type_terms = _label_terms(entity.entity_type or "")
    matched_name_terms = {term for term in matched_terms if term in name_terms}
    matched_type_terms = {term for term in matched_terms if term in type_terms}
    if matched_name_terms:
        return min(1.0, len(matched_name_terms) / max(1, len(name_terms)))
    if matched_type_terms:
        return min(0.4, 0.2 * len(matched_type_terms))
    return 0.0


def _exact_entity_phrase_match(entity: GraphEntity, query_text: str) -> bool:
    for label in (
        entity.canonical_name,
        *(str(alias) for alias in (entity.aliases_json or [])),
    ):
        normalized = " ".join(label.split()).strip().lower()
        if normalized and normalized in query_text:
            return True
    return False


def _entity_name_terms(entity: GraphEntity) -> set[str]:
    terms = set(_label_terms(entity.canonical_name))
    for alias in entity.aliases_json or []:
        terms.update(_label_terms(str(alias)))
    return terms


def _label_terms(value: str) -> tuple[str, ...]:
    return tuple(
        term
        for term in (
            " ".join(value.replace("_", " ").replace("-", " ").split()).lower().split()
        )
        if len(term) >= 2
    )
