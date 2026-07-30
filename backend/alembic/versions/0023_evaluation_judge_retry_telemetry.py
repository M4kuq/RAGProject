"""add evaluation judge retry telemetry

Revision ID: 0023_eval_judge_retry
Revises: 0022_eval_reliability
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023_eval_judge_retry"
down_revision = "0022_eval_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evaluation_auxiliary_judgments",
        sa.Column("attempt_count", sa.Integer()),
    )
    op.add_column(
        "evaluation_auxiliary_judgments",
        sa.Column("first_failure_code", sa.String(100)),
    )
    op.add_column(
        "evaluation_auxiliary_judgments",
        sa.Column("terminal_reason_code", sa.String(100)),
    )
    op.add_column(
        "evaluation_auxiliary_judgments",
        sa.Column("recovered_after_retry", sa.Boolean()),
    )
    op.create_check_constraint(
        "ck_eval_auxiliary_judgments_attempt_telemetry",
        "evaluation_auxiliary_judgments",
        "(attempt_count IS NULL "
        "AND first_failure_code IS NULL "
        "AND terminal_reason_code IS NULL "
        "AND recovered_after_retry IS NULL) OR "
        "(attempt_count BETWEEN 0 AND 2 "
        "AND terminal_reason_code IS NOT NULL "
        "AND recovered_after_retry IS NOT NULL "
        "AND ((attempt_count = 0 "
        "AND first_failure_code IS NOT NULL "
        "AND recovered_after_retry = FALSE) "
        "OR (attempt_count = 1 "
        "AND first_failure_code IS NULL "
        "AND recovered_after_retry = FALSE) "
        "OR (attempt_count = 2 AND first_failure_code IS NOT NULL)))",
    )
    op.create_check_constraint(
        "ck_eval_auxiliary_judgments_retry_recovery",
        "evaluation_auxiliary_judgments",
        "recovered_after_retry IS NULL OR recovered_after_retry = FALSE OR status = 'succeeded'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_eval_auxiliary_judgments_retry_recovery",
        "evaluation_auxiliary_judgments",
        type_="check",
    )
    op.drop_constraint(
        "ck_eval_auxiliary_judgments_attempt_telemetry",
        "evaluation_auxiliary_judgments",
        type_="check",
    )
    op.drop_column("evaluation_auxiliary_judgments", "recovered_after_retry")
    op.drop_column("evaluation_auxiliary_judgments", "terminal_reason_code")
    op.drop_column("evaluation_auxiliary_judgments", "first_failure_code")
    op.drop_column("evaluation_auxiliary_judgments", "attempt_count")
