"""add tool result compression trace

Revision ID: 0011_tool_result_compression
Revises: 0010_context_compression
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011_tool_result_compression"
down_revision = "0010_context_compression"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "retrieval_runs",
        sa.Column("tool_result_compression_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("retrieval_runs", "tool_result_compression_json")
