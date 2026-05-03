"""initial phase1 schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("role_id", sa.BigInteger(), primary_key=True),
        sa.Column("role_name", sa.String(50), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("roles.role_id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.Text()),
        sa.Column("status", sa.String(30), server_default="active", nullable=False),
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
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
    )
    op.create_table(
        "user_settings",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ui_theme", sa.String(30), server_default="system", nullable=False),
        sa.Column("memory_message_limit", sa.Integer(), server_default="8", nullable=False),
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
    )
    op.create_table(
        "user_sessions",
        sa.Column("session_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("csrf_state_hash", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "chat_sessions",
        sa.Column("chat_session_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), server_default="active", nullable=False),
        sa.Column("temporary_flag", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("ttl_expires_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
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
    )
    op.create_table(
        "chat_messages",
        sa.Column("chat_message_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_session_id",
            sa.BigInteger(),
            sa.ForeignKey("chat_sessions.chat_session_id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("client_message_id", sa.String(255)),
        sa.Column("linked_retrieval_run_id", sa.BigInteger()),
        sa.Column("edited_flag", sa.Boolean(), server_default=sa.false(), nullable=False),
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
    )
    op.create_table(
        "chat_tags",
        sa.Column("chat_tag_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_session_id",
            sa.BigInteger(),
            sa.ForeignKey("chat_sessions.chat_session_id"),
            nullable=False,
        ),
        sa.Column("tag_name", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("chat_session_id", "tag_name", name="uq_chat_tags_session_name"),
    )
    op.create_table(
        "logical_documents",
        sa.Column("logical_document_id", sa.BigInteger(), primary_key=True),
        sa.Column("owner_user_id", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), server_default="active", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
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
    )
    op.create_table(
        "document_versions",
        sa.Column("document_version_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "logical_document_id",
            sa.BigInteger(),
            sa.ForeignKey("logical_documents.logical_document_id"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), server_default="processing", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.Text()),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
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
            "logical_document_id", "version_no", name="uq_document_versions_version_no"
        ),
        sa.UniqueConstraint(
            "logical_document_id", "content_hash", name="uq_document_versions_content_hash"
        ),
    )
    op.create_table(
        "document_chunks",
        sa.Column("document_chunk_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.BigInteger(),
            sa.ForeignKey("document_versions.document_version_id"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_from", sa.Integer()),
        sa.Column("page_to", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "retrieval_runs",
        sa.Column("retrieval_run_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_session_id", sa.BigInteger(), sa.ForeignKey("chat_sessions.chat_session_id")
        ),
        sa.Column(
            "request_message_id", sa.BigInteger(), sa.ForeignKey("chat_messages.chat_message_id")
        ),
        sa.Column("request_id", sa.String(100)),
        sa.Column("origin_type", sa.String(30), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), server_default="running", nullable=False),
        sa.Column("retrieval_score_summary", sa.JSON()),
        sa.Column("error_code", sa.String(100)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "retrieval_run_items",
        sa.Column("retrieval_run_item_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "retrieval_run_id",
            sa.BigInteger(),
            sa.ForeignKey("retrieval_runs.retrieval_run_id"),
            nullable=False,
        ),
        sa.Column(
            "document_chunk_id",
            sa.BigInteger(),
            sa.ForeignKey("document_chunks.document_chunk_id"),
            nullable=False,
        ),
        sa.Column("retrieval_score", sa.Float(), nullable=False),
        sa.Column("rerank_score", sa.Float()),
        sa.Column("rerank_order", sa.Integer()),
        sa.Column("payload_snapshot", sa.JSON()),
    )
    op.create_table(
        "citations",
        sa.Column("citation_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "retrieval_run_id",
            sa.BigInteger(),
            sa.ForeignKey("retrieval_runs.retrieval_run_id"),
            nullable=False,
        ),
        sa.Column(
            "document_chunk_id",
            sa.BigInteger(),
            sa.ForeignKey("document_chunks.document_chunk_id"),
            nullable=False,
        ),
        sa.Column("marker", sa.String(30), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("source_label", sa.String(255), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.BigInteger(), primary_key=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), server_default="queued", nullable=False),
        sa.Column("payload", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("retry_of_job_id", sa.BigInteger(), sa.ForeignKey("jobs.job_id")),
        sa.Column("locked_by", sa.String(100)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.user_id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("evaluation_run_id", sa.BigInteger(), primary_key=True),
        sa.Column("trigger_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), server_default="queued", nullable=False),
        sa.Column("summary", sa.JSON()),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "audit_logs",
        sa.Column("audit_log_id", sa.BigInteger(), primary_key=True),
        sa.Column("actor_user_id", sa.BigInteger(), sa.ForeignKey("users.user_id")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(100)),
        sa.Column("target_id", sa.String(100)),
        sa.Column("request_id", sa.String(100)),
        sa.Column("metadata", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "system_settings",
        sa.Column("setting_key", sa.String(100), primary_key=True),
        sa.Column("setting_value", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), sa.ForeignKey("users.user_id")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    for table in [
        "system_settings",
        "audit_logs",
        "evaluation_runs",
        "jobs",
        "citations",
        "retrieval_run_items",
        "retrieval_runs",
        "document_chunks",
        "document_versions",
        "logical_documents",
        "chat_tags",
        "chat_messages",
        "chat_sessions",
        "user_sessions",
        "user_settings",
        "users",
        "roles",
    ]:
        op.drop_table(table)
