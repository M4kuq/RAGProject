"""add graph schema index foundation

Revision ID: 0012_graph_schema_index
Revises: 0011_tool_result_compression
Create Date: 2026-06-03
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012_graph_schema_index"
down_revision = "0011_tool_result_compression"
branch_labels = None
depends_on = None

_GRAPH_INDEX_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "skipped",
)
_GRAPH_SETTINGS = {
    "rag.graph.enabled": (
        False,
        "Enable Graph-RAG retrieval. PR-46 default is disabled.",
    ),
    "rag.graph.indexing.enabled": (
        False,
        "Enable graph index build jobs. PR-46 default is disabled.",
    ),
    "rag.graph.extractor.default": (
        "none",
        "Default graph extractor. PR-47 connects extractors.",
    ),
    "rag.graph.max_entities_per_chunk": (
        20,
        "Maximum entity candidates per chunk.",
    ),
    "rag.graph.max_relations_per_chunk": (
        40,
        "Maximum relation candidates per chunk.",
    ),
    "rag.graph.store_raw_evidence_text": (
        False,
        "Raw graph evidence text must not be stored.",
    ),
    "rag.graph.retrieval.enabled": (
        False,
        "Enable graph retrieval strategies. PR-48 connects retrieval.",
    ),
}


def upgrade() -> None:
    op.create_table(
        "graph_entities",
        sa.Column("graph_entity_id", sa.BigInteger(), primary_key=True),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column(
            "aliases_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("description", sa.Text()),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(canonical_name) <> ''", name="ck_graph_entities_name"),
        sa.CheckConstraint("btrim(entity_type) <> ''", name="ck_graph_entities_type"),
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_graph_entities_lower_name_type "
        "ON graph_entities (lower(canonical_name), entity_type)"
    )
    op.create_index(
        "ix_graph_entities_entity_type",
        "graph_entities",
        ["entity_type"],
    )
    op.create_index(
        "ix_graph_entities_aliases_json",
        "graph_entities",
        ["aliases_json"],
        postgresql_using="gin",
    )

    op.create_table(
        "graph_index_runs",
        sa.Column("graph_index_run_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.BigInteger(),
            sa.ForeignKey("document_versions.document_version_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "job_id",
            sa.BigInteger(),
            sa.ForeignKey("jobs.job_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "status",
            sa.String(30),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "extractor_type",
            sa.String(80),
            server_default=sa.text("'none'"),
            nullable=False,
        ),
        sa.Column("extractor_version", sa.String(80)),
        sa.Column(
            "entity_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "relation_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "mention_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(120)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'skipped')",
            name="ck_graph_index_runs_status",
        ),
        sa.CheckConstraint("entity_count >= 0", name="ck_graph_index_runs_entity_count"),
        sa.CheckConstraint(
            "relation_count >= 0",
            name="ck_graph_index_runs_relation_count",
        ),
        sa.CheckConstraint("mention_count >= 0", name="ck_graph_index_runs_mention_count"),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_graph_index_runs_finished_after_started",
        ),
        sa.CheckConstraint(
            "status <> 'running' OR (started_at IS NOT NULL AND finished_at IS NULL)",
            name="ck_graph_index_runs_running_times",
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'cancelled', 'skipped') "
            "OR finished_at IS NOT NULL",
            name="ck_graph_index_runs_terminal_finished",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_graph_index_runs_failed_error_code",
        ),
    )
    op.create_index(
        "ix_graph_index_runs_document_status",
        "graph_index_runs",
        ["document_version_id", "status"],
    )
    op.create_index(
        "ix_graph_index_runs_status_created",
        "graph_index_runs",
        ["status", "created_at"],
    )
    op.create_index("ix_graph_index_runs_job", "graph_index_runs", ["job_id"])

    op.create_table(
        "graph_relations",
        sa.Column("graph_relation_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "source_entity_id",
            sa.BigInteger(),
            sa.ForeignKey("graph_entities.graph_entity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_entity_id",
            sa.BigInteger(),
            sa.ForeignKey("graph_entities.graph_entity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(120), nullable=False),
        sa.Column("relation_label", sa.String(255)),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column(
            "source_document_chunk_id",
            sa.BigInteger(),
            sa.ForeignKey("document_chunks.document_chunk_id", ondelete="CASCADE"),
        ),
        sa.Column("evidence_text_hash", sa.String(64)),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_entity_id <> target_entity_id",
            name="ck_graph_relations_no_self",
        ),
        sa.CheckConstraint("btrim(relation_type) <> ''", name="ck_graph_relations_type"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_graph_relations_confidence",
        ),
        sa.CheckConstraint(
            "evidence_text_hash IS NULL OR evidence_text_hash ~ '^[0-9a-f]{64}$'",
            name="ck_graph_relations_evidence_hash",
        ),
        sa.UniqueConstraint(
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            "source_document_chunk_id",
            name="uq_graph_relations_source_target_type_chunk",
        ),
    )
    op.create_index(
        "ix_graph_relations_source_type",
        "graph_relations",
        ["source_entity_id", "relation_type"],
    )
    op.create_index(
        "ix_graph_relations_target_type",
        "graph_relations",
        ["target_entity_id", "relation_type"],
    )
    op.create_index(
        "ix_graph_relations_source_chunk",
        "graph_relations",
        ["source_document_chunk_id"],
    )
    op.create_index(
        "ux_graph_relations_source_target_type_no_chunk",
        "graph_relations",
        ["source_entity_id", "target_entity_id", "relation_type"],
        unique=True,
        postgresql_where=sa.text("source_document_chunk_id IS NULL"),
    )

    op.create_table(
        "graph_entity_mentions",
        sa.Column("graph_entity_mention_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "graph_entity_id",
            sa.BigInteger(),
            sa.ForeignKey("graph_entities.graph_entity_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_chunk_id",
            sa.BigInteger(),
            sa.ForeignKey("document_chunks.document_chunk_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.BigInteger(),
            sa.ForeignKey("document_versions.document_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mention_text_hash", sa.String(64)),
        sa.Column("mention_offset_start", sa.Integer()),
        sa.Column("mention_offset_end", sa.Integer()),
        sa.Column("confidence", sa.Numeric(6, 5)),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mention_text_hash IS NULL OR mention_text_hash ~ '^[0-9a-f]{64}$'",
            name="ck_graph_entity_mentions_hash",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_graph_entity_mentions_confidence",
        ),
        sa.CheckConstraint(
            "mention_offset_start IS NULL OR mention_offset_start >= 0",
            name="ck_graph_entity_mentions_offset_start",
        ),
        sa.CheckConstraint(
            "mention_offset_end IS NULL OR mention_offset_end >= 0",
            name="ck_graph_entity_mentions_offset_end",
        ),
        sa.CheckConstraint(
            "mention_offset_start IS NULL OR mention_offset_end IS NULL "
            "OR mention_offset_end >= mention_offset_start",
            name="ck_graph_entity_mentions_offset_order",
        ),
        sa.UniqueConstraint(
            "graph_entity_id",
            "document_chunk_id",
            "mention_text_hash",
            "mention_offset_start",
            "mention_offset_end",
            name="uq_graph_entity_mentions_entity_chunk_hash_offsets",
        ),
    )
    op.create_index(
        "ix_graph_entity_mentions_entity",
        "graph_entity_mentions",
        ["graph_entity_id"],
    )
    op.create_index(
        "ix_graph_entity_mentions_chunk",
        "graph_entity_mentions",
        ["document_chunk_id"],
    )
    op.create_index(
        "ix_graph_entity_mentions_version",
        "graph_entity_mentions",
        ["document_version_id"],
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_graph_entity_mentions_entity_chunk_hash_offsets_coalesced "
        "ON graph_entity_mentions ("
        "graph_entity_id, document_chunk_id, "
        "COALESCE(mention_text_hash, ''), "
        "COALESCE(mention_offset_start, -1), "
        "COALESCE(mention_offset_end, -1))"
    )

    op.create_table(
        "graph_retrieval_paths",
        sa.Column("graph_retrieval_path_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "retrieval_run_id",
            sa.BigInteger(),
            sa.ForeignKey("retrieval_runs.retrieval_run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "score_breakdown_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "source_chunk_ids_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("jsonb_typeof(path_json) = 'object'", name="ck_graph_paths_path_object"),
        sa.CheckConstraint(
            "jsonb_typeof(score_breakdown_json) = 'object'",
            name="ck_graph_paths_score_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_chunk_ids_json) = 'array'",
            name="ck_graph_paths_source_chunks_array",
        ),
    )
    op.create_index(
        "ix_graph_retrieval_paths_retrieval_run",
        "graph_retrieval_paths",
        ["retrieval_run_id"],
    )

    _seed_graph_settings()


def downgrade() -> None:
    # Keep rag.graph.* settings on rollback. The upgrade uses ON CONFLICT DO NOTHING,
    # so downgrade cannot distinguish migration-seeded defaults from pre-existing
    # operator-provided settings without risking destructive configuration loss.
    op.drop_table("graph_retrieval_paths")
    op.drop_table("graph_entity_mentions")
    op.drop_table("graph_relations")
    op.drop_table("graph_index_runs")
    op.drop_table("graph_entities")


def _seed_graph_settings() -> None:
    bind = op.get_bind()
    for key, (value, description) in _GRAPH_SETTINGS.items():
        bind.execute(
            sa.text(
                """
                INSERT INTO system_settings (setting_key, setting_value, description)
                VALUES (:setting_key, CAST(:setting_value AS jsonb), :description)
                ON CONFLICT (setting_key) DO NOTHING
                """
            ),
            {
                "setting_key": key,
                "setting_value": json.dumps(value),
                "description": description,
            },
        )
