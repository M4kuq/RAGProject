from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import ResourceNotFound
from app.db.graph_models import GraphEntity, GraphIndexRun
from app.db.models import DocumentVersion
from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE
from app.graph.extraction import (
    RULE_BASED_GRAPH_EXTRACTOR_TYPE,
    RULE_BASED_GRAPH_EXTRACTOR_VERSION,
    EntityExtractionService,
    EntityMentionCandidate,
    GraphChunkRef,
    GraphExtractionResult,
    RelationCandidate,
    RelationExtractionService,
)
from app.repositories.graph_repository import GraphRepository
from app.schemas.graph import (
    GraphEntityCreate,
    GraphEntityMentionCreate,
    GraphIndexJobPayload,
    GraphIndexRunCreate,
    GraphIndexSummary,
    GraphRelationCreate,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphIndexBuildSnapshot:
    graph_index_run_id: int
    document_version_id: int
    logical_document_id: int
    job_id: int | None
    extractor_type: str
    extractor_version: str | None
    chunks: tuple[GraphChunkRef, ...]


class GraphIndexService:
    """Build document-version graph indexes without persisting raw evidence text."""

    def __init__(
        self,
        repository: GraphRepository | None = None,
        *,
        entity_extractor: EntityExtractionService | None = None,
        relation_extractor: RelationExtractionService | None = None,
        extractor_type: str = RULE_BASED_GRAPH_EXTRACTOR_TYPE,
        extractor_version: str | None = RULE_BASED_GRAPH_EXTRACTOR_VERSION,
    ) -> None:
        self.repository = repository or GraphRepository()
        self.entity_extractor = entity_extractor or EntityExtractionService()
        self.relation_extractor = relation_extractor or RelationExtractionService()
        self.extractor_type = extractor_type
        self.extractor_version = extractor_version

    def create_index_run_for_document_version(
        self,
        db: Session,
        *,
        document_version_id: int,
        job_id: int | None = None,
        extractor_type: str | None = None,
        extractor_version: str | None = None,
        metadata_json: dict[str, object] | None = None,
    ) -> GraphIndexRun:
        version = db.get(DocumentVersion, document_version_id)
        if version is None:
            raise ResourceNotFound()
        if version.status != "ready":
            raise ValueError("document_version_id must reference a ready document version")
        return self.repository.create_graph_index_run(
            db,
            GraphIndexRunCreate(
                document_version_id=document_version_id,
                job_id=job_id,
                extractor_type=extractor_type or self.extractor_type,
                extractor_version=extractor_version or self.extractor_version,
                metadata_json=metadata_json or {},
            ),
        )

    def build_graph_index_job_payload(
        self,
        *,
        document_version_id: int,
        graph_index_run_id: int | None = None,
        extractor_type: str | None = None,
        extractor_version: str | None = None,
        reindex_policy: str = "replace_existing",
    ) -> dict[str, object]:
        return GraphIndexJobPayload(
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            document_version_id=document_version_id,
            graph_index_run_id=graph_index_run_id,
            extractor_type=extractor_type,
            extractor_version=extractor_version,
            reindex_policy=reindex_policy,
        ).model_dump(exclude_none=True)

    def prepare_index_build(
        self,
        db: Session,
        *,
        document_version_id: int,
        graph_index_run_id: int | None = None,
        job_id: int | None = None,
        extractor_type: str | None = None,
        extractor_version: str | None = None,
    ) -> GraphIndexBuildSnapshot:
        version = db.get(DocumentVersion, document_version_id)
        if version is None:
            raise ResourceNotFound()
        if version.status != "ready":
            raise ValueError("document_version_id must reference a ready document version")

        run = (
            self.repository.get_graph_index_run(db, graph_index_run_id, for_update=True)
            if graph_index_run_id is not None
            else None
        )
        if graph_index_run_id is not None and run is None:
            raise ResourceNotFound()
        if run is not None and run.document_version_id != document_version_id:
            raise ValueError("graph_index_run_id must belong to document_version_id")
        if run is None:
            run = self.repository.create_graph_index_run(
                db,
                GraphIndexRunCreate(
                    document_version_id=document_version_id,
                    job_id=job_id,
                    extractor_type=extractor_type or self.extractor_type,
                    extractor_version=extractor_version or self.extractor_version,
                ),
            )
        if run.status == "succeeded":
            raise ValueError("graph_index_run has already succeeded")
        if run.status == "queued":
            self.repository.mark_graph_index_run_running(db, run=run)
        elif run.status != "running":
            raise ValueError("graph_index_run must be queued or running")

        if run.job_id is None and job_id is not None:
            run.job_id = job_id
        if run.extractor_type == "none":
            run.extractor_type = extractor_type or self.extractor_type
        if run.extractor_version is None:
            run.extractor_version = extractor_version or self.extractor_version

        chunks = tuple(
            GraphChunkRef(
                document_chunk_id=chunk.document_chunk_id,
                document_version_id=chunk.document_version_id,
                chunk_index=chunk.chunk_index,
                chunk_hash=chunk.chunk_hash,
                content_text=chunk.content_text,
            )
            for chunk in self.repository.list_chunks_for_graph_index(
                db,
                document_version_id=document_version_id,
            )
        )
        return GraphIndexBuildSnapshot(
            graph_index_run_id=run.graph_index_run_id,
            document_version_id=document_version_id,
            logical_document_id=version.logical_document_id,
            job_id=run.job_id,
            extractor_type=run.extractor_type,
            extractor_version=run.extractor_version,
            chunks=chunks,
        )

    def extract_from_snapshot(self, snapshot: GraphIndexBuildSnapshot) -> GraphExtractionResult:
        mentions = self.entity_extractor.extract(snapshot.chunks)
        relations = self.relation_extractor.extract(snapshot.chunks, mentions)
        return GraphExtractionResult(
            entity_mentions=_dedupe_mentions(mentions),
            relations=_dedupe_relations(relations),
        )

    def persist_extraction_result(
        self,
        db: Session,
        *,
        snapshot: GraphIndexBuildSnapshot,
        result: GraphExtractionResult,
    ) -> GraphIndexRun:
        run = self.repository.get_graph_index_run(
            db,
            snapshot.graph_index_run_id,
            for_update=True,
        )
        if run is None:
            raise ResourceNotFound()
        if run.status != "running":
            raise ValueError("graph_index_run must be running")

        version = db.get(DocumentVersion, snapshot.document_version_id)
        if version is None:
            raise ResourceNotFound()
        if version.status != "ready":
            raise ValueError("document_version_id must reference a ready document version")

        self.repository.acquire_graph_index_document_version_lock(
            db,
            document_version_id=snapshot.document_version_id,
        )
        self.repository.delete_index_artifacts_for_document_version(
            db,
            document_version_id=snapshot.document_version_id,
        )
        snapshot_chunk_ids = frozenset(chunk.document_chunk_id for chunk in snapshot.chunks)
        entity_map = self._upsert_entities(db, result.entity_mentions)
        mention_count = self._persist_mentions(
            db,
            result.entity_mentions,
            entity_map,
            document_version_id=snapshot.document_version_id,
            allowed_chunk_ids=snapshot_chunk_ids,
        )
        relation_count = self._persist_relations(
            db,
            result.relations,
            entity_map,
            allowed_chunk_ids=snapshot_chunk_ids,
        )
        entity_count = len(
            {
                entity_map[mention.entity_key].graph_entity_id
                for mention in result.entity_mentions
                if mention.entity_key in entity_map
            }
        )
        summary = GraphIndexSummary(
            entity_count=entity_count,
            relation_count=relation_count,
            mention_count=mention_count,
        )
        self.repository.mark_graph_index_run_succeeded(
            db,
            run=run,
            entity_count=summary.entity_count,
            relation_count=summary.relation_count,
            mention_count=summary.mention_count,
        )
        return run

    def _upsert_entities(
        self,
        db: Session,
        mentions: tuple[EntityMentionCandidate, ...],
    ) -> dict[tuple[str, str], GraphEntity]:
        aliases_by_key: dict[tuple[str, str], set[str]] = {}
        names_by_key: dict[tuple[str, str], tuple[str, str]] = {}
        for mention in mentions:
            aliases_by_key.setdefault(mention.entity_key, set()).update(mention.aliases)
            names_by_key.setdefault(
                mention.entity_key,
                (mention.canonical_name, mention.entity_type),
            )
        self.repository.acquire_graph_entity_key_locks(db, keys=set(names_by_key))
        entities = self.repository.list_entities_by_keys(db, keys=set(names_by_key))
        for key, (canonical_name, entity_type) in names_by_key.items():
            entity = entities.get(key)
            aliases = tuple(sorted(aliases_by_key.get(key, set())))
            if entity is None:
                entity = self.repository.create_entity(
                    db,
                    GraphEntityCreate(
                        canonical_name=canonical_name,
                        entity_type=entity_type,
                        aliases_json=list(aliases),
                        metadata_json={"extractor_ref_count": 1},
                    ),
                )
                entities[key] = entity
            else:
                self.repository.merge_entity_aliases(db, entity=entity, aliases=aliases)
        return entities

    def _persist_mentions(
        self,
        db: Session,
        mentions: tuple[EntityMentionCandidate, ...],
        entity_map: dict[tuple[str, str], GraphEntity],
        *,
        document_version_id: int,
        allowed_chunk_ids: frozenset[int],
    ) -> int:
        items: list[GraphEntityMentionCreate] = []
        for mention in mentions:
            if mention.document_chunk_id not in allowed_chunk_ids:
                raise ValueError("entity mention chunk must belong to graph index snapshot")
            entity = entity_map.get(mention.entity_key)
            if entity is None:
                logger.warning(
                    "graph mention skipped because entity is missing",
                    extra={
                        "document_version_id": mention.document_version_id,
                        "document_chunk_id": mention.document_chunk_id,
                    },
                )
                continue
            items.append(
                GraphEntityMentionCreate(
                    graph_entity_id=entity.graph_entity_id,
                    document_chunk_id=mention.document_chunk_id,
                    document_version_id=mention.document_version_id,
                    mention_text_hash=mention.mention_text_hash,
                    mention_offset_start=mention.mention_offset_start,
                    mention_offset_end=mention.mention_offset_end,
                    confidence=mention.confidence,
                    metadata_json=mention.metadata_json,
                )
            )
        self.repository.create_entity_mentions_for_version(
            db,
            document_version_id=document_version_id,
            items=items,
        )
        return len(items)

    def _persist_relations(
        self,
        db: Session,
        relations: tuple[RelationCandidate, ...],
        entity_map: dict[tuple[str, str], GraphEntity],
        *,
        allowed_chunk_ids: frozenset[int],
    ) -> int:
        items: list[GraphRelationCreate] = []
        for relation in relations:
            if relation.source_document_chunk_id not in allowed_chunk_ids:
                raise ValueError("relation source chunk must belong to graph index snapshot")
            source = entity_map.get(relation.source_key)
            target = entity_map.get(relation.target_key)
            if source is None or target is None:
                logger.warning(
                    "graph relation skipped because endpoint is missing",
                    extra={"document_chunk_id": relation.source_document_chunk_id},
                )
                continue
            items.append(
                GraphRelationCreate(
                    source_entity_id=source.graph_entity_id,
                    target_entity_id=target.graph_entity_id,
                    relation_type=relation.relation_type,
                    relation_label=relation.relation_label,
                    confidence=relation.confidence,
                    source_document_chunk_id=relation.source_document_chunk_id,
                    evidence_text_hash=relation.evidence_text_hash,
                    metadata_json=relation.metadata_json,
                )
            )
        self.repository.create_relations(db, items)
        return len(items)

    def mark_index_run_running(self, db: Session, *, graph_index_run_id: int) -> GraphIndexRun:
        run = self.repository.get_graph_index_run(db, graph_index_run_id, for_update=True)
        if run is None:
            raise ResourceNotFound()
        self.repository.mark_graph_index_run_running(db, run=run)
        return run

    def record_index_summary(
        self,
        db: Session,
        *,
        graph_index_run_id: int,
        summary: GraphIndexSummary,
    ) -> GraphIndexRun:
        run = self.repository.get_graph_index_run(db, graph_index_run_id, for_update=True)
        if run is None:
            raise ResourceNotFound()
        self.repository.mark_graph_index_run_succeeded(
            db,
            run=run,
            entity_count=summary.entity_count,
            relation_count=summary.relation_count,
            mention_count=summary.mention_count,
        )
        return run

    def mark_index_run_failed(
        self,
        db: Session,
        *,
        graph_index_run_id: int,
        error_code: str,
        error_message: str | None = None,
    ) -> GraphIndexRun:
        run = self.repository.get_graph_index_run(db, graph_index_run_id, for_update=True)
        if run is None:
            raise ResourceNotFound()
        self.repository.mark_graph_index_run_failed(
            db,
            run=run,
            error_code=error_code,
            error_message=error_message,
        )
        return run


def _dedupe_mentions(
    mentions: tuple[EntityMentionCandidate, ...],
) -> tuple[EntityMentionCandidate, ...]:
    deduped: list[EntityMentionCandidate] = []
    seen: set[tuple[tuple[str, str], int, str, int, int]] = set()
    for mention in mentions:
        key = (
            mention.entity_key,
            mention.document_chunk_id,
            mention.mention_text_hash,
            mention.mention_offset_start,
            mention.mention_offset_end,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(mention)
    return tuple(deduped)


def _dedupe_relations(
    relations: tuple[RelationCandidate, ...],
) -> tuple[RelationCandidate, ...]:
    deduped: list[RelationCandidate] = []
    seen: set[tuple[tuple[str, str], tuple[str, str], str, int]] = set()
    for relation in relations:
        key = (
            relation.source_key,
            relation.target_key,
            relation.relation_type,
            relation.source_document_chunk_id,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(relation)
    return tuple(deduped)
