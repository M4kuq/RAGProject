from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models import big_int, jsonb, pg_check


class GraphEntity(Base):
    __tablename__ = "graph_entities"
    __table_args__ = (
        CheckConstraint("btrim(canonical_name) <> ''", name="ck_graph_entities_name"),
        CheckConstraint("btrim(entity_type) <> ''", name="ck_graph_entities_type"),
    )

    graph_entity_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aliases_json: Mapped[list[Any]] = mapped_column(
        jsonb(), default=list, server_default=text("'[]'"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        jsonb(), default=dict, server_default=text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    outgoing_relations: Mapped[list[GraphRelation]] = relationship(
        "GraphRelation",
        back_populates="source_entity",
        cascade="all, delete-orphan",
        foreign_keys="GraphRelation.source_entity_id",
    )
    incoming_relations: Mapped[list[GraphRelation]] = relationship(
        "GraphRelation",
        back_populates="target_entity",
        cascade="all, delete-orphan",
        foreign_keys="GraphRelation.target_entity_id",
    )
    mentions: Mapped[list[GraphEntityMention]] = relationship(
        "GraphEntityMention",
        back_populates="entity",
        cascade="all, delete-orphan",
    )


class GraphRelation(Base):
    __tablename__ = "graph_relations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_entity_id"],
            ["graph_entities.graph_entity_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["target_entity_id"],
            ["graph_entities.graph_entity_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_document_chunk_id"],
            ["document_chunks.document_chunk_id"],
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "source_entity_id <> target_entity_id",
            name="ck_graph_relations_no_self",
        ),
        CheckConstraint("btrim(relation_type) <> ''", name="ck_graph_relations_type"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_graph_relations_confidence",
        ),
        pg_check(
            "evidence_text_hash IS NULL OR evidence_text_hash ~ '^[0-9a-f]{64}$'",
            name="ck_graph_relations_evidence_hash",
        ),
        UniqueConstraint(
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            "source_document_chunk_id",
            name="uq_graph_relations_source_target_type_chunk",
        ),
    )

    graph_relation_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    source_entity_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    target_entity_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(120), nullable=False)
    relation_label: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    source_document_chunk_id: Mapped[int | None] = mapped_column(big_int())
    evidence_text_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        jsonb(), default=dict, server_default=text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source_entity: Mapped[GraphEntity] = relationship(
        "GraphEntity",
        back_populates="outgoing_relations",
        foreign_keys=[source_entity_id],
    )
    target_entity: Mapped[GraphEntity] = relationship(
        "GraphEntity",
        back_populates="incoming_relations",
        foreign_keys=[target_entity_id],
    )


class GraphEntityMention(Base):
    __tablename__ = "graph_entity_mentions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["graph_entity_id"],
            ["graph_entities.graph_entity_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_chunk_id"],
            ["document_chunks.document_chunk_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.document_version_id"],
            ondelete="CASCADE",
        ),
        pg_check(
            "mention_text_hash IS NULL OR mention_text_hash ~ '^[0-9a-f]{64}$'",
            name="ck_graph_entity_mentions_hash",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_graph_entity_mentions_confidence",
        ),
        CheckConstraint(
            "mention_offset_start IS NULL OR mention_offset_start >= 0",
            name="ck_graph_entity_mentions_offset_start",
        ),
        CheckConstraint(
            "mention_offset_end IS NULL OR mention_offset_end >= 0",
            name="ck_graph_entity_mentions_offset_end",
        ),
        CheckConstraint(
            "mention_offset_start IS NULL OR mention_offset_end IS NULL "
            "OR mention_offset_end >= mention_offset_start",
            name="ck_graph_entity_mentions_offset_order",
        ),
        UniqueConstraint(
            "graph_entity_id",
            "document_chunk_id",
            "mention_text_hash",
            name="uq_graph_entity_mentions_entity_chunk_hash",
        ),
    )

    graph_entity_mention_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    graph_entity_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    document_chunk_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    document_version_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    mention_text_hash: Mapped[str | None] = mapped_column(String(64))
    mention_offset_start: Mapped[int | None] = mapped_column(Integer)
    mention_offset_end: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        jsonb(), default=dict, server_default=text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped[GraphEntity] = relationship("GraphEntity", back_populates="mentions")


