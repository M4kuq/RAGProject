from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ResourceNotFound
from app.db.graph_models import GraphEntity, GraphEntityMention, GraphIndexRun, GraphRelation
from app.db.models import DocumentChunk, DocumentVersion
from app.graph.constants import (
    DEFAULT_GRAPH_EXTRACTOR_TYPE,
    GRAPH_EXTRACTION_LLM_FAILED,
    GRAPH_EXTRACTION_LLM_FALLBACK,
    GRAPH_EXTRACTION_LLM_PARTIAL_COMPLETED,
    GRAPH_EXTRACTION_LLM_PARTIAL_REBUILD_SKIPPED,
    GRAPH_EXTRACTION_LLM_RETRYABLE_FAILED,
    GRAPH_EXTRACTION_LLM_UNAVAILABLE,
    GRAPH_INDEX_BUILD_JOB_TYPE,
    LLM_GRAPH_EXTRACTOR_TYPE,
    LLM_GRAPH_EXTRACTOR_VERSION,
)
from app.graph.extraction import (
    RULE_BASED_GRAPH_EXTRACTOR_TYPE,
    RULE_BASED_GRAPH_EXTRACTOR_VERSION,
    EntityExtractionService,
    EntityMentionCandidate,
    GraphChunkRef,
    GraphExtractionResult,
    GraphExtractor,
    RelationCandidate,
    RelationExtractionService,
    RuleBasedGraphExtractor,
)
from app.graph.job_settings import graph_extraction_settings, graph_extractor_type_override
from app.graph.llm_extraction import LLMGraphExtractionError, LLMGraphExtractor
from app.repositories.graph_repository import GraphRepository
from app.schemas.graph import (
    GraphEntityCreate,
    GraphEntityMentionCreate,
    GraphIndexJobPayload,
    GraphIndexRunCreate,
    GraphIndexSummary,
    GraphRelationCreate,
    validate_safe_graph_metadata,
)

logger = logging.getLogger(__name__)

_RETRYABLE_LLM_FAILURE_REASON_CODES = frozenset(
    {
        GRAPH_EXTRACTION_LLM_UNAVAILABLE,
        GRAPH_EXTRACTION_LLM_FAILED,
    }
)
_RETRYABLE_LLM_FAILURE_MESSAGE = "Graph LLM extraction failed retryably."
_PARTIAL_REBUILD_SKIPPED_MESSAGE = "Graph LLM partial rebuild skipped to preserve existing index."


@dataclass(frozen=True)
class GraphIndexBuildSnapshot:
    graph_index_run_id: int
    document_version_id: int
    logical_document_id: int
    job_id: int | None
    extractor_type: str
    extractor_version: str | None
    chunks: tuple[GraphChunkRef, ...]
    graph_extraction_settings: Settings


class Neo4jProjectionServiceProtocol(Protocol):
    def project_document_version(
        self,
        db: Session,
        *,
        document_version_id: int,
        graph_index_run_id: int | None = None,
    ) -> object: ...


