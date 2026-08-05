"""add shared RAG admission and aggregate denial controls

Revision ID: 0023_rag_abuse_controls
Revises: 0022_eval_reliability
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023_rag_abuse_controls"
down_revision = "0022_eval_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rag_request_admissions",
        sa.Column(
            "admission_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("strategy_type", sa.String(50), nullable=False),
        sa.Column("charged_units", sa.Integer(), nullable=False),
        sa.Column(
            "admitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column(
            "outcome",
            sa.String(20),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "subject_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rag_request_admissions_subject_hash",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rag_request_admissions_request_hash",
        ),
        sa.CheckConstraint(
            "charged_units > 0",
            name="ck_rag_request_admissions_charged_units",
        ),
        sa.CheckConstraint(
            "lease_expires_at > admitted_at",
            name="ck_rag_request_admissions_lease_expiry",
        ),
        sa.CheckConstraint(
            "(outcome = 'running' AND released_at IS NULL) OR "
            "(outcome IN ('succeeded', 'failed', 'replayed') AND released_at IS NOT NULL)",
            name="ck_rag_request_admissions_outcome",
        ),
    )
    op.create_index(
        "ix_rag_request_admissions_subject_admitted",
        "rag_request_admissions",
        ["subject_hash", sa.text("admitted_at DESC")],
    )
    op.create_index(
        "ix_rag_request_admissions_admitted",
        "rag_request_admissions",
        [sa.text("admitted_at DESC")],
    )
    op.create_index(
        "ix_rag_request_admissions_active_lease",
        "rag_request_admissions",
        ["lease_expires_at"],
        postgresql_where=sa.text("released_at IS NULL"),
    )

    op.create_table(
        "rag_abuse_denial_buckets",
        sa.Column(
            "denial_bucket_id",
            sa.BigInteger(),
            sa.Identity(),
            primary_key=True,
        ),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column(
            "denied_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "scope",
            "subject_hash",
            "window_started_at",
            "reason_code",
            name="uq_rag_abuse_denial_bucket",
        ),
        sa.CheckConstraint(
            "scope IN ('user', 'global', 'service')",
            name="ck_rag_abuse_denial_buckets_scope",
        ),
        sa.CheckConstraint(
            "reason_code IN ("
            "'rag_user_rate_limited', "
            "'rag_capacity_rate_limited', "
            "'rag_user_concurrency_limited', "
            "'rag_capacity_concurrency_limited', "
            "'rag_user_daily_budget_exhausted', "
            "'rag_capacity_daily_budget_exhausted'"
            ")",
            name="ck_rag_abuse_denial_buckets_reason",
        ),
        sa.CheckConstraint(
            "subject_hash ~ '^[0-9a-f]{64}$'",
            name="ck_rag_abuse_denial_buckets_subject_hash",
        ),
        sa.CheckConstraint(
            "denied_count > 0",
            name="ck_rag_abuse_denial_buckets_count",
        ),
    )
    op.create_index(
        "ix_rag_abuse_denial_buckets_window",
        "rag_abuse_denial_buckets",
        [sa.text("window_started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rag_abuse_denial_buckets_window",
        table_name="rag_abuse_denial_buckets",
    )
    op.drop_table("rag_abuse_denial_buckets")
    op.drop_index(
        "ix_rag_request_admissions_active_lease",
        table_name="rag_request_admissions",
    )
    op.drop_index(
        "ix_rag_request_admissions_admitted",
        table_name="rag_request_admissions",
    )
    op.drop_index(
        "ix_rag_request_admissions_subject_admitted",
        table_name="rag_request_admissions",
    )
    op.drop_table("rag_request_admissions")
