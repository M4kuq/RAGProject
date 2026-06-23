"""persist evaluation generation usage

Revision ID: 0018_evaluation_generation_usage
Revises: 0017_retrieval_cache_foundation
Create Date: 2026-06-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018_evaluation_generation_usage"
down_revision = "0017_retrieval_cache_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evaluation_run_items",
        sa.Column("generation_provider", sa.String(length=50)),
    )
    op.add_column(
        "evaluation_run_items",
        sa.Column("generation_model", sa.String(length=128)),
    )
    op.add_column("evaluation_run_items", sa.Column("input_tokens", sa.Integer()))
    op.add_column("evaluation_run_items", sa.Column("output_tokens", sa.Integer()))
    op.add_column("evaluation_run_items", sa.Column("total_tokens", sa.Integer()))
    op.add_column(
        "evaluation_run_items",
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6)),
    )
    op.add_column("evaluation_run_items", sa.Column("generation_latency_ms", sa.Integer()))
    op.create_check_constraint(
        "ck_evaluation_run_items_generation_non_negative",
        "evaluation_run_items",
        "(input_tokens IS NULL OR input_tokens >= 0) "
        "AND (output_tokens IS NULL OR output_tokens >= 0) "
        "AND (total_tokens IS NULL OR total_tokens >= 0) "
        "AND (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0) "
        "AND (generation_latency_ms IS NULL OR generation_latency_ms >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_evaluation_run_items_generation_non_negative",
        "evaluation_run_items",
        type_="check",
    )
    op.drop_column("evaluation_run_items", "generation_latency_ms")
    op.drop_column("evaluation_run_items", "estimated_cost_usd")
    op.drop_column("evaluation_run_items", "total_tokens")
    op.drop_column("evaluation_run_items", "output_tokens")
    op.drop_column("evaluation_run_items", "input_tokens")
    op.drop_column("evaluation_run_items", "generation_model")
    op.drop_column("evaluation_run_items", "generation_provider")
