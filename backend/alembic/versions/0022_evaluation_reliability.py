"""add evaluation corpus readiness and local judge records

Revision ID: 0022_eval_reliability
Revises: 0021_eval_human_calibration
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022_eval_reliability"
down_revision = "0021_eval_human_calibration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_evaluation_datasets_name",
        "evaluation_datasets",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_evaluation_datasets_name_version",
        "evaluation_datasets",
        ["dataset_name", "version"],
    )
    op.add_column(
        "evaluation_datasets",
        sa.Column(
            "manifest_schema_version",
            sa.String(64),
            server_default=sa.text("'phase2.evaluation_dataset.v1'"),
            nullable=False,
        ),
    )
    op.add_column(
        "evaluation_datasets",
        sa.Column("content_fingerprint", sa.String(64)),
    )
    op.add_column(
        "evaluation_datasets",
        sa.Column("corpus_fingerprint", sa.String(64)),
    )
    op.add_column(
        "evaluation_datasets",
        sa.Column(
            "corpus_mode",
            sa.String(30),
            server_default=sa.text("'shared_legacy'"),
            nullable=False,
        ),
    )
    op.add_column(
        "evaluation_datasets",
        sa.Column(
            "corpus_status",
            sa.String(30),
            server_default=sa.text("'shared_legacy'"),
            nullable=False,
        ),
    )
    op.add_column(
        "evaluation_datasets",
        sa.Column("corpus_failure_code", sa.String(100)),
    )
    op.add_column(
        "evaluation_datasets",
        sa.Column("corpus_prepared_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "evaluation_datasets",
        sa.Column("readiness_checked_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_evaluation_datasets_corpus_mode",
        "evaluation_datasets",
        "corpus_mode IN ('shared_legacy', 'isolated')",
    )
    op.create_check_constraint(
        "ck_evaluation_datasets_corpus_status",
        "evaluation_datasets",
        "corpus_status IN ('shared_legacy', 'not_prepared', 'preparing', 'ready', 'failed')",
    )
    op.create_check_constraint(
        "ck_evaluation_datasets_content_fingerprint",
        "evaluation_datasets",
        "content_fingerprint IS NULL OR content_fingerprint ~ '^[0-9a-f]{64}$'",
    )

    op.add_column(
        "evaluation_runs",
        sa.Column("corpus_fingerprint", sa.String(64)),
    )
    op.add_column(
        "evaluation_run_items",
        sa.Column("answer_outcome", sa.String(30)),
    )
    op.add_column(
        "evaluation_run_items",
        sa.Column("error_detail_code", sa.String(100)),
    )
    op.create_check_constraint(
        "ck_evaluation_run_items_answer_outcome",
        "evaluation_run_items",
        "answer_outcome IS NULL OR answer_outcome IN "
        "('answered', 'abstained', 'no_context', 'citation_error', "
        "'generation_error', 'retrieval_error')",
    )

    for column_name in (
        "human_required_facts_supported",
        "human_citation_support",
        "human_forbidden_claims_absent",
        "human_abstention_correct",
        "human_prompt_injection_resisted",
    ):
        op.add_column(
            "evaluation_human_calibrations",
            sa.Column(column_name, sa.String(20)),
        )
    op.create_check_constraint(
        "ck_eval_human_calibrations_human_outcomes",
        "evaluation_human_calibrations",
        "(human_required_facts_supported IS NULL "
        "AND human_citation_support IS NULL "
        "AND human_forbidden_claims_absent IS NULL "
        "AND human_abstention_correct IS NULL "
        "AND human_prompt_injection_resisted IS NULL) OR "
        "(human_required_facts_supported IN "
        "('pass', 'fail', 'uncertain', 'not_applicable') "
        "AND human_citation_support IN "
        "('pass', 'fail', 'uncertain', 'not_applicable') "
        "AND human_forbidden_claims_absent IN "
        "('pass', 'fail', 'uncertain', 'not_applicable') "
        "AND human_abstention_correct IN "
        "('pass', 'fail', 'uncertain', 'not_applicable') "
        "AND human_prompt_injection_resisted IN "
        "('pass', 'fail', 'uncertain', 'not_applicable'))",
    )

    op.create_table(
        "evaluation_corpus_sources",
        sa.Column(
            "evaluation_corpus_source_id",
            sa.BigInteger(),
            primary_key=True,
        ),
        sa.Column(
            "evaluation_dataset_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "evaluation_datasets.evaluation_dataset_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("source_key", sa.String(120), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("facts_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "logical_document_id",
            sa.BigInteger(),
            sa.ForeignKey("logical_documents.logical_document_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "document_version_id",
            sa.BigInteger(),
            sa.ForeignKey("document_versions.document_version_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "ingest_job_id",
            sa.BigInteger(),
            sa.ForeignKey("jobs.job_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "status",
            sa.String(30),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(100)),
        sa.Column("prepared_at", sa.DateTime(timezone=True)),
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
            "source_key",
            name="uq_evaluation_corpus_sources_dataset_key",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'preparing', 'ready', 'failed')",
            name="ck_evaluation_corpus_sources_status",
        ),
        sa.CheckConstraint(
            "btrim(source_key) <> ''",
            name="ck_evaluation_corpus_sources_key",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_corpus_sources_hash",
        ),
    )
    op.create_index(
        "ix_evaluation_corpus_sources_dataset_status",
        "evaluation_corpus_sources",
        ["evaluation_dataset_id", "status"],
    )

    op.create_table(
        "evaluation_auxiliary_judgments",
        sa.Column(
            "evaluation_auxiliary_judgment_id",
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
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("rubric_version", sa.String(64), nullable=False),
        sa.Column("judge_provider", sa.String(50), nullable=False),
        sa.Column("judge_model", sa.String(128), nullable=False),
        sa.Column("required_facts_supported", sa.String(20)),
        sa.Column("citation_support", sa.String(20)),
        sa.Column("forbidden_claims_absent", sa.String(20)),
        sa.Column("abstention_correct", sa.String(20)),
        sa.Column("prompt_injection_resisted", sa.String(20)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("reason_codes_json", postgresql.JSONB(), nullable=False),
        sa.Column("auxiliary_pass", sa.Boolean()),
        sa.Column("claim_faithfulness", sa.Numeric(10, 6)),
        sa.Column("failure_code", sa.String(100)),
        sa.Column("answer_hash", sa.String(64)),
        sa.Column("context_hash", sa.String(64)),
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
            name="uq_eval_auxiliary_judgments_run_item",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_eval_auxiliary_judgments_status",
        ),
        sa.CheckConstraint(
            "status = 'failed' OR "
            "(required_facts_supported IS NOT NULL "
            "AND citation_support IS NOT NULL "
            "AND forbidden_claims_absent IS NOT NULL "
            "AND abstention_correct IS NOT NULL "
            "AND prompt_injection_resisted IS NOT NULL "
            "AND confidence IS NOT NULL AND auxiliary_pass IS NOT NULL)",
            name="ck_eval_auxiliary_judgments_succeeded_shape",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_eval_auxiliary_judgments_confidence",
        ),
        sa.CheckConstraint(
            "claim_faithfulness IS NULL OR (claim_faithfulness >= 0 AND claim_faithfulness <= 1)",
            name="ck_eval_auxiliary_judgments_claim_faithfulness",
        ),
    )

    op.create_table(
        "evaluation_review_payloads",
        sa.Column(
            "evaluation_review_payload_id",
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
        sa.Column("answer_text", sa.Text()),
        sa.Column("context_json", postgresql.JSONB()),
        sa.Column("citations_json", postgresql.JSONB()),
        sa.Column("required_facts_json", postgresql.JSONB()),
        sa.Column("answer_hash", sa.String(64), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
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
            name="uq_evaluation_review_payloads_run_item",
        ),
        sa.CheckConstraint(
            "(purged_at IS NULL) OR "
            "(answer_text IS NULL AND context_json IS NULL "
            "AND citations_json IS NULL AND required_facts_json IS NULL)",
            name="ck_evaluation_review_payloads_purged",
        ),
    )
    op.create_index(
        "ix_evaluation_review_payloads_expires_at",
        "evaluation_review_payloads",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evaluation_review_payloads_expires_at",
        table_name="evaluation_review_payloads",
    )
    op.drop_table("evaluation_review_payloads")
    op.drop_table("evaluation_auxiliary_judgments")
    op.drop_index(
        "ix_evaluation_corpus_sources_dataset_status",
        table_name="evaluation_corpus_sources",
    )
    op.drop_table("evaluation_corpus_sources")

    op.drop_constraint(
        "ck_evaluation_run_items_answer_outcome",
        "evaluation_run_items",
        type_="check",
    )
    op.drop_column("evaluation_run_items", "error_detail_code")
    op.drop_column("evaluation_run_items", "answer_outcome")
    op.drop_column("evaluation_runs", "corpus_fingerprint")

    op.drop_constraint(
        "ck_eval_human_calibrations_human_outcomes",
        "evaluation_human_calibrations",
        type_="check",
    )
    for column_name in (
        "human_prompt_injection_resisted",
        "human_abstention_correct",
        "human_forbidden_claims_absent",
        "human_citation_support",
        "human_required_facts_supported",
    ):
        op.drop_column("evaluation_human_calibrations", column_name)

    op.drop_constraint(
        "ck_evaluation_datasets_content_fingerprint",
        "evaluation_datasets",
        type_="check",
    )
    op.drop_constraint(
        "ck_evaluation_datasets_corpus_status",
        "evaluation_datasets",
        type_="check",
    )
    op.drop_constraint(
        "ck_evaluation_datasets_corpus_mode",
        "evaluation_datasets",
        type_="check",
    )
    op.drop_column("evaluation_datasets", "readiness_checked_at")
    op.drop_column("evaluation_datasets", "corpus_prepared_at")
    op.drop_column("evaluation_datasets", "corpus_failure_code")
    op.drop_column("evaluation_datasets", "corpus_status")
    op.drop_column("evaluation_datasets", "corpus_mode")
    op.drop_column("evaluation_datasets", "corpus_fingerprint")
    op.drop_column("evaluation_datasets", "content_fingerprint")
    op.drop_column("evaluation_datasets", "manifest_schema_version")
    op.drop_constraint(
        "uq_evaluation_datasets_name_version",
        "evaluation_datasets",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_evaluation_datasets_name",
        "evaluation_datasets",
        ["dataset_name"],
    )
