"""add safe evaluation human calibration records

Revision ID: 0021_eval_human_calibration
Revises: 0020_graph_llm_extractor
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021_eval_human_calibration"
down_revision = "0020_graph_llm_extractor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_human_calibrations",
        sa.Column(
            "evaluation_human_calibration_id",
            sa.BigInteger(),
            primary_key=True,
        ),
        sa.Column(
            "evaluation_run_item_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "evaluation_run_items.evaluation_run_item_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("case_id", sa.String(120), nullable=False),
        sa.Column("rubric_version", sa.String(64), nullable=False),
        sa.Column("required_facts_supported", sa.String(20), nullable=False),
        sa.Column("citation_support", sa.String(20), nullable=False),
        sa.Column("forbidden_claims_absent", sa.String(20), nullable=False),
        sa.Column("abstention_correct", sa.String(20), nullable=False),
        sa.Column("prompt_injection_resisted", sa.String(20), nullable=False),
        sa.Column("auxiliary_confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("auxiliary_reason_codes_json", postgresql.JSONB(), nullable=False),
        sa.Column("auxiliary_pass", sa.Boolean(), nullable=False),
        sa.Column("human_pass", sa.Boolean(), nullable=False),
        sa.Column("disagreement_category", sa.String(40)),
        sa.Column("human_reason_codes_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "reviewed_by",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "evaluation_run_item_id",
            name="uq_eval_human_calibrations_run_item",
        ),
        sa.CheckConstraint(
            "btrim(case_id) <> ''",
            name="ck_eval_human_calibrations_case_id",
        ),
        sa.CheckConstraint(
            "rubric_version = 'phase3.grounded_answer_judge.v1'",
            name="ck_eval_human_calibrations_rubric",
        ),
        sa.CheckConstraint(
            "required_facts_supported IN ('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND citation_support IN ('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND forbidden_claims_absent IN ('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND abstention_correct IN ('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND prompt_injection_resisted IN "
            "('pass', 'fail', 'uncertain', 'not_applicable')",
            name="ck_eval_human_calibrations_outcomes",
        ),
        sa.CheckConstraint(
            "auxiliary_confidence >= 0 AND auxiliary_confidence <= 1",
            name="ck_eval_human_calibrations_confidence",
        ),
        sa.CheckConstraint(
            "disagreement_category IS NULL OR disagreement_category IN "
            "('auxiliary_false_positive', 'auxiliary_false_negative', "
            "'rubric_ambiguity', 'gold_case_defect')",
            name="ck_eval_human_calibrations_disagreement",
        ),
        sa.CheckConstraint(
            "(auxiliary_pass = human_pass AND disagreement_category IS NULL) OR "
            "(auxiliary_pass <> human_pass AND disagreement_category IS NOT NULL)",
            name="ck_eval_human_calibrations_verdict_consistency",
        ),
    )
    op.create_index(
        "ix_eval_human_calibrations_reviewer",
        "evaluation_human_calibrations",
        ["reviewed_by"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_eval_human_calibrations_reviewer",
        table_name="evaluation_human_calibrations",
    )
    op.drop_table("evaluation_human_calibrations")
