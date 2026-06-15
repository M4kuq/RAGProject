from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.graph_models import GraphEntity, GraphEntityMention, GraphRelation
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument
from app.graph.neo4j_backend import Neo4jClient, Neo4jConnectionConfig, Neo4jUnavailable
from app.schemas.graph import validate_safe_graph_label


@dataclass(frozen=True)
class Neo4jProjectionResult:
    enabled: bool
    projected_entities: int = 0
    projected_relations: int = 0
    projected_mentions: int = 0
    projected_chunks: int = 0
    reason_codes: tuple[str, ...] = ()


class Neo4jProjectionService:
    def __init__(
        self,
        *,
        client: Neo4jClient | None = None,
        config: Neo4jConnectionConfig | None = None,
        projection_enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.projection_enabled = (
            bool(getattr(settings, "neo4j_projection_enabled", False))
            if projection_enabled is None
            else projection_enabled
        )
        self.client = client or Neo4jClient(
            config=config or Neo4jConnectionConfig.from_settings(settings)
        )

    def project_document_version(
        self,
        db: Session,
        *,
        document_version_id: int,
        graph_index_run_id: int | None = None,
    ) -> Neo4jProjectionResult:
        if not self.projection_enabled:
            return Neo4jProjectionResult(enabled=False, reason_codes=("neo4j_projection_disabled",))
        unavailable_reason = self.client.unavailable_reason()
        if unavailable_reason is not None:
            return Neo4jProjectionResult(enabled=True, reason_codes=(unavailable_reason,))

        projection = _load_projection_rows(
            db,
            document_version_id=document_version_id,
            graph_index_run_id=graph_index_run_id,
        )
        try:
            _ensure_constraints(self.client)
            _replace_document_version_projection(
                self.client,
                document_version_id=document_version_id,
                entities=projection.entities,
                chunks=projection.chunks,
                mentions=projection.mentions,
                relations=projection.relations,
            )
        except Neo4jUnavailable as exc:
            return Neo4jProjectionResult(enabled=True, reason_codes=(exc.reason_code,))
        return Neo4jProjectionResult(
            enabled=True,
            projected_entities=len(projection.entities),
            projected_relations=len(projection.relations),
            projected_mentions=len(projection.mentions),
            projected_chunks=len(projection.chunks),
            reason_codes=("neo4j_projection_completed",),
        )

    def close(self) -> None:
        self.client.close()


@dataclass(frozen=True)
class _ProjectionRows:
    entities: list[dict[str, object]]
    chunks: list[dict[str, object]]
    mentions: list[dict[str, object]]
    relations: list[dict[str, object]]


def _load_projection_rows(
    db: Session,
    *,
    document_version_id: int,
    graph_index_run_id: int | None,
) -> _ProjectionRows:
    chunk_rows = list(
        db.execute(
            select(
                DocumentChunk.document_chunk_id,
                DocumentChunk.document_version_id,
                DocumentChunk.chunk_hash,
                DocumentChunk.modality,
                DocumentVersion.logical_document_id,
                DocumentVersion.status,
                DocumentVersion.is_active,
                LogicalDocument.status,
            )
            .join(
                DocumentVersion,
                DocumentVersion.document_version_id == DocumentChunk.document_version_id,
            )
            .join(
                LogicalDocument,
                LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
            )
            .where(DocumentChunk.document_version_id == document_version_id)
            .order_by(DocumentChunk.document_chunk_id.asc())
        ).all()
    )
    chunk_ids = {
        document_chunk_id
        for (
            document_chunk_id,
            _version_id,
            _hash,
            _modality,
            _logical_id,
            _version_status,
            _version_is_active,
            _logical_status,
        ) in chunk_rows
    }
    mention_rows = list(
        db.execute(
            select(
                GraphEntityMention.graph_entity_mention_id,
                GraphEntityMention.graph_entity_id,
                GraphEntityMention.document_chunk_id,
                GraphEntityMention.document_version_id,
                GraphEntityMention.mention_text_hash,
                GraphEntityMention.confidence,
            )
            .where(GraphEntityMention.document_version_id == document_version_id)
            .order_by(GraphEntityMention.graph_entity_mention_id.asc())
        ).all()
    )
    relation_rows = (
        list(
            db.execute(
                select(
                    GraphRelation.graph_relation_id,
                    GraphRelation.source_entity_id,
                    GraphRelation.target_entity_id,
                    GraphRelation.relation_type,
                    GraphRelation.confidence,
                    GraphRelation.source_document_chunk_id,
                    GraphRelation.evidence_text_hash,
                )
                .where(GraphRelation.source_document_chunk_id.in_(chunk_ids))
                .order_by(GraphRelation.graph_relation_id.asc())
            ).all()
        )
        if chunk_ids
        else []
    )
    entity_ids = {
        *[graph_entity_id for _mention_id, graph_entity_id, *_rest in mention_rows],
        *[source_entity_id for _relation_id, source_entity_id, *_rest in relation_rows],
        *[
            target_entity_id
            for _relation_id, _source_entity_id, target_entity_id, *_rest in relation_rows
        ],
    }
    entity_rows = (
        list(
            db.execute(
                select(
                    GraphEntity.graph_entity_id,
                    GraphEntity.canonical_name,
                    GraphEntity.entity_type,
                    GraphEntity.aliases_json,
                )
                .where(GraphEntity.graph_entity_id.in_(entity_ids))
                .order_by(GraphEntity.graph_entity_id.asc())
            ).all()
        )
        if entity_ids
        else []
    )
    chunk_version_by_id = {
        document_chunk_id: chunk_document_version_id
        for (
            document_chunk_id,
            chunk_document_version_id,
            _chunk_hash,
            _modality,
            _logical_id,
            _version_status,
            _version_is_active,
            _logical_status,
        ) in chunk_rows
    }
    return _ProjectionRows(
        entities=[
            {
                "graph_entity_id": graph_entity_id,
                "safe_label": validate_safe_graph_label(
                    canonical_name,
                    field_name="canonical_name",
                    max_length=255,
                ),
                "entity_type": validate_safe_graph_label(
                    entity_type,
                    field_name="entity_type",
                    max_length=80,
                ),
                "aliases": _safe_aliases(aliases_json),
                "graph_index_run_id": graph_index_run_id,
            }
            for graph_entity_id, canonical_name, entity_type, aliases_json in entity_rows
        ],
        chunks=[
            {
                "document_chunk_id": document_chunk_id,
                "document_version_id": chunk_document_version_id,
                "logical_document_id": logical_document_id,
                "chunk_hash": chunk_hash,
                "modality": validate_safe_graph_label(
                    modality,
                    field_name="modality",
                    max_length=40,
                ),
                "document_version_status": validate_safe_graph_label(
                    document_version_status,
                    field_name="document_version_status",
                    max_length=40,
                ),
                "document_version_is_active": bool(document_version_is_active),
                "logical_document_status": validate_safe_graph_label(
                    logical_document_status,
                    field_name="logical_document_status",
                    max_length=40,
                ),
                "graph_index_run_id": graph_index_run_id,
            }
            for (
                document_chunk_id,
                chunk_document_version_id,
                chunk_hash,
                modality,
                logical_document_id,
                document_version_status,
                document_version_is_active,
                logical_document_status,
            ) in chunk_rows
        ],
        mentions=[
            {
                "graph_entity_mention_id": graph_entity_mention_id,
                "graph_entity_id": graph_entity_id,
                "document_chunk_id": document_chunk_id,
                "document_version_id": mention_document_version_id,
                "mention_text_hash": mention_text_hash,
                "confidence": _optional_float(confidence),
                "graph_index_run_id": graph_index_run_id,
            }
            for (
                graph_entity_mention_id,
                graph_entity_id,
                document_chunk_id,
                mention_document_version_id,
                mention_text_hash,
                confidence,
            ) in mention_rows
        ],
        relations=[
            {
                "graph_relation_id": graph_relation_id,
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
                "relation_type": validate_safe_graph_label(
                    relation_type,
                    field_name="relation_type",
                    max_length=120,
                ),
                "confidence": _optional_float(confidence),
                "source_document_chunk_id": source_document_chunk_id,
                "source_chunk_ids": [source_document_chunk_id]
                if source_document_chunk_id is not None
                else [],
                "document_version_id": (
                    chunk_version_by_id.get(source_document_chunk_id)
                    if source_document_chunk_id is not None
                    else None
                ),
                "evidence_text_hash": evidence_text_hash,
                "graph_index_run_id": graph_index_run_id,
            }
            for (
                graph_relation_id,
                source_entity_id,
                target_entity_id,
                relation_type,
                confidence,
                source_document_chunk_id,
                evidence_text_hash,
            ) in relation_rows
        ],
    )


def _ensure_constraints(client: Neo4jClient) -> None:
    for query in (
        """
        CREATE CONSTRAINT rag_graph_entity_id IF NOT EXISTS
        FOR (entity:RAGGraphEntity)
        REQUIRE entity.graph_entity_id IS UNIQUE
        """,
        """
        CREATE CONSTRAINT rag_graph_chunk_id IF NOT EXISTS
        FOR (chunk:RAGGraphChunk)
        REQUIRE chunk.document_chunk_id IS UNIQUE
        """,
    ):
        client.execute(query, {})


def _replace_document_version_projection(
    client: Neo4jClient,
    *,
    document_version_id: int,
    entities: list[dict[str, object]],
    chunks: list[dict[str, object]],
    mentions: list[dict[str, object]],
    relations: list[dict[str, object]],
) -> None:
    client.execute(
        """
        MATCH (:RAGGraphEntity)-[mention:MENTIONED_IN]->(:RAGGraphChunk)
        WHERE mention.document_version_id = $document_version_id
        DELETE mention
        """,
        {"document_version_id": document_version_id},
    )
    client.execute(
        """
        MATCH ()-[relation:GRAPH_RELATION]->()
        WHERE relation.document_version_id = $document_version_id
        DELETE relation
        """,
        {"document_version_id": document_version_id},
    )
    client.execute(
        """
        MATCH (chunk:RAGGraphChunk {document_version_id: $document_version_id})
        DETACH DELETE chunk
        """,
        {"document_version_id": document_version_id},
    )
    client.execute(
        """
        UNWIND $entities AS row
        MERGE (entity:RAGGraphEntity {graph_entity_id: row.graph_entity_id})
        SET entity.safe_label = row.safe_label,
            entity.entity_type = row.entity_type,
            entity.aliases = row.aliases,
            entity.last_graph_index_run_id = row.graph_index_run_id
        """,
        {"entities": entities},
    )
    client.execute(
        """
        UNWIND $chunks AS row
        MERGE (chunk:RAGGraphChunk {document_chunk_id: row.document_chunk_id})
        SET chunk.document_version_id = row.document_version_id,
            chunk.logical_document_id = row.logical_document_id,
            chunk.chunk_hash = row.chunk_hash,
            chunk.modality = row.modality,
            chunk.document_version_status = row.document_version_status,
            chunk.document_version_is_active = row.document_version_is_active,
            chunk.logical_document_status = row.logical_document_status,
            chunk.last_graph_index_run_id = row.graph_index_run_id
        """,
        {"chunks": chunks},
    )
    client.execute(
        """
        UNWIND $mentions AS row
        MATCH (entity:RAGGraphEntity {graph_entity_id: row.graph_entity_id})
        MATCH (chunk:RAGGraphChunk {document_chunk_id: row.document_chunk_id})
        MERGE (entity)-[mention:MENTIONED_IN {
            graph_entity_mention_id: row.graph_entity_mention_id
        }]->(chunk)
        SET mention.document_chunk_id = row.document_chunk_id,
            mention.document_version_id = row.document_version_id,
            mention.mention_text_hash = row.mention_text_hash,
            mention.confidence = row.confidence,
            mention.graph_index_run_id = row.graph_index_run_id
        """,
        {"mentions": mentions},
    )
    client.execute(
        """
        UNWIND $relations AS row
        MATCH (source:RAGGraphEntity {graph_entity_id: row.source_entity_id})
        MATCH (target:RAGGraphEntity {graph_entity_id: row.target_entity_id})
        MERGE (source)-[relation:GRAPH_RELATION {
            graph_relation_id: row.graph_relation_id
        }]->(target)
        SET relation.relation_type = row.relation_type,
            relation.confidence = row.confidence,
            relation.source_document_chunk_id = row.source_document_chunk_id,
            relation.source_chunk_ids = row.source_chunk_ids,
            relation.document_version_id = row.document_version_id,
            relation.evidence_text_hash = row.evidence_text_hash,
            relation.graph_index_run_id = row.graph_index_run_id
        """,
        {"relations": relations},
    )


def _optional_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _safe_aliases(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    aliases: list[str] = []
    seen: set[str] = set()
    for alias in value:
        try:
            safe_alias = validate_safe_graph_label(
                str(alias),
                field_name="aliases_json",
                max_length=120,
            )
        except ValueError:
            continue
        key = safe_alias.lower()
        if key in seen:
            continue
        aliases.append(safe_alias)
        seen.add(key)
        if len(aliases) >= 32:
            break
    return aliases
