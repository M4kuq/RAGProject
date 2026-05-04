"""initial DDL v1.8 schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "roles",
        sa.Column("role_id", sa.BigInteger(), primary_key=True),
        sa.Column("role_name", sa.String(50), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("role_name", name="uq_roles_role_name"),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "role_id",
            sa.BigInteger(),
            sa.ForeignKey("roles.role_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.Text()),
        sa.Column("status", sa.String(30), server_default=sa.text("'active'"), nullable=False),
        *timestamps(),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        sa.CheckConstraint(
            "email = lower(email) AND email = btrim(email) AND email <> ''",
            name="ck_users_email_normalized",
        ),
        sa.CheckConstraint("btrim(display_name) <> ''", name="ck_users_display_name_not_empty"),
    )
    op.create_table(
        "user_settings",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ui_theme", sa.String(30), server_default=sa.text("'system'"), nullable=False),
        sa.Column(
            "memory_message_limit", sa.Integer(), server_default=sa.text("8"), nullable=False
        ),
        *timestamps(),
        sa.CheckConstraint(
            "ui_theme IN ('light', 'dark', 'system')", name="ck_user_settings_ui_theme"
        ),
        sa.CheckConstraint(
            "memory_message_limit BETWEEN 1 AND 50",
            name="ck_user_settings_memory_message_limit",
        ),
    )
    op.create_table(
        "user_sessions",
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_token_hash", sa.Text(), nullable=False),
        sa.Column("csrf_state_hash", sa.Text()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("ip_address", postgresql.INET()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("session_token_hash", name="uq_user_sessions_session_token_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_user_sessions_expiry"),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("chat_session_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), server_default=sa.text("'active'"), nullable=False),
        sa.Column("temporary_flag", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("ttl_expires_at", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_chat_sessions_status"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_chat_sessions_title_not_empty"),
        sa.CheckConstraint(
            "(temporary_flag = TRUE AND ttl_expires_at IS NOT NULL) OR "
            "(temporary_flag = FALSE AND ttl_expires_at IS NULL)",
            name="ck_chat_sessions_temporary_ttl",
        ),
        sa.CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status = 'active' AND archived_at IS NULL)",
            name="ck_chat_sessions_archived_at",
        ),
    )
    op.create_table(
        "chat_tags",
        sa.Column("chat_tag_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_session_id",
            sa.BigInteger(),
            sa.ForeignKey("chat_sessions.chat_session_id", ondelete="RESTRICT"),
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
        sa.CheckConstraint("btrim(tag_name) <> ''", name="ck_chat_tags_tag_name_not_empty"),
    )
    op.create_table(
        "chat_messages",
        sa.Column("chat_message_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_session_id",
            sa.BigInteger(),
            sa.ForeignKey("chat_sessions.chat_session_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("client_message_id", sa.String(255)),
        sa.Column("linked_retrieval_run_id", sa.BigInteger()),
        sa.Column("edited_flag", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        *timestamps(),
        sa.UniqueConstraint(
            "chat_session_id", "chat_message_id", name="uq_chat_messages_session_message"
        ),
        sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_chat_messages_role"),
        sa.CheckConstraint("btrim(content) <> ''", name="ck_chat_messages_content_not_empty"),
        sa.CheckConstraint(
            "client_message_id IS NULL OR role = 'user'",
            name="ck_chat_messages_client_message_user_only",
        ),
        sa.CheckConstraint(
            "client_message_id IS NULL OR client_message_id <> ''",
            name="ck_chat_messages_client_message_not_empty",
        ),
        sa.CheckConstraint(
            "linked_retrieval_run_id IS NULL OR role = 'assistant'",
            name="ck_chat_messages_linked_retrieval_assistant_only",
        ),
    )
    op.create_table(
        "summary_memories",
        sa.Column("summary_memory_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_session_id",
            sa.BigInteger(),
            sa.ForeignKey("chat_sessions.chat_session_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_message_upto_id", sa.BigInteger(), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_session_id", "source_message_upto_id"],
            ["chat_messages.chat_session_id", "chat_messages.chat_message_id"],
            name="fk_summary_memories_same_session_message",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "btrim(summary_text) <> ''", name="ck_summary_memories_summary_text_not_empty"
        ),
    )

    op.create_table(
        "logical_documents",
        sa.Column("logical_document_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), server_default=sa.text("'active'"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_logical_documents_status"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_logical_documents_title_not_empty"),
        sa.CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status = 'active' AND archived_at IS NULL)",
            name="ck_logical_documents_archived_at",
        ),
    )
    op.create_table(
        "document_versions",
        sa.Column("document_version_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "logical_document_id",
            sa.BigInteger(),
            sa.ForeignKey("logical_documents.logical_document_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("status", sa.String(30), server_default=sa.text("'processing'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.Text()),
        sa.Column("page_count", sa.Integer()),
        sa.Column("extractor_name", sa.String(100)),
        sa.Column("extractor_version", sa.String(100)),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        *timestamps(),
        sa.UniqueConstraint(
            "logical_document_id", "version_no", name="uq_document_versions_version_no"
        ),
        sa.UniqueConstraint(
            "logical_document_id", "content_hash", name="uq_document_versions_content_hash"
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_document_versions_version_no"),
        sa.CheckConstraint(
            "status IN ('processing', 'ready', 'failed', 'archived')",
            name="ck_document_versions_status",
        ),
        sa.CheckConstraint(
            "is_active = FALSE OR status = 'ready'",
            name="ck_document_versions_active_ready_only",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_document_versions_content_hash_format",
        ),
        sa.CheckConstraint(
            "btrim(file_name) <> ''", name="ck_document_versions_file_name_not_empty"
        ),
        sa.CheckConstraint(
            "btrim(mime_type) <> ''", name="ck_document_versions_mime_type_not_empty"
        ),
        sa.CheckConstraint("file_size_bytes >= 0", name="ck_document_versions_file_size"),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count >= 0", name="ck_document_versions_page_count"
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status <> 'failed' AND error_code IS NULL)",
            name="ck_document_versions_error_code_by_status",
        ),
    )
    op.create_table(
        "document_chunks",
        sa.Column("document_chunk_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.BigInteger(),
            sa.ForeignKey("document_versions.document_version_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_hash", sa.CHAR(64), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer()),
        sa.Column("char_count", sa.Integer()),
        sa.Column("page_from", sa.Integer()),
        sa.Column("page_to", sa.Integer()),
        sa.Column("section_title", sa.Text()),
        sa.Column("modality", sa.String(30), server_default=sa.text("'text'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "document_version_id", "chunk_index", name="uq_document_chunks_version_index"
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_document_chunks_chunk_index"),
        sa.CheckConstraint(
            "chunk_hash ~ '^[0-9a-f]{64}$'", name="ck_document_chunks_chunk_hash_format"
        ),
        sa.CheckConstraint(
            "btrim(content_text) <> ''", name="ck_document_chunks_content_not_empty"
        ),
        sa.CheckConstraint(
            "token_count IS NULL OR token_count >= 0", name="ck_document_chunks_token_count"
        ),
        sa.CheckConstraint(
            "char_count IS NULL OR char_count >= 0", name="ck_document_chunks_char_count"
        ),
        sa.CheckConstraint(
            "page_from IS NULL OR page_to IS NULL OR page_from <= page_to",
            name="ck_document_chunks_page_range",
        ),
        sa.CheckConstraint(
            "(page_from IS NULL OR page_from >= 1) AND (page_to IS NULL OR page_to >= 1)",
            name="ck_document_chunks_page_positive",
        ),
        sa.CheckConstraint("modality IN ('text')", name="ck_document_chunks_modality"),
    )

    op.create_table(
        "jobs",
        sa.Column("job_id", sa.BigInteger(), primary_key=True),
        sa.Column("job_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("target_type", sa.String(80)),
        sa.Column("target_id", sa.BigInteger()),
        sa.Column("payload_json", postgresql.JSONB()),
        sa.Column("result_json", postgresql.JSONB()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("locked_by", sa.String(100)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("retry_of_job_id", sa.BigInteger()),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_by", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="SET NULL")
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.ForeignKeyConstraint(["retry_of_job_id"], ["jobs.job_id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_jobs_priority"),
        sa.CheckConstraint("retry_count >= 0", name="ck_jobs_retry_count"),
        sa.CheckConstraint(
            "retry_of_job_id IS NULL OR retry_of_job_id <> job_id",
            name="ck_jobs_no_self_retry",
        ),
        sa.CheckConstraint(
            "status <> 'queued' OR (started_at IS NULL AND finished_at IS NULL)",
            name="ck_jobs_queued_times",
        ),
        sa.CheckConstraint(
            "status <> 'running' OR "
            "(locked_by IS NOT NULL AND locked_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND started_at IS NOT NULL AND finished_at IS NULL)",
            name="ck_jobs_running_required_fields",
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'canceled') OR finished_at IS NOT NULL",
            name="ck_jobs_terminal_finished",
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR started_at IS NOT NULL",
            name="ck_jobs_success_failed_started",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL", name="ck_jobs_failed_error_code"
        ),
        sa.CheckConstraint(
            "job_type <> 'message_edit_regeneration' OR "
            "(target_type = 'chat_message' AND target_id IS NOT NULL)",
            name="ck_jobs_message_edit_target_required",
        ),
        sa.CheckConstraint(
            "lease_expires_at IS NULL OR locked_at IS NULL OR lease_expires_at > locked_at",
            name="ck_jobs_lease_order",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_jobs_finished_after_started",
        ),
    )

    op.create_table(
        "retrieval_runs",
        sa.Column("retrieval_run_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "chat_session_id",
            sa.BigInteger(),
            sa.ForeignKey("chat_sessions.chat_session_id", ondelete="RESTRICT"),
        ),
        sa.Column("request_message_id", sa.BigInteger()),
        sa.Column("status", sa.String(30), server_default=sa.text("'running'"), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("top_k", sa.Integer()),
        sa.Column("query_hash", sa.CHAR(64)),
        sa.Column("retrieval_score_summary", postgresql.JSONB()),
        sa.Column("rerank_score_top1", sa.Numeric(10, 6)),
        sa.Column("answer_confidence", sa.Numeric(10, 6)),
        sa.Column("groundedness_score", sa.Numeric(10, 6)),
        sa.Column("confidence_label", sa.String(30)),
        sa.Column("request_id", sa.String(100)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "chat_session_id", "retrieval_run_id", name="uq_retrieval_runs_session_run"
        ),
        sa.ForeignKeyConstraint(
            ["chat_session_id", "request_message_id"],
            ["chat_messages.chat_session_id", "chat_messages.chat_message_id"],
            name="fk_retrieval_runs_request_message_same_session",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "(chat_session_id IS NULL AND request_message_id IS NULL) OR "
            "(chat_session_id IS NOT NULL AND request_message_id IS NOT NULL)",
            name="ck_retrieval_runs_origin",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_retrieval_runs_status"
        ),
        sa.CheckConstraint(
            "status <> 'running' OR "
            "(started_at IS NOT NULL AND finished_at IS NULL AND error_code IS NULL)",
            name="ck_retrieval_runs_running_times",
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR "
            "(started_at IS NOT NULL AND finished_at IS NOT NULL)",
            name="ck_retrieval_runs_terminal_times",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL", name="ck_retrieval_runs_failed_error"
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR error_code IS NULL",
            name="ck_retrieval_runs_succeeded_error_null",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_retrieval_runs_finished_after_started",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR "
            "(answer_confidence IS NULL AND groundedness_score IS NULL "
            "AND confidence_label IS NULL)",
            name="ck_retrieval_runs_failed_confidence_null",
        ),
        sa.CheckConstraint(
            "answer_confidence IS NULL OR (answer_confidence >= 0 AND answer_confidence <= 1)",
            name="ck_retrieval_runs_confidence_range",
        ),
        sa.CheckConstraint(
            "groundedness_score IS NULL OR (groundedness_score >= 0 AND groundedness_score <= 1)",
            name="ck_retrieval_runs_groundedness_range",
        ),
        sa.CheckConstraint(
            "confidence_label IS NULL OR confidence_label IN ('High', 'Medium', 'Low')",
            name="ck_retrieval_runs_confidence_label",
        ),
        sa.CheckConstraint(
            "top_k IS NULL OR (top_k BETWEEN 1 AND 20)", name="ck_retrieval_runs_top_k"
        ),
        sa.CheckConstraint(
            "query_hash IS NULL OR query_hash ~ '^[0-9a-f]{64}$'",
            name="ck_retrieval_runs_query_hash_format",
        ),
        sa.CheckConstraint(
            "request_id IS NULL OR btrim(request_id) <> ''",
            name="ck_retrieval_runs_request_id_not_empty",
        ),
    )
    op.create_foreign_key(
        "fk_chat_messages_linked_retrieval_run_same_session",
        "chat_messages",
        "retrieval_runs",
        ["chat_session_id", "linked_retrieval_run_id"],
        ["chat_session_id", "retrieval_run_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "retrieval_run_items",
        sa.Column("retrieval_run_item_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "retrieval_run_id",
            sa.BigInteger(),
            sa.ForeignKey("retrieval_runs.retrieval_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_chunk_id",
            sa.BigInteger(),
            sa.ForeignKey("document_chunks.document_chunk_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("retrieval_score", sa.Numeric(10, 6), nullable=False),
        sa.Column("rerank_score", sa.Numeric(10, 6)),
        sa.Column("rank_order", sa.Integer(), nullable=False),
        sa.Column("rerank_order", sa.Integer()),
        sa.Column("selected_flag", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("payload_snapshot", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "retrieval_run_id", "document_chunk_id", name="uq_retrieval_run_items_run_chunk"
        ),
        sa.CheckConstraint("rank_order >= 1", name="ck_retrieval_run_items_rank_order"),
        sa.CheckConstraint(
            "rerank_order IS NULL OR rerank_order >= 1",
            name="ck_retrieval_run_items_rerank_order",
        ),
    )
    op.create_table(
        "citations",
        sa.Column("citation_id", sa.BigInteger(), primary_key=True),
        sa.Column("retrieval_run_id", sa.BigInteger(), nullable=False),
        sa.Column("document_chunk_id", sa.BigInteger(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("page_from", sa.Integer()),
        sa.Column("page_to", sa.Integer()),
        sa.Column("source_type", sa.String(50), server_default=sa.text("'upload'"), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("display_label", sa.Text(), nullable=False),
        sa.Column("rank_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_run_id", "document_chunk_id"],
            ["retrieval_run_items.retrieval_run_id", "retrieval_run_items.document_chunk_id"],
            name="fk_citations_retrieval_item",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("btrim(snippet) <> ''", name="ck_citations_snippet_not_empty"),
        sa.CheckConstraint(
            "btrim(display_label) <> ''", name="ck_citations_display_label_not_empty"
        ),
        sa.CheckConstraint("rank_order >= 1", name="ck_citations_rank_order"),
        sa.CheckConstraint(
            "page_from IS NULL OR page_to IS NULL OR page_from <= page_to",
            name="ck_citations_page_range",
        ),
        sa.CheckConstraint(
            "(page_from IS NULL OR page_from >= 1) AND (page_to IS NULL OR page_to >= 1)",
            name="ck_citations_page_positive",
        ),
        sa.CheckConstraint(
            "source_type IN ('upload', 'external_url')", name="ck_citations_source_type"
        ),
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("evaluation_run_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("target_type", sa.String(80)),
        sa.Column("target_id", sa.BigInteger()),
        sa.Column("metrics_config", postgresql.JSONB()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_evaluation_runs_status",
        ),
        sa.CheckConstraint(
            "status <> 'queued' OR (started_at IS NULL AND finished_at IS NULL)",
            name="ck_evaluation_runs_queued_times",
        ),
        sa.CheckConstraint(
            "status <> 'running' OR (started_at IS NOT NULL AND finished_at IS NULL)",
            name="ck_evaluation_runs_running_times",
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'canceled') OR finished_at IS NOT NULL",
            name="ck_evaluation_runs_terminal_finished",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_evaluation_runs_failed_error_code",
        ),
    )
    op.create_table(
        "evaluation_run_items",
        sa.Column("evaluation_run_item_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "evaluation_run_id",
            sa.BigInteger(),
            sa.ForeignKey("evaluation_runs.evaluation_run_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "retrieval_run_id",
            sa.BigInteger(),
            sa.ForeignKey("retrieval_runs.retrieval_run_id", ondelete="RESTRICT"),
        ),
        sa.Column("status", sa.String(30), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("faithfulness_score", sa.Numeric(10, 6)),
        sa.Column("groundedness_score", sa.Numeric(10, 6)),
        sa.Column("citation_coverage", sa.Numeric(10, 6)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        *timestamps(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_evaluation_run_items_status",
        ),
        sa.CheckConstraint(
            "(faithfulness_score IS NULL OR "
            "(faithfulness_score >= 0 AND faithfulness_score <= 1)) "
            "AND (groundedness_score IS NULL OR "
            "(groundedness_score >= 0 AND groundedness_score <= 1)) "
            "AND (citation_coverage IS NULL OR "
            "(citation_coverage >= 0 AND citation_coverage <= 1))",
            name="ck_evaluation_run_items_scores",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name="ck_evaluation_run_items_latency"
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_evaluation_run_items_failed_error_code",
        ),
    )

    op.create_table(
        "audit_logs",
        sa.Column("audit_log_id", sa.BigInteger(), primary_key=True),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column(
            "actor_user_id", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="SET NULL")
        ),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(100), nullable=False),
        sa.Column("target_id", sa.BigInteger()),
        sa.Column("metadata_json", postgresql.JSONB()),
        sa.Column("ip_address", postgresql.INET()),
        sa.Column("user_agent", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(action_type) <> ''", name="ck_audit_logs_action_type_not_empty"),
        sa.CheckConstraint("btrim(target_type) <> ''", name="ck_audit_logs_target_type_not_empty"),
        sa.CheckConstraint("btrim(request_id) <> ''", name="ck_audit_logs_request_id_not_empty"),
    )
    op.create_table(
        "system_settings",
        sa.Column("setting_key", sa.String(100), primary_key=True),
        sa.Column("setting_value", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "updated_by", sa.BigInteger(), sa.ForeignKey("users.user_id", ondelete="SET NULL")
        ),
        *timestamps(),
        sa.CheckConstraint("btrim(setting_key) <> ''", name="ck_system_settings_key_not_empty"),
    )

    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.execute(
        """
        CREATE INDEX ix_chat_sessions_user_status_created
        ON chat_sessions(user_id, status, created_at DESC)
        """
    )
    op.create_index(
        "ix_chat_sessions_ttl",
        "chat_sessions",
        ["ttl_expires_at"],
        postgresql_where=sa.text("temporary_flag = TRUE"),
    )
    op.create_index(
        "ix_chat_messages_session_created",
        "chat_messages",
        ["chat_session_id", "created_at", "chat_message_id"],
    )
    op.create_index(
        "ux_chat_messages_client_message_id",
        "chat_messages",
        ["chat_session_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE INDEX ix_summary_memories_session_created
        ON summary_memories(chat_session_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_logical_documents_owner_status_created
        ON logical_documents(owner_user_id, status, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_document_versions_logical_status
        ON document_versions(logical_document_id, status, created_at DESC)
        """
    )
    op.create_index(
        "ux_document_versions_one_active",
        "document_versions",
        ["logical_document_id"],
        unique=True,
        postgresql_where=sa.text("is_active = TRUE"),
    )
    op.create_index(
        "ix_document_versions_active",
        "document_versions",
        ["logical_document_id", "is_active"],
        postgresql_where=sa.text("is_active = TRUE"),
    )
    op.create_index(
        "ix_document_chunks_version_index",
        "document_chunks",
        ["document_version_id", "chunk_index"],
    )
    op.create_index("ix_jobs_status_priority_created", "jobs", ["status", "priority", "created_at"])
    op.create_index(
        "ix_jobs_lease_expires",
        "jobs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index("ix_jobs_target", "jobs", ["target_type", "target_id"])
    op.create_index(
        "ux_jobs_active_retry_per_source",
        "jobs",
        ["retry_of_job_id"],
        unique=True,
        postgresql_where=sa.text("retry_of_job_id IS NOT NULL AND status IN ('queued', 'running')"),
    )
    op.create_index(
        "ux_jobs_active_message_edit",
        "jobs",
        ["target_type", "target_id"],
        unique=True,
        postgresql_where=sa.text(
            "job_type = 'message_edit_regeneration' "
            "AND target_type = 'chat_message' "
            "AND status IN ('queued', 'running')"
        ),
    )
    op.execute(
        """
        CREATE INDEX ix_retrieval_runs_chat_created
        ON retrieval_runs(chat_session_id, created_at DESC)
        WHERE chat_session_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_retrieval_runs_status_created
        ON retrieval_runs(status, created_at DESC)
        """
    )
    op.create_index(
        "ix_retrieval_runs_request_message",
        "retrieval_runs",
        ["request_message_id"],
        postgresql_where=sa.text("request_message_id IS NOT NULL"),
    )
    op.create_index(
        "ux_retrieval_run_items_run_rank",
        "retrieval_run_items",
        ["retrieval_run_id", "rank_order"],
        unique=True,
    )
    op.create_index(
        "ux_retrieval_run_items_run_rerank_order",
        "retrieval_run_items",
        ["retrieval_run_id", "rerank_order"],
        unique=True,
        postgresql_where=sa.text("rerank_order IS NOT NULL"),
    )
    op.create_index("ix_retrieval_run_items_chunk", "retrieval_run_items", ["document_chunk_id"])
    op.create_index(
        "ux_citations_run_rank", "citations", ["retrieval_run_id", "rank_order"], unique=True
    )
    op.create_index("ix_citations_chunk", "citations", ["document_chunk_id"])
    op.execute(
        """
        CREATE INDEX ix_evaluation_runs_status_created
        ON evaluation_runs(status, created_at DESC)
        """
    )
    op.create_index(
        "ix_evaluation_run_items_run_status",
        "evaluation_run_items",
        ["evaluation_run_id", "status"],
    )
    op.execute("CREATE INDEX ix_audit_logs_created ON audit_logs(created_at DESC)")
    op.execute(
        """
        CREATE INDEX ix_audit_logs_action_created
        ON audit_logs(action_type, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_audit_logs_target
        ON audit_logs(target_type, target_id, created_at DESC)
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_chat_messages_linked_retrieval_run_same_session",
        "chat_messages",
        type_="foreignkey",
    )
    for table in [
        "system_settings",
        "audit_logs",
        "evaluation_run_items",
        "evaluation_runs",
        "citations",
        "retrieval_run_items",
        "retrieval_runs",
        "jobs",
        "document_chunks",
        "document_versions",
        "logical_documents",
        "summary_memories",
        "chat_tags",
        "chat_messages",
        "chat_sessions",
        "user_sessions",
        "user_settings",
        "users",
        "roles",
    ]:
        op.drop_table(table)
