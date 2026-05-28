"""add document chunk metadata

Revision ID: 0006_document_chunk_metadata
Revises: 0005_sparse_retrieval_fts
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_document_chunk_metadata"
down_revision = "0005_sparse_retrieval_fts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("document_chunks", "metadata_json")
