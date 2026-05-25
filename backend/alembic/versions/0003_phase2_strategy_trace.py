"""add phase2 strategy trace schema

Revision ID: 0003_phase2_strategy_trace
Revises: 0002_evaluation_results
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_phase2_strategy_trace"
down_revision = "0002_evaluation_results"
branch_labels = None
depends_on = None

RETRIEVAL_STRATEGY_VALUES = (
    "dense",
    "sparse",
    "hybrid",
    "multi_query_dense",
    "multi_query_hybrid",
    "metadata_filtered",
    "version_aware",
    "agentic_router",
    "fallback_dense",
)
RETRIEVAL_SOURCE_VALUES = (
    "dense",
    "sparse",
    "hybrid",
    "rerank",
    "fallback_dense",
    "metadata_filter",
)


def upgrade() -> None:
    op.add_column(
        "retrieval_runs",
        sa.Column(
            "strategy_type",
            sa.String(length=50),
            server_default=sa.text("'dense'"),
            nullable=False,
        ),
    )
    op.add_column("retrieval_runs", sa.Column("query_plan_json", postgresql.JSONB()))
    op.add_column("retrieval_runs", sa.Column("strategy_decision_json", postgresql.JSONB()))
    op.add_column("retrieval_runs", sa.Column("latency_breakdown_json", postgresql.JSONB()))
    op.add_column("retrieval_runs", sa.Column("retrieval_settings_json", postgresql.JSONB()))
    op.create_check_constraint(
        "ck_retrieval_runs_strategy_type",
        "retrieval_runs",
        f"strategy_type IN ({_sql_literal_list(RETRIEVAL_STRATEGY_VALUES)})",
    )

    op.add_column("retrieval_run_items", sa.Column("retrieval_source", sa.String(length=50)))
    op.add_column("retrieval_run_items", sa.Column("score_breakdown_json", postgresql.JSONB()))
    op.create_check_constraint(
        "ck_retrieval_run_items_source",
        "retrieval_run_items",
        f"retrieval_source IS NULL OR "
        f"retrieval_source IN ({_sql_literal_list(RETRIEVAL_SOURCE_VALUES)})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_retrieval_run_items_source", "retrieval_run_items", type_="check")
    op.drop_column("retrieval_run_items", "score_breakdown_json")
    op.drop_column("retrieval_run_items", "retrieval_source")

    op.drop_constraint("ck_retrieval_runs_strategy_type", "retrieval_runs", type_="check")
    op.drop_column("retrieval_runs", "retrieval_settings_json")
    op.drop_column("retrieval_runs", "latency_breakdown_json")
    op.drop_column("retrieval_runs", "strategy_decision_json")
    op.drop_column("retrieval_runs", "query_plan_json")
    op.drop_column("retrieval_runs", "strategy_type")


def _sql_literal_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
