"""add strategy agnostic retrieval cache

Revision ID: 0017_retrieval_cache_foundation
Revises: 0016_graph_store_provider_seed
Create Date: 2026-06-18
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op  # type: ignore[attr-defined]

revision = "0017_retrieval_cache_foundation"
down_revision = "0016_graph_store_provider_seed"
branch_labels = None
depends_on = None

_CORPUS_MARKER_SETTING_KEY = "rag.retrieval_cache.corpus_marker"


def _jsonb() -> Any:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("retrieval_runs", "cache_summary_json"):
        op.add_column("retrieval_runs", sa.Column("cache_summary_json", _jsonb()))

    if not _has_table("retrieval_cache_entries"):
        op.create_table(
            "retrieval_cache_entries",
            sa.Column("retrieval_cache_entry_id", sa.BigInteger(), primary_key=True),
            sa.Column("cache_namespace", sa.String(80), nullable=False),
            sa.Column("cache_key", sa.String(64), nullable=False),
            sa.Column("schema_version", sa.String(80), nullable=False),
            sa.Column("strategy_type", sa.String(50), nullable=False),
            sa.Column("query_hash", sa.String(64), nullable=False),
            sa.Column("retrieval_settings_hash", sa.String(64), nullable=False),
            sa.Column("rerank_settings_hash", sa.String(64), nullable=False),
            sa.Column("embedding_model", sa.String(255), nullable=False),
            sa.Column("rerank_model", sa.String(255), nullable=False),
            sa.Column("active_document_fingerprint", sa.String(64), nullable=False),
            sa.Column("graph_index_fingerprint", sa.String(64), nullable=False),
            sa.Column("graph_store_provider", sa.String(50), nullable=False),
            sa.Column("top_k", sa.Integer(), nullable=False),
            sa.Column("rerank_top_n", sa.Integer(), nullable=False),
            sa.Column("user_visible_scope", sa.String(64), nullable=False),
            sa.Column("payload_json", _jsonb(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_accessed_at", sa.DateTime(timezone=True)),
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
            sa.UniqueConstraint("cache_key", name="uq_retrieval_cache_entries_key"),
            sa.CheckConstraint(
                "top_k BETWEEN 1 AND 20",
                name="ck_retrieval_cache_entries_top_k",
            ),
            sa.CheckConstraint(
                "rerank_top_n BETWEEN 1 AND 20",
                name="ck_retrieval_cache_entries_rerank_top_n",
            ),
            sa.CheckConstraint(
                "expires_at > created_at",
                name="ck_retrieval_cache_entries_expires_after_created",
            ),
            sa.CheckConstraint(
                "btrim(cache_namespace) <> ''",
                name="ck_retrieval_cache_entries_namespace",
            ),
            sa.CheckConstraint(
                "btrim(schema_version) <> ''",
                name="ck_retrieval_cache_entries_schema",
            ),
            sa.CheckConstraint(
                "btrim(strategy_type) <> ''",
                name="ck_retrieval_cache_entries_strategy",
            ),
            sa.CheckConstraint(
                "btrim(embedding_model) <> ''",
                name="ck_retrieval_cache_entries_embedding_model",
            ),
            sa.CheckConstraint(
                "btrim(rerank_model) <> ''",
                name="ck_retrieval_cache_entries_rerank_model",
            ),
            sa.CheckConstraint(
                "btrim(graph_store_provider) <> ''",
                name="ck_retrieval_cache_entries_graph_store",
            ),
            sa.CheckConstraint(
                "cache_key ~ '^[0-9a-f]{64}$'",
                name="ck_retrieval_cache_entries_cache_key_hash",
            ),
            sa.CheckConstraint(
                "query_hash ~ '^[0-9a-f]{64}$'",
                name="ck_retrieval_cache_entries_query_hash",
            ),
            sa.CheckConstraint(
                "retrieval_settings_hash ~ '^[0-9a-f]{64}$'",
                name="ck_retrieval_cache_entries_retrieval_hash",
            ),
            sa.CheckConstraint(
                "rerank_settings_hash ~ '^[0-9a-f]{64}$'",
                name="ck_retrieval_cache_entries_rerank_hash",
            ),
            sa.CheckConstraint(
                "active_document_fingerprint ~ '^[0-9a-f]{64}$'",
                name="ck_retrieval_cache_entries_document_fp",
            ),
            sa.CheckConstraint(
                "graph_index_fingerprint ~ '^[0-9a-f]{64}$'",
                name="ck_retrieval_cache_entries_graph_fp",
            ),
            sa.CheckConstraint(
                "user_visible_scope ~ '^[0-9a-f]{64}$'",
                name="ck_retrieval_cache_entries_scope_hash",
            ),
        )
        op.create_index(
            "ix_retrieval_cache_entries_expires",
            "retrieval_cache_entries",
            ["expires_at"],
        )
        op.create_index(
            "ix_retrieval_cache_entries_namespace_strategy",
            "retrieval_cache_entries",
            ["cache_namespace", "strategy_type", "created_at"],
        )
    _seed_retrieval_cache_corpus_marker()


def downgrade() -> None:
    _delete_retrieval_cache_corpus_marker()
    if _has_table("retrieval_cache_entries"):
        op.drop_index(
            "ix_retrieval_cache_entries_namespace_strategy",
            table_name="retrieval_cache_entries",
        )
        op.drop_index(
            "ix_retrieval_cache_entries_expires",
            table_name="retrieval_cache_entries",
        )
        op.drop_table("retrieval_cache_entries")
    if _has_column("retrieval_runs", "cache_summary_json"):
        op.drop_column("retrieval_runs", "cache_summary_json")


def _seed_retrieval_cache_corpus_marker() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO system_settings (setting_key, setting_value, description)
            VALUES (:setting_key, CAST(:setting_value AS jsonb), :description)
            ON CONFLICT (setting_key) DO NOTHING
            """
        ),
        {
            "setting_key": _CORPUS_MARKER_SETTING_KEY,
            "setting_value": json.dumps({"version": 1, "updated_at": "migration"}),
            "description": "Bumped when retrieval-visible active document corpus state changes.",
        },
    )


def _delete_retrieval_cache_corpus_marker() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM system_settings WHERE setting_key = :setting_key"),
        {"setting_key": _CORPUS_MARKER_SETTING_KEY},
    )
