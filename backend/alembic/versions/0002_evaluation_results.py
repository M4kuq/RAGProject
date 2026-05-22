"""add evaluation results

Revision ID: 0002_evaluation_results
Revises: 0001_initial
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_evaluation_results"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_results",
        sa.Column("evaluation_result_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "evaluation_run_item_id",
            sa.BigInteger(),
            sa.ForeignKey("evaluation_run_items.evaluation_run_item_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("metric_score", sa.Numeric(10, 6)),
        sa.Column("metric_label", sa.String(100)),
        sa.Column("details_json", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "evaluation_run_item_id",
            "metric_name",
            name="uq_evaluation_results_item_metric",
        ),
        sa.CheckConstraint(
            "btrim(metric_name) <> ''",
            name="ck_evaluation_results_metric_name",
        ),
        sa.CheckConstraint(
            "metric_score IS NULL OR (metric_score >= 0 AND metric_score <= 1)",
            name="ck_evaluation_results_score",
        ),
    )
    op.create_index("ix_evaluation_results_item", "evaluation_results", ["evaluation_run_item_id"])
    op.create_index(
        "ix_evaluation_results_metric_score",
        "evaluation_results",
        ["metric_name", "metric_score"],
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_results_metric_score", table_name="evaluation_results")
    op.drop_index("ix_evaluation_results_item", table_name="evaluation_results")
    op.drop_table("evaluation_results")
