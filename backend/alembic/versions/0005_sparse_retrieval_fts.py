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

_SIMPLE_FTS_INDEX_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_document_chunks_content_fts "
    "ON document_chunks USING GIN (to_tsvector('simple', content_text))"
)
_ENGLISH_FTS_INDEX_SQL = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
    "ix_document_chunks_content_fts_english ON document_chunks "
    "USING GIN (to_tsvector('english', content_text))"
)
_DROP_ENGLISH_FTS_INDEX_SQL = (
    "DROP INDEX CONCURRENTLY IF EXISTS "
    "ix_document_chunks_content_fts_english"
)
_DROP_SIMPLE_FTS_INDEX_SQL = (
    "DROP INDEX CONCURRENTLY IF EXISTS "
    "ix_document_chunks_content_fts"
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_SIMPLE_FTS_INDEX_SQL)
        op.execute(_ENGLISH_FTS_INDEX_SQL)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(_DROP_ENGLISH_FTS_INDEX_SQL)
        op.execute(_DROP_SIMPLE_FTS_INDEX_SQL)
