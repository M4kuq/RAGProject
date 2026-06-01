"""add context budget trace

Revision ID: 0009_context_budget
Revises: 0008_llm_tool_orch
Create Date: 2026-06-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009_context_budget"
down_revision = "0008_llm_tool_orch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "retrieval_runs",
        sa.Column("context_budget_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("retrieval_runs", "context_budget_json")
