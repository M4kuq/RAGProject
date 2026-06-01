"""add context compression trace

Revision ID: 0010_context_compression
Revises: 0009_context_budget
Create Date: 2026-06-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_context_compression"
down_revision = "0009_context_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "retrieval_runs",
        sa.Column("context_compression_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("retrieval_runs", "context_compression_json")
