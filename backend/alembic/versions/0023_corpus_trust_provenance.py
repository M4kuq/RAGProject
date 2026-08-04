"""add corpus source trust and security review state

Revision ID: 0023_corpus_trust
Revises: 0022_eval_reliability
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023_corpus_trust"
down_revision = "0022_eval_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column(
            "source_provenance",
            sa.String(30),
            server_default=sa.text("'legacy'"),
            nullable=False,
        ),
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "source_trust_level",
            sa.String(30),
            server_default=sa.text("'trusted'"),
            nullable=False,
        ),
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "security_review_status",
            sa.String(30),
            server_default=sa.text("'approved'"),
            nullable=False,
        ),
    )
    op.add_column(
        "document_versions",
        sa.Column("security_review_reason_code", sa.String(60)),
    )
    op.add_column(
        "document_versions",
        sa.Column("security_reviewed_at", sa.DateTime(timezone=True)),
    )
    op.create_check_constraint(
        "ck_document_versions_source_provenance",
        "document_versions",
        "source_provenance IN ('legacy', 'admin_upload', 'external_url', 'evaluation_fixture')",
    )
    op.create_check_constraint(
        "ck_document_versions_source_trust_level",
        "document_versions",
        "source_trust_level IN ('trusted', 'external_untrusted')",
    )
    op.create_check_constraint(
        "ck_document_versions_security_review_status",
        "document_versions",
        "security_review_status IN ('pending', 'approved', 'quarantined')",
    )
    op.create_check_constraint(
        "ck_document_versions_active_security_approved_only",
        "document_versions",
        "is_active = FALSE OR security_review_status = 'approved'",
    )
    op.create_check_constraint(
        "ck_document_versions_security_review_state",
        "document_versions",
        "(security_review_status = 'pending' AND security_review_reason_code IS NULL "
        "AND security_reviewed_at IS NULL) OR security_review_status = 'approved' OR "
        "(security_review_status = 'quarantined' AND "
        "security_review_reason_code IS NOT NULL AND security_reviewed_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_document_versions_security_review_state",
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_versions_active_security_approved_only",
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_versions_security_review_status",
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_versions_source_trust_level",
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_versions_source_provenance",
        "document_versions",
        type_="check",
    )
    op.drop_column("document_versions", "security_reviewed_at")
    op.drop_column("document_versions", "security_review_reason_code")
    op.drop_column("document_versions", "security_review_status")
    op.drop_column("document_versions", "source_trust_level")
    op.drop_column("document_versions", "source_provenance")