class GraphIndexService:
    """Build document-version graph indexes without persisting raw evidence text."""

    def __init__(
        self,
        repository: GraphRepository | None = None,
        *,
        entity_extractor: EntityExtractionService | None = None,
        relation_extractor: RelationExtractionService | None = None,
        graph_extractor: GraphExtractor | None = None,
        llm_extractor: GraphExtractor | None = None,
        neo4j_projection_service: Neo4jProjectionServiceProtocol | None = None,
        settings: Settings | None = None,
        extractor_type: str | None = None,
        extractor_version: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.repository = repository or GraphRepository()
        self.rule_based_extractor = RuleBasedGraphExtractor(
            entity_extractor=entity_extractor
            or EntityExtractionService(
                max_entities_per_chunk=self.settings.graph_extraction_max_entities_per_chunk,
            ),
            relation_extractor=relation_extractor
            or RelationExtractionService(
                max_relations_per_chunk=self.settings.graph_extraction_max_relations_per_chunk,
            ),
        )
        self.graph_extractor = graph_extractor
        self.llm_extractor = llm_extractor
        self.neo4j_projection_service = neo4j_projection_service
        self._uses_custom_rule_based_extractor = (
            entity_extractor is not None or relation_extractor is not None
        )
        self.extractor_type = _requested_extractor_type(
            extractor_type or self.settings.graph_extractor_type
        )
        self.extractor_version = extractor_version or _default_extractor_version(
            self.extractor_type
        )

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
        effective_settings = graph_extraction_settings(db, self.settings)
        effective_extractor_type = extractor_type or graph_extractor_type_override(db)

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
                    extractor_type=effective_extractor_type or self.extractor_type,
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
        if extractor_type is not None:
            run.extractor_type = _requested_extractor_type(extractor_type)
            run.extractor_version = extractor_version or _default_extractor_version(
                run.extractor_type
            )
        if run.extractor_type == "none":
            run.extractor_type = effective_extractor_type or self.extractor_type
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
            graph_extraction_settings=effective_settings,
        )

    def extract_from_snapshot(self, snapshot: GraphIndexBuildSnapshot) -> GraphExtractionResult:
        extractor_type = _requested_extractor_type(snapshot.extractor_type)
        if self.graph_extractor is not None:
            return _dedupe_result(self.graph_extractor.extract(snapshot.chunks))
        if extractor_type == LLM_GRAPH_EXTRACTOR_TYPE:
            try:
                return _dedupe_result(
                    self._llm_extractor(snapshot.graph_extraction_settings).extract(snapshot.chunks)
                )
            except LLMGraphExtractionError as exc:
                if exc.reason_code in _RETRYABLE_LLM_FAILURE_REASON_CODES:
                    logger.warning(
                        "graph llm extraction failed retryably",
                        extra={
                            "document_version_id": snapshot.document_version_id,
                            "graph_index_run_id": snapshot.graph_index_run_id,
                            "reason_code": exc.reason_code,
                            "provider": self._llm_provider_name(snapshot.graph_extraction_settings),
                        },
                    )
                    return _failed_llm_result(
                        reason_code=exc.reason_code,
                        provider=self._llm_provider_name(snapshot.graph_extraction_settings),
                        model_name=self._llm_model_name(snapshot.graph_extraction_settings),
                    )
                logger.warning(
                    "graph llm extraction fell back to rule_based",
                    extra={
                        "document_version_id": snapshot.document_version_id,
                        "graph_index_run_id": snapshot.graph_index_run_id,
                        "reason_code": exc.reason_code,
                        "provider": self._llm_provider_name(snapshot.graph_extraction_settings),
                    },
                )
                fallback = self._rule_based_extractor(snapshot.graph_extraction_settings).extract(
                    snapshot.chunks
                )
                fallback_metadata = dict(fallback.metadata_json)
                fallback_metadata.update(
                    {
                        "extractor_result_code": GRAPH_EXTRACTION_LLM_FALLBACK,
                        "requested_extractor_type": LLM_GRAPH_EXTRACTOR_TYPE,
                        "fallback_reason_code": exc.reason_code,
                        "fallback_extractor_type": RULE_BASED_GRAPH_EXTRACTOR_TYPE,
                        "graph_extraction_provider": self._llm_provider_name(
                            snapshot.graph_extraction_settings
                        ),
                        "graph_extraction_model": self._llm_model_name(
                            snapshot.graph_extraction_settings
                        ),
                    }
                )
                return _dedupe_result(
                    GraphExtractionResult(
                        entity_mentions=fallback.entity_mentions,
                        relations=fallback.relations,
                        extractor_type=RULE_BASED_GRAPH_EXTRACTOR_TYPE,
                        extractor_version=RULE_BASED_GRAPH_EXTRACTOR_VERSION,
                        metadata_json=validate_safe_graph_metadata(fallback_metadata),
                    )
                )
        return _dedupe_result(
            self._rule_based_extractor(snapshot.graph_extraction_settings).extract(snapshot.chunks)
        )

    def _rule_based_extractor(self, settings: Settings) -> GraphExtractor:
        if self._uses_custom_rule_based_extractor or settings is self.settings:
            return self.rule_based_extractor
        return RuleBasedGraphExtractor(
            entity_extractor=EntityExtractionService(
                max_entities_per_chunk=settings.graph_extraction_max_entities_per_chunk,
            ),
            relation_extractor=RelationExtractionService(
                max_relations_per_chunk=settings.graph_extraction_max_relations_per_chunk,
            ),
        )

    def _llm_extractor(self, settings: Settings) -> GraphExtractor:
        if self.llm_extractor is None:
            if settings is self.settings:
                self.llm_extractor = LLMGraphExtractor(settings=self.settings)
            else:
                return LLMGraphExtractor(settings=settings)
        return self.llm_extractor

    def _llm_provider_name(self, settings: Settings) -> str:
        return (settings.graph_extraction_provider or settings.generation_provider).strip().lower()

    def _llm_model_name(self, settings: Settings) -> str:
        return (settings.graph_extraction_model_name or settings.generation_model_name).strip()[
            :160
        ]

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
        run.extractor_type = result.extractor_type
        run.extractor_version = result.extractor_version
        run.metadata_json = validate_safe_graph_metadata(
            {
                **(run.metadata_json or {}),
                **result.metadata_json,
            }
        )

        version = db.get(DocumentVersion, snapshot.document_version_id)
        if version is None:
            raise ResourceNotFound()
        if version.status != "ready":
            raise ValueError("document_version_id must reference a ready document version")

        self.repository.acquire_graph_index_document_version_lock(
            db,
            document_version_id=snapshot.document_version_id,
        )
        result_code = _extractor_result_code(result)
        if result_code == GRAPH_EXTRACTION_LLM_RETRYABLE_FAILED:
            self.repository.mark_graph_index_run_failed(
                db,
                run=run,
                error_code=GRAPH_EXTRACTION_LLM_RETRYABLE_FAILED,
                error_message=_RETRYABLE_LLM_FAILURE_MESSAGE,
            )
            return run
        if (
            result_code == GRAPH_EXTRACTION_LLM_PARTIAL_COMPLETED
            and _document_version_has_graph_artifacts(
                db,
                document_version_id=snapshot.document_version_id,
            )
        ):
            run.metadata_json = validate_safe_graph_metadata(
                {
                    **(run.metadata_json or {}),
                    "extractor_result_code": GRAPH_EXTRACTION_LLM_PARTIAL_REBUILD_SKIPPED,
                    "partial_result_code": GRAPH_EXTRACTION_LLM_PARTIAL_COMPLETED,
                    "retryable": True,
                }
            )
            self.repository.mark_graph_index_run_failed(
                db,
                run=run,
                error_code=GRAPH_EXTRACTION_LLM_PARTIAL_REBUILD_SKIPPED,
                error_message=_PARTIAL_REBUILD_SKIPPED_MESSAGE,
            )
            return run
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

    def project_neo4j_index_run(
        self,
        db: Session,
        *,
        document_version_id: int,
        graph_index_run_id: int,
    ) -> object:
        projection_service = self.neo4j_projection_service
        temporary_projection_service = False
        if projection_service is None:
            from app.services.neo4j_projection_service import Neo4jProjectionService

            projection_service = Neo4jProjectionService()
            temporary_projection_service = True
        try:
            return projection_service.project_document_version(
                db,
                document_version_id=document_version_id,
                graph_index_run_id=graph_index_run_id,
            )
        finally:
            if temporary_projection_service:
                close = getattr(projection_service, "close", None)
                if callable(close):
                    close()


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


