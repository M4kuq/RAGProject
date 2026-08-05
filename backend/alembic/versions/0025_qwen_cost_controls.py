"""add atomic Qwen token, cost, escalation, and circuit controls

Revision ID: 0025_qwen_cost_controls
Revises: 0024_security_merge
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0025_qwen_cost_controls"
down_revision = "0024_security_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qwen_cost_reservations",
        sa.Column(
            "reservation_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model_id", sa.String(256), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("call_index", sa.Integer(), nullable=False),
        sa.Column("pricing_version", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("input_price_per_million", sa.Numeric(18, 9), nullable=False),
        sa.Column("output_price_per_million", sa.Numeric(18, 9), nullable=False),
        sa.Column("reserved_input_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_output_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_cost", sa.Numeric(18, 9), nullable=False),
        sa.Column("actual_input_tokens", sa.Integer()),
        sa.Column("actual_output_tokens", sa.Integer()),
        sa.Column("actual_cost", sa.Numeric(18, 9)),
        sa.Column("is_escalation", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'reserved'"), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "request_hash",
            "provider",
            "call_index",
            name="uq_qwen_cost_reservations_request_call",
        ),
        sa.CheckConstraint(
            "subject_hash ~ '^[0-9a-f]{64}$'",
            name="ck_qwen_cost_reservations_subject_hash",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_qwen_cost_reservations_request_hash",
        ),
        sa.CheckConstraint("provider = 'qwen'", name="ck_qwen_cost_reservations_provider"),
        sa.CheckConstraint("tier IN ('flash', 'plus')", name="ck_qwen_cost_reservations_tier"),
        sa.CheckConstraint("call_index > 0", name="ck_qwen_cost_reservations_call_index"),
        sa.CheckConstraint(
            "status IN ('reserved', 'finalized', 'failed', 'cancelled')",
            name="ck_qwen_cost_reservations_status",
        ),
        sa.CheckConstraint(
            "reserved_input_tokens >= 0 AND reserved_output_tokens > 0",
            name="ck_qwen_cost_reservations_reserved_tokens",
        ),
        sa.CheckConstraint(
            "reserved_cost >= 0 AND input_price_per_million > 0 AND output_price_per_million > 0",
            name="ck_qwen_cost_reservations_prices",
        ),
        sa.CheckConstraint(
            "lease_expires_at > reserved_at",
            name="ck_qwen_cost_reservations_lease",
        ),
    )
    op.create_index(
        "ix_qwen_cost_reservations_subject_reserved",
        "qwen_cost_reservations",
        ["subject_hash", sa.text("reserved_at DESC")],
    )
    op.create_index(
        "ix_qwen_cost_reservations_reserved",
        "qwen_cost_reservations",
        [sa.text("reserved_at DESC")],
    )
    op.create_index(
        "ix_qwen_cost_reservations_active_lease",
        "qwen_cost_reservations",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'reserved'"),
    )

    op.create_table(
        "qwen_circuit_breakers",
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("state", sa.String(20), server_default=sa.text("'closed'"), nullable=False),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("opened_until", sa.DateTime(timezone=True)),
        sa.Column("probe_lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_reason_code", sa.String(100)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("provider = 'qwen'", name="ck_qwen_circuit_breakers_provider"),
        sa.CheckConstraint(
            "state IN ('closed', 'open', 'half_open')",
            name="ck_qwen_circuit_breakers_state",
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_qwen_circuit_breakers_failure_count",
        ),
    )


def downgrade() -> None:
    op.drop_table("qwen_circuit_breakers")
    op.drop_index(
        "ix_qwen_cost_reservations_active_lease",
        table_name="qwen_cost_reservations",
    )
    op.drop_index(
        "ix_qwen_cost_reservations_reserved",
        table_name="qwen_cost_reservations",
    )
    op.drop_index(
        "ix_qwen_cost_reservations_subject_reserved",
        table_name="qwen_cost_reservations",
    )
    op.drop_table("qwen_cost_reservations")
