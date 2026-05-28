"""add document version metadata

Revision ID: 0007_document_version_metadata
Revises: 0006_document_chunk_metadata
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_document_version_metadata"
down_revision = "0006_document_chunk_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column(
            "metadata_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "metadata_json")