class GraphIndexRun(Base):
    __tablename__ = "graph_index_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.document_version_id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="SET NULL"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'skipped')",
            name="ck_graph_index_runs_status",
        ),
        CheckConstraint("entity_count >= 0", name="ck_graph_index_runs_entity_count"),
        CheckConstraint("relation_count >= 0", name="ck_graph_index_runs_relation_count"),
        CheckConstraint("mention_count >= 0", name="ck_graph_index_runs_mention_count"),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_graph_index_runs_finished_after_started",
        ),
        CheckConstraint(
            "status <> 'running' OR (started_at IS NOT NULL AND finished_at IS NULL)",
            name="ck_graph_index_runs_running_times",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'cancelled', 'skipped') "
            "OR finished_at IS NOT NULL",
            name="ck_graph_index_runs_terminal_finished",
        ),
        CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_graph_index_runs_failed_error_code",
        ),
    )

    graph_index_run_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    document_version_id: Mapped[int | None] = mapped_column(big_int())
    job_id: Mapped[int | None] = mapped_column(big_int())
    status: Mapped[str] = mapped_column(
        String(30), default="queued", server_default=text("'queued'"), nullable=False
    )
    extractor_type: Mapped[str] = mapped_column(
        String(80), default="none", server_default=text("'none'"), nullable=False
    )
    extractor_version: Mapped[str | None] = mapped_column(String(80))
    entity_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    relation_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    mention_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        jsonb(), default=dict, server_default=text("'{}'"), nullable=False
    )


class GraphRetrievalPath(Base):
    __tablename__ = "graph_retrieval_paths"
    __table_args__ = (
        ForeignKeyConstraint(
            ["retrieval_run_id"],
            ["retrieval_runs.retrieval_run_id"],
            ondelete="CASCADE",
        ),
        pg_check("jsonb_typeof(path_json) = 'object'", name="ck_graph_paths_path_object"),
        pg_check(
            "jsonb_typeof(score_breakdown_json) = 'object'",
            name="ck_graph_paths_score_object",
        ),
        pg_check(
            "jsonb_typeof(source_chunk_ids_json) = 'array'",
            name="ck_graph_paths_source_chunks_array",
        ),
    )

    graph_retrieval_path_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    retrieval_run_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    path_json: Mapped[dict[str, Any]] = mapped_column(jsonb(), nullable=False)
    score_breakdown_json: Mapped[dict[str, Any]] = mapped_column(
        jsonb(), default=dict, server_default=text("'{}'"), nullable=False
    )
    source_chunk_ids_json: Mapped[list[int]] = mapped_column(
        jsonb(), default=list, server_default=text("'[]'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index(
    "ux_graph_entities_lower_name_type",
    func.lower(GraphEntity.canonical_name),
    GraphEntity.entity_type,
    unique=True,
)
Index("ix_graph_entities_entity_type", GraphEntity.entity_type)
Index(
    "ix_graph_entities_aliases_json",
    GraphEntity.aliases_json,
    postgresql_using="gin",
)
Index(
    "ix_graph_relations_source_type",
    GraphRelation.source_entity_id,
    GraphRelation.relation_type,
)
Index(
    "ix_graph_relations_target_type",
    GraphRelation.target_entity_id,
    GraphRelation.relation_type,
)
Index("ix_graph_relations_source_chunk", GraphRelation.source_document_chunk_id)
Index("ix_graph_entity_mentions_entity", GraphEntityMention.graph_entity_id)
Index("ix_graph_entity_mentions_chunk", GraphEntityMention.document_chunk_id)
Index("ix_graph_entity_mentions_version", GraphEntityMention.document_version_id)
Index(
    "ix_graph_index_runs_document_status",
    GraphIndexRun.document_version_id,
    GraphIndexRun.status,
)
Index("ix_graph_index_runs_status_created", GraphIndexRun.status, GraphIndexRun.created_at)
Index("ix_graph_index_runs_job", GraphIndexRun.job_id)
Index("ix_graph_retrieval_paths_retrieval_run", GraphRetrievalPath.retrieval_run_id)
