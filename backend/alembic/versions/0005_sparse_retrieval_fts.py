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


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_chunks_content_fts
        ON document_chunks
        USING GIN (to_tsvector('simple', content_text))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_content_fts")
