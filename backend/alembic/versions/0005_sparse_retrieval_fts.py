"""add sparse retrieval full text index

Revision ID: 0005_sparse_retrieval_fts
Revises: 0004_eval_dataset_metrics
Create Date: 2026-05-26
"""

from __future__ import annotations

from alembic import op

revision = "0005_sparse_retrieval_fts"
down_revision = "0004_eval_dataset_metrics"
branch_labels = None
depends_on = None


def _create_index_sql(index_name: str, language: str) -> str:
    return " ".join(
        [
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS",
            index_name,
            "ON document_chunks",
            "USING GIN",
            f"(to_tsvector('{language}', content_text))",
        ]
    )


def _drop_index_sql(index_name: str) -> str:
    return " ".join(["DROP INDEX CONCURRENTLY IF EXISTS", index_name])


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_create_index_sql("ix_document_chunks_content_fts", "simple"))
        op.execute(
            _create_index_sql("ix_document_chunks_content_fts_english", "english")
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_drop_index_sql("ix_document_chunks_content_fts_english"))
        op.execute(_drop_index_sql("ix_document_chunks_content_fts"))