def _dedupe_result(result: GraphExtractionResult) -> GraphExtractionResult:
    return GraphExtractionResult(
        entity_mentions=_dedupe_mentions(result.entity_mentions),
        relations=_dedupe_relations(result.relations),
        extractor_type=result.extractor_type,
        extractor_version=result.extractor_version,
        metadata_json=validate_safe_graph_metadata(dict(result.metadata_json)),
    )


def _failed_llm_result(
    *,
    reason_code: str,
    provider: str,
    model_name: str,
) -> GraphExtractionResult:
    return GraphExtractionResult(
        entity_mentions=(),
        relations=(),
        extractor_type=LLM_GRAPH_EXTRACTOR_TYPE,
        extractor_version=LLM_GRAPH_EXTRACTOR_VERSION,
        metadata_json=validate_safe_graph_metadata(
            {
                "extractor_result_code": GRAPH_EXTRACTION_LLM_RETRYABLE_FAILED,
                "requested_extractor_type": LLM_GRAPH_EXTRACTOR_TYPE,
                "llm_failure_reason_code": reason_code,
                "graph_extraction_provider": provider,
                "graph_extraction_model": model_name,
                "retryable": True,
            }
        ),
    )


def _extractor_result_code(result: GraphExtractionResult) -> str | None:
    result_code = result.metadata_json.get("extractor_result_code")
    if isinstance(result_code, str):
        return result_code
    return None


def _document_version_has_graph_artifacts(
    db: Session,
    *,
    document_version_id: int,
) -> bool:
    mention_id = db.scalar(
        select(GraphEntityMention.graph_entity_mention_id)
        .where(GraphEntityMention.document_version_id == document_version_id)
        .limit(1)
    )
    if mention_id is not None:
        return True
    chunk_ids = select(DocumentChunk.document_chunk_id).where(
        DocumentChunk.document_version_id == document_version_id
    )
    relation_id = db.scalar(
        select(GraphRelation.graph_relation_id)
        .where(GraphRelation.source_document_chunk_id.in_(chunk_ids))
        .limit(1)
    )
    return relation_id is not None


def _requested_extractor_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"", "none"}:
        return DEFAULT_GRAPH_EXTRACTOR_TYPE
    if normalized in {LLM_GRAPH_EXTRACTOR_TYPE, RULE_BASED_GRAPH_EXTRACTOR_TYPE}:
        return normalized
    logger.warning("unknown graph extractor requested; falling back to rule_based")
    return RULE_BASED_GRAPH_EXTRACTOR_TYPE


def _default_extractor_version(extractor_type: str) -> str | None:
    if extractor_type == LLM_GRAPH_EXTRACTOR_TYPE:
        return LLM_GRAPH_EXTRACTOR_VERSION
    if extractor_type == RULE_BASED_GRAPH_EXTRACTOR_TYPE:
        return RULE_BASED_GRAPH_EXTRACTOR_VERSION
    return None


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
