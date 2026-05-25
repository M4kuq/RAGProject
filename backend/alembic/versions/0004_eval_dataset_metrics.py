"""add evaluation dataset and strategy metrics schema

Revision ID: 0004_eval_dataset_metrics
Revises: 0003_phase2_strategy_trace
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_eval_dataset_metrics"
down_revision = "0003_phase2_strategy_trace"
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

EVALUATION_TRIGGER_VALUES = (
    "manual",
    "ci",
    "scheduled",
    "post_deploy",
    "online_sampled_trace",
)


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        sa.Column("evaluation_dataset_id", sa.BigInteger(), primary_key=True),
        sa.Column("dataset_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("version", sa.String(length=50), server_default=sa.text("'v1'"), nullable=False),
        sa.Column(
            "source_type",
            sa.String(length=50),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("metadata_json", postgresql.JSONB()),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
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
        sa.UniqueConstraint("dataset_name", name="uq_evaluation_datasets_name"),
        sa.CheckConstraint(
            "source_type IN ('manual', 'fixture', 'feedback_promoted', 'imported')",
            name="ck_evaluation_datasets_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_evaluation_datasets_status",
        ),
        sa.CheckConstraint(
            "btrim(dataset_name) <> ''",
            name="ck_evaluation_datasets_name_not_empty",
        ),
        sa.CheckConstraint(
            "btrim(version) <> ''",
            name="ck_evaluation_datasets_version_not_empty",
        ),
    )
    op.create_index(
        "ix_evaluation_datasets_status_created",
        "evaluation_datasets",
        ["status", "created_at"],
    )

    op.create_table(
        "evaluation_cases",
        sa.Column("evaluation_case_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "evaluation_dataset_id",
            sa.BigInteger(),
            sa.ForeignKey("evaluation_datasets.evaluation_dataset_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("case_key", sa.String(length=120), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text()),
        sa.Column("expected_keywords", postgresql.JSONB()),
        sa.Column("expected_document_ids", postgresql.JSONB()),
        sa.Column("expected_chunk_ids", postgresql.JSONB()),
        sa.Column(
            "required_citation",
            sa.Boolean(),
            server_default=sa.text("TRUE"),
            nullable=False,
        ),
        sa.Column("tags", postgresql.JSONB()),
        sa.Column("metadata_json", postgresql.JSONB()),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'active'"),
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
            "evaluation_dataset_id",
            "case_key",
            name="uq_evaluation_cases_dataset_key",
        ),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_evaluation_cases_status"),
        sa.CheckConstraint("btrim(case_key) <> ''", name="ck_evaluation_cases_key_not_empty"),
        sa.CheckConstraint(
            "btrim(question) <> ''",
            name="ck_evaluation_cases_question_not_empty",
        ),
    )
    op.create_index(
        "ix_evaluation_cases_dataset_status",
        "evaluation_cases",
        ["evaluation_dataset_id", "status"],
    )

    op.add_column("evaluation_runs", sa.Column("evaluation_dataset_id", sa.BigInteger()))
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "strategy_type",
            sa.String(length=50),
            server_default=sa.text("'dense'"),
            nullable=False,
        ),
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "trigger_type",
            sa.String(length=50),
            server_default=sa.text("'manual'"),
            nullable=False,
        ),
    )
    op.add_column("evaluation_runs", sa.Column("retrieval_settings_json", postgresql.JSONB()))
    op.add_column("evaluation_runs", sa.Column("strategy_metrics_summary_json", postgresql.JSONB()))
    op.create_foreign_key(
        "fk_evaluation_runs_dataset",
        "evaluation_runs",
        "evaluation_datasets",
        ["evaluation_dataset_id"],
        ["evaluation_dataset_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_evaluation_runs_strategy_type",
        "evaluation_runs",
        f"strategy_type IN ({_sql_literal_list(RETRIEVAL_STRATEGY_VALUES)})",
    )
    op.create_check_constraint(
        "ck_evaluation_runs_trigger_type",
        "evaluation_runs",
        f"trigger_type IN ({_sql_literal_list(EVALUATION_TRIGGER_VALUES)})",
    )
    op.create_index(
        "ix_evaluation_runs_dataset_strategy",
        "evaluation_runs",
        ["evaluation_dataset_id", "strategy_type", "created_at"],
    )

    op.add_column("evaluation_run_items", sa.Column("evaluation_case_id", sa.BigInteger()))
    op.add_column(
        "evaluation_run_items",
        sa.Column(
            "strategy_type",
            sa.String(length=50),
            server_default=sa.text("'dense'"),
            nullable=False,
        ),
    )
    op.add_column("evaluation_run_items", sa.Column("case_key", sa.String(length=120)))
    op.add_column("evaluation_run_items", sa.Column("latency_breakdown_json", postgresql.JSONB()))
    op.add_column("evaluation_run_items", sa.Column("metric_summary_json", postgresql.JSONB()))
    op.create_foreign_key(
        "fk_evaluation_run_items_case",
        "evaluation_run_items",
        "evaluation_cases",
        ["evaluation_case_id"],
        ["evaluation_case_id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_evaluation_run_items_strategy_type",
        "evaluation_run_items",
        f"strategy_type IN ({_sql_literal_list(RETRIEVAL_STRATEGY_VALUES)})",
    )
    op.create_index(
        "ix_evaluation_run_items_case",
        "evaluation_run_items",
        ["evaluation_case_id"],
    )

    op.add_column("evaluation_results", sa.Column("metric_value", sa.Numeric(12, 6)))
    op.add_column("evaluation_results", sa.Column("metric_detail_json", postgresql.JSONB()))
    op.add_column(
        "evaluation_results",
        sa.Column(
            "strategy_type",
            sa.String(length=50),
            server_default=sa.text("'dense'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_evaluation_results_strategy_type",
        "evaluation_results",
        f"strategy_type IN ({_sql_literal_list(RETRIEVAL_STRATEGY_VALUES)})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_evaluation_results_strategy_type", "evaluation_results", type_="check")
    op.drop_column("evaluation_results", "strategy_type")
    op.drop_column("evaluation_results", "metric_detail_json")
    op.drop_column("evaluation_results", "metric_value")

    op.drop_index("ix_evaluation_run_items_case", table_name="evaluation_run_items")
    op.drop_constraint(
        "ck_evaluation_run_items_strategy_type",
        "evaluation_run_items",
        type_="check",
    )
    op.drop_constraint("fk_evaluation_run_items_case", "evaluation_run_items", type_="foreignkey")
    op.drop_column("evaluation_run_items", "metric_summary_json")
    op.drop_column("evaluation_run_items", "latency_breakdown_json")
    op.drop_column("evaluation_run_items", "case_key")
    op.drop_column("evaluation_run_items", "strategy_type")
    op.drop_column("evaluation_run_items", "evaluation_case_id")

    op.drop_index("ix_evaluation_runs_dataset_strategy", table_name="evaluation_runs")
    op.drop_constraint("ck_evaluation_runs_trigger_type", "evaluation_runs", type_="check")
    op.drop_constraint("ck_evaluation_runs_strategy_type", "evaluation_runs", type_="check")
    op.drop_constraint("fk_evaluation_runs_dataset", "evaluation_runs", type_="foreignkey")
    op.drop_column("evaluation_runs", "strategy_metrics_summary_json")
    op.drop_column("evaluation_runs", "retrieval_settings_json")
    op.drop_column("evaluation_runs", "trigger_type")
    op.drop_column("evaluation_runs", "strategy_type")
    op.drop_column("evaluation_runs", "evaluation_dataset_id")

    op.drop_index("ix_evaluation_cases_dataset_status", table_name="evaluation_cases")
    op.drop_table("evaluation_cases")
    op.drop_index("ix_evaluation_datasets_status_created", table_name="evaluation_datasets")
    op.drop_table("evaluation_datasets")


def _sql_literal_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
