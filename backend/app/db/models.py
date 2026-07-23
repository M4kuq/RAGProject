from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.rag.strategy import (
    DEFAULT_RETRIEVAL_STRATEGY,
    RETRIEVAL_SOURCE_VALUES,
    RETRIEVAL_STRATEGY_VALUES,
    sql_literal_list,
)


def big_int() -> BigInteger:
    return BigInteger().with_variant(Integer, "sqlite")


def jsonb() -> JSON:
    return JSON().with_variant(postgresql.JSONB(), "postgresql")


def inet() -> String:
    return String(45).with_variant(postgresql.INET(), "postgresql")


def pg_check(sqltext: str, name: str) -> CheckConstraint:
    return CheckConstraint(sqltext, name=name).ddl_if(dialect="postgresql")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("role_name", name="uq_roles_role_name"),)

    role_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    role_name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        ForeignKeyConstraint(["role_id"], ["roles.role_id"], ondelete="RESTRICT"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        pg_check(
            "email = lower(email) AND email = btrim(email) AND email <> ''",
            "ck_users_email_normalized",
        ),
        pg_check("btrim(display_name) <> ''", "ck_users_display_name_not_empty"),
    )

    user_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    role_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'active'"), default="active", nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSetting(Base, TimestampMixin):
    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint(
            "ui_theme IN ('light', 'dark', 'system')", name="ck_user_settings_ui_theme"
        ),
        CheckConstraint(
            "memory_message_limit BETWEEN 1 AND 50",
            name="ck_user_settings_memory_message_limit",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        big_int(), ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    ui_theme: Mapped[str] = mapped_column(
        String(30), server_default=text("'system'"), default="system", nullable=False
    )
    memory_message_limit: Mapped[int] = mapped_column(
        Integer, server_default=text("8"), default=8, nullable=False
    )


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("session_token_hash", name="uq_user_sessions_session_token_hash"),
        CheckConstraint("expires_at > created_at", name="ck_user_sessions_expiry"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        big_int(), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    session_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    csrf_state_hash: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(inet())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="RESTRICT"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_chat_sessions_status"),
        pg_check("btrim(title) <> ''", "ck_chat_sessions_title_not_empty"),
        CheckConstraint(
            "(temporary_flag = TRUE AND ttl_expires_at IS NOT NULL) OR "
            "(temporary_flag = FALSE AND ttl_expires_at IS NULL)",
            name="ck_chat_sessions_temporary_ttl",
        ),
        CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status = 'active' AND archived_at IS NULL)",
            name="ck_chat_sessions_archived_at",
        ),
    )

    chat_session_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    user_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'active'"), default="active", nullable=False
    )
    temporary_flag: Mapped[bool] = mapped_column(
        Boolean, server_default=text("FALSE"), default=False, nullable=False
    )
    ttl_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chat_session_id"], ["chat_sessions.chat_session_id"], ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "chat_session_id", "chat_message_id", name="uq_chat_messages_session_message"
        ),
        ForeignKeyConstraint(
            ["chat_session_id", "linked_retrieval_run_id"],
            ["retrieval_runs.chat_session_id", "retrieval_runs.retrieval_run_id"],
            name="fk_chat_messages_linked_retrieval_run_same_session",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_chat_messages_role"),
        pg_check("btrim(content) <> ''", "ck_chat_messages_content_not_empty"),
        CheckConstraint(
            "client_message_id IS NULL OR role = 'user'",
            name="ck_chat_messages_client_message_user_only",
        ),
        CheckConstraint(
            "client_message_id IS NULL OR client_message_id <> ''",
            name="ck_chat_messages_client_message_not_empty",
        ),
        CheckConstraint(
            "linked_retrieval_run_id IS NULL OR role = 'assistant'",
            name="ck_chat_messages_linked_retrieval_assistant_only",
        ),
    )

    chat_message_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    chat_session_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(255))
    linked_retrieval_run_id: Mapped[int | None] = mapped_column(big_int())
    edited_flag: Mapped[bool] = mapped_column(
        Boolean, server_default=text("FALSE"), default=False, nullable=False
    )


class ChatTag(Base):
    __tablename__ = "chat_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chat_session_id"], ["chat_sessions.chat_session_id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("chat_session_id", "tag_name", name="uq_chat_tags_session_name"),
        pg_check("btrim(tag_name) <> ''", "ck_chat_tags_tag_name_not_empty"),
    )

    chat_tag_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    chat_session_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    tag_name: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SummaryMemory(Base):
    __tablename__ = "summary_memories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chat_session_id"], ["chat_sessions.chat_session_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["chat_session_id", "source_message_upto_id"],
            ["chat_messages.chat_session_id", "chat_messages.chat_message_id"],
            name="fk_summary_memories_same_session_message",
            ondelete="RESTRICT",
        ),
        pg_check("btrim(summary_text) <> ''", "ck_summary_memories_summary_text_not_empty"),
    )

    summary_memory_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    chat_session_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    source_message_upto_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LogicalDocument(Base, TimestampMixin):
    __tablename__ = "logical_documents"
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id"], ["users.user_id"], ondelete="RESTRICT"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_logical_documents_status"),
        pg_check("btrim(title) <> ''", "ck_logical_documents_title_not_empty"),
        CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status = 'active' AND archived_at IS NULL)",
            name="ck_logical_documents_archived_at",
        ),
    )

    logical_document_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'active'"), default="active", nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Base, TimestampMixin):
    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["logical_document_id"], ["logical_documents.logical_document_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["created_by"], ["users.user_id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "logical_document_id", "version_no", name="uq_document_versions_version_no"
        ),
        UniqueConstraint(
            "logical_document_id", "content_hash", name="uq_document_versions_content_hash"
        ),
        CheckConstraint("version_no >= 1", name="ck_document_versions_version_no"),
        CheckConstraint(
            "status IN ('processing', 'ready', 'failed', 'archived')",
            name="ck_document_versions_status",
        ),
        CheckConstraint(
            "is_active = FALSE OR status = 'ready'",
            name="ck_document_versions_active_ready_only",
        ),
        pg_check(
            "content_hash ~ '^[0-9a-f]{64}$'",
            "ck_document_versions_content_hash_format",
        ),
        pg_check("btrim(file_name) <> ''", "ck_document_versions_file_name_not_empty"),
        pg_check("btrim(mime_type) <> ''", "ck_document_versions_mime_type_not_empty"),
        CheckConstraint("file_size_bytes >= 0", name="ck_document_versions_file_size"),
        CheckConstraint(
            "page_count IS NULL OR page_count >= 0", name="ck_document_versions_page_count"
        ),
        CheckConstraint(
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status <> 'failed' AND error_code IS NULL)",
            name="ck_document_versions_error_code_by_status",
        ),
    )

    document_version_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    logical_document_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'processing'"), default="processing", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("FALSE"), default=False, nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(big_int(), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    page_count: Mapped[int | None] = mapped_column(Integer)
    extractor_name: Mapped[str | None] = mapped_column(String(100))
    extractor_version: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[int] = mapped_column(big_int(), nullable=False)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.document_version_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "document_version_id", "chunk_index", name="uq_document_chunks_version_index"
        ),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_chunk_index"),
        pg_check("chunk_hash ~ '^[0-9a-f]{64}$'", "ck_document_chunks_chunk_hash_format"),
        pg_check("btrim(content_text) <> ''", "ck_document_chunks_content_not_empty"),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0", name="ck_document_chunks_token_count"
        ),
        CheckConstraint(
            "char_count IS NULL OR char_count >= 0", name="ck_document_chunks_char_count"
        ),
        CheckConstraint(
            "page_from IS NULL OR page_to IS NULL OR page_from <= page_to",
            name="ck_document_chunks_page_range",
        ),
        CheckConstraint(
            "(page_from IS NULL OR page_from >= 1) AND (page_to IS NULL OR page_to >= 1)",
            name="ck_document_chunks_page_positive",
        ),
        CheckConstraint("modality IN ('text')", name="ck_document_chunks_modality"),
    )

    document_chunk_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    document_version_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int | None] = mapped_column(Integer)
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    modality: Mapped[str] = mapped_column(
        String(30), server_default=text("'text'"), default="text", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        ForeignKeyConstraint(["retry_of_job_id"], ["jobs.job_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["created_by"], ["users.user_id"], ondelete="SET NULL"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_jobs_status",
        ),
        CheckConstraint("priority >= 0", name="ck_jobs_priority"),
        CheckConstraint("retry_count >= 0", name="ck_jobs_retry_count"),
        CheckConstraint(
            "retry_of_job_id IS NULL OR retry_of_job_id <> job_id",
            name="ck_jobs_no_self_retry",
        ),
        CheckConstraint(
            "status <> 'queued' OR (started_at IS NULL AND finished_at IS NULL)",
            name="ck_jobs_queued_times",
        ),
        CheckConstraint(
            "status <> 'running' OR "
            "(locked_by IS NOT NULL AND locked_at IS NOT NULL AND "
            "lease_expires_at IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NULL)",
            name="ck_jobs_running_required_fields",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'canceled') OR finished_at IS NOT NULL",
            name="ck_jobs_terminal_finished",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR started_at IS NOT NULL",
            name="ck_jobs_success_failed_started",
        ),
        CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL", name="ck_jobs_failed_error_code"
        ),
        CheckConstraint(
            "job_type <> 'message_edit_regeneration' OR "
            "(target_type = 'chat_message' AND target_id IS NOT NULL)",
            name="ck_jobs_message_edit_target_required",
        ),
        CheckConstraint(
            "lease_expires_at IS NULL OR locked_at IS NULL OR lease_expires_at > locked_at",
            name="ck_jobs_lease_order",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_jobs_finished_after_started",
        ),
    )

    job_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'queued'"), default="queued", nullable=False
    )
    priority: Mapped[int] = mapped_column(
        Integer, server_default=text("100"), default=100, nullable=False
    )
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[int | None] = mapped_column(big_int())
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    result_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_of_job_id: Mapped[int | None] = mapped_column(big_int())
    retry_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0, nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(big_int())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetrievalRun(Base):
    __tablename__ = "retrieval_runs"
    __table_args__ = (
        UniqueConstraint(
            "chat_session_id", "retrieval_run_id", name="uq_retrieval_runs_session_run"
        ),
        ForeignKeyConstraint(
            ["chat_session_id"], ["chat_sessions.chat_session_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["chat_session_id", "request_message_id"],
            ["chat_messages.chat_session_id", "chat_messages.chat_message_id"],
            name="fk_retrieval_runs_request_message_same_session",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "(chat_session_id IS NULL AND request_message_id IS NULL) OR "
            "(chat_session_id IS NOT NULL AND request_message_id IS NOT NULL)",
            name="ck_retrieval_runs_origin",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name="ck_retrieval_runs_status"
        ),
        CheckConstraint(
            "status <> 'running' OR "
            "(started_at IS NOT NULL AND finished_at IS NULL AND error_code IS NULL)",
            name="ck_retrieval_runs_running_times",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed') OR "
            "(started_at IS NOT NULL AND finished_at IS NOT NULL)",
            name="ck_retrieval_runs_terminal_times",
        ),
        CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL", name="ck_retrieval_runs_failed_error"
        ),
        CheckConstraint(
            "status <> 'succeeded' OR error_code IS NULL",
            name="ck_retrieval_runs_succeeded_error_null",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_retrieval_runs_finished_after_started",
        ),
        CheckConstraint(
            "status <> 'failed' OR "
            "(answer_confidence IS NULL AND groundedness_score IS NULL "
            "AND confidence_label IS NULL)",
            name="ck_retrieval_runs_failed_confidence_null",
        ),
        CheckConstraint(
            "answer_confidence IS NULL OR (answer_confidence >= 0 AND answer_confidence <= 1)",
            name="ck_retrieval_runs_confidence_range",
        ),
        CheckConstraint(
            "groundedness_score IS NULL OR (groundedness_score >= 0 AND groundedness_score <= 1)",
            name="ck_retrieval_runs_groundedness_range",
        ),
        CheckConstraint(
            "confidence_label IS NULL OR confidence_label IN ('High', 'Medium', 'Low')",
            name="ck_retrieval_runs_confidence_label",
        ),
        CheckConstraint(
            "top_k IS NULL OR (top_k BETWEEN 1 AND 20)", name="ck_retrieval_runs_top_k"
        ),
        CheckConstraint(
            f"strategy_type IN ({sql_literal_list(RETRIEVAL_STRATEGY_VALUES)})",
            name="ck_retrieval_runs_strategy_type",
        ),
        pg_check(
            "query_hash IS NULL OR query_hash ~ '^[0-9a-f]{64}$'",
            "ck_retrieval_runs_query_hash_format",
        ),
        pg_check(
            "request_id IS NULL OR btrim(request_id) <> ''",
            "ck_retrieval_runs_request_id_not_empty",
        ),
    )

    retrieval_run_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    chat_session_id: Mapped[int | None] = mapped_column(big_int())
    request_message_id: Mapped[int | None] = mapped_column(big_int())
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'running'"), default="running", nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    top_k: Mapped[int | None] = mapped_column(Integer)
    strategy_type: Mapped[str] = mapped_column(
        String(50),
        server_default=text(f"'{DEFAULT_RETRIEVAL_STRATEGY.value}'"),
        default=DEFAULT_RETRIEVAL_STRATEGY.value,
        nullable=False,
    )
    query_hash: Mapped[str | None] = mapped_column(String(64))
    retrieval_score_summary: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    query_plan_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    strategy_decision_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    latency_breakdown_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    retrieval_settings_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    context_budget_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    context_compression_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    tool_result_compression_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    cache_summary_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    rerank_score_top1: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    answer_confidence: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    groundedness_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    confidence_label: Mapped[str | None] = mapped_column(String(30))
    request_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RetrievalRunItem(Base):
    __tablename__ = "retrieval_run_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["retrieval_run_id"], ["retrieval_runs.retrieval_run_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["document_chunk_id"], ["document_chunks.document_chunk_id"], ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "retrieval_run_id", "document_chunk_id", name="uq_retrieval_run_items_run_chunk"
        ),
        CheckConstraint("rank_order >= 1", name="ck_retrieval_run_items_rank_order"),
        CheckConstraint(
            "rerank_order IS NULL OR rerank_order >= 1",
            name="ck_retrieval_run_items_rerank_order",
        ),
        CheckConstraint(
            f"retrieval_source IS NULL OR "
            f"retrieval_source IN ({sql_literal_list(RETRIEVAL_SOURCE_VALUES)})",
            name="ck_retrieval_run_items_source",
        ),
    )

    retrieval_run_item_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    retrieval_run_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    document_chunk_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    retrieval_score: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    rerank_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    rank_order: Mapped[int] = mapped_column(Integer, nullable=False)
    rerank_order: Mapped[int | None] = mapped_column(Integer)
    selected_flag: Mapped[bool] = mapped_column(
        Boolean, server_default=text("FALSE"), default=False, nullable=False
    )
    payload_snapshot: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    retrieval_source: Mapped[str | None] = mapped_column(String(50))
    score_breakdown_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RetrievalCacheEntry(Base):
    __tablename__ = "retrieval_cache_entries"
    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_retrieval_cache_entries_key"),
        CheckConstraint("top_k BETWEEN 1 AND 20", name="ck_retrieval_cache_entries_top_k"),
        CheckConstraint(
            "rerank_top_n BETWEEN 1 AND 20",
            name="ck_retrieval_cache_entries_rerank_top_n",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_retrieval_cache_entries_expires_after_created",
        ),
        pg_check("btrim(cache_namespace) <> ''", "ck_retrieval_cache_entries_namespace"),
        pg_check("btrim(schema_version) <> ''", "ck_retrieval_cache_entries_schema"),
        pg_check("btrim(strategy_type) <> ''", "ck_retrieval_cache_entries_strategy"),
        pg_check("btrim(embedding_model) <> ''", "ck_retrieval_cache_entries_embedding_model"),
        pg_check("btrim(rerank_model) <> ''", "ck_retrieval_cache_entries_rerank_model"),
        pg_check("btrim(graph_store_provider) <> ''", "ck_retrieval_cache_entries_graph_store"),
        pg_check(
            "cache_key ~ '^[0-9a-f]{64}$'",
            "ck_retrieval_cache_entries_cache_key_hash",
        ),
        pg_check(
            "query_hash ~ '^[0-9a-f]{64}$'",
            "ck_retrieval_cache_entries_query_hash",
        ),
        pg_check(
            "retrieval_settings_hash ~ '^[0-9a-f]{64}$'",
            "ck_retrieval_cache_entries_retrieval_hash",
        ),
        pg_check(
            "rerank_settings_hash ~ '^[0-9a-f]{64}$'",
            "ck_retrieval_cache_entries_rerank_hash",
        ),
        pg_check(
            "active_document_fingerprint ~ '^[0-9a-f]{64}$'",
            "ck_retrieval_cache_entries_document_fp",
        ),
        pg_check(
            "graph_index_fingerprint ~ '^[0-9a-f]{64}$'",
            "ck_retrieval_cache_entries_graph_fp",
        ),
        pg_check(
            "user_visible_scope ~ '^[0-9a-f]{64}$'",
            "ck_retrieval_cache_entries_scope_hash",
        ),
    )

    retrieval_cache_entry_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    cache_namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_settings_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rerank_settings_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    rerank_model: Mapped[str] = mapped_column(String(255), nullable=False)
    active_document_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_index_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_store_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    rerank_top_n: Mapped[int] = mapped_column(Integer, nullable=False)
    user_visible_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(jsonb(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["retrieval_run_id", "document_chunk_id"],
            ["retrieval_run_items.retrieval_run_id", "retrieval_run_items.document_chunk_id"],
            name="fk_citations_retrieval_item",
            ondelete="RESTRICT",
        ),
        pg_check("btrim(snippet) <> ''", "ck_citations_snippet_not_empty"),
        pg_check("btrim(display_label) <> ''", "ck_citations_display_label_not_empty"),
        CheckConstraint("rank_order >= 1", name="ck_citations_rank_order"),
        CheckConstraint(
            "page_from IS NULL OR page_to IS NULL OR page_from <= page_to",
            name="ck_citations_page_range",
        ),
        CheckConstraint(
            "(page_from IS NULL OR page_from >= 1) AND (page_to IS NULL OR page_to >= 1)",
            name="ck_citations_page_positive",
        ),
        CheckConstraint(
            "source_type IN ('upload', 'external_url')", name="ck_citations_source_type"
        ),
    )

    citation_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    retrieval_run_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    document_chunk_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(
        String(50), server_default=text("'upload'"), default="upload", nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    display_label: Mapped[str] = mapped_column(Text, nullable=False)
    rank_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvaluationDataset(Base, TimestampMixin):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (
        ForeignKeyConstraint(["created_by"], ["users.user_id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "dataset_name",
            "version",
            name="uq_evaluation_datasets_name_version",
        ),
        CheckConstraint(
            "source_type IN ('manual', 'fixture', 'feedback_promoted', 'imported')",
            name="ck_evaluation_datasets_source_type",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_evaluation_datasets_status",
        ),
        CheckConstraint(
            "corpus_mode IN ('shared_legacy', 'isolated')",
            name="ck_evaluation_datasets_corpus_mode",
        ),
        CheckConstraint(
            "corpus_status IN "
            "('shared_legacy', 'not_prepared', 'preparing', 'ready', 'failed')",
            name="ck_evaluation_datasets_corpus_status",
        ),
        pg_check(
            "content_fingerprint IS NULL OR content_fingerprint ~ '^[0-9a-f]{64}$'",
            "ck_evaluation_datasets_content_fingerprint",
        ),
        pg_check("btrim(dataset_name) <> ''", "ck_evaluation_datasets_name_not_empty"),
        pg_check("btrim(version) <> ''", "ck_evaluation_datasets_version_not_empty"),
    )

    evaluation_dataset_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    dataset_name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str] = mapped_column(
        String(50), server_default=text("'v1'"), default="v1", nullable=False
    )
    source_type: Mapped[str] = mapped_column(
        String(50), server_default=text("'manual'"), default="manual", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'active'"), default="active", nullable=False
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    manifest_schema_version: Mapped[str] = mapped_column(
        String(64),
        server_default=text("'phase2.evaluation_dataset.v1'"),
        default="phase2.evaluation_dataset.v1",
        nullable=False,
    )
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    corpus_fingerprint: Mapped[str | None] = mapped_column(String(64))
    corpus_mode: Mapped[str] = mapped_column(
        String(30),
        server_default=text("'shared_legacy'"),
        default="shared_legacy",
        nullable=False,
    )
    corpus_status: Mapped[str] = mapped_column(
        String(30),
        server_default=text("'shared_legacy'"),
        default="shared_legacy",
        nullable=False,
    )
    corpus_failure_code: Mapped[str | None] = mapped_column(String(100))
    corpus_prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    readiness_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(big_int())


class EvaluationCase(Base, TimestampMixin):
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evaluation_dataset_id"],
            ["evaluation_datasets.evaluation_dataset_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "evaluation_dataset_id",
            "case_key",
            name="uq_evaluation_cases_dataset_key",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_evaluation_cases_status",
        ),
        pg_check("btrim(case_key) <> ''", "ck_evaluation_cases_key_not_empty"),
        pg_check("btrim(question) <> ''", "ck_evaluation_cases_question_not_empty"),
    )

    evaluation_case_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    evaluation_dataset_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    case_key: Mapped[str] = mapped_column(String(120), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str | None] = mapped_column(Text)
    expected_keywords: Mapped[list[Any] | None] = mapped_column(jsonb())
    expected_document_ids: Mapped[list[Any] | None] = mapped_column(jsonb())
    expected_chunk_ids: Mapped[list[Any] | None] = mapped_column(jsonb())
    required_citation: Mapped[bool] = mapped_column(
        Boolean, server_default=text("TRUE"), default=True, nullable=False
    )
    tags: Mapped[list[Any] | None] = mapped_column(jsonb())
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'active'"), default="active", nullable=False
    )


class EvaluationRun(Base, TimestampMixin):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evaluation_dataset_id"],
            ["evaluation_datasets.evaluation_dataset_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["created_by"], ["users.user_id"], ondelete="RESTRICT"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_evaluation_runs_status",
        ),
        CheckConstraint(
            "status <> 'queued' OR (started_at IS NULL AND finished_at IS NULL)",
            name="ck_evaluation_runs_queued_times",
        ),
        CheckConstraint(
            "status <> 'running' OR (started_at IS NOT NULL AND finished_at IS NULL)",
            name="ck_evaluation_runs_running_times",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'failed', 'canceled') OR finished_at IS NOT NULL",
            name="ck_evaluation_runs_terminal_finished",
        ),
        CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_evaluation_runs_failed_error_code",
        ),
        CheckConstraint(
            f"strategy_type IN ({sql_literal_list(RETRIEVAL_STRATEGY_VALUES)})",
            name="ck_evaluation_runs_strategy_type",
        ),
        CheckConstraint(
            "trigger_type IN ('manual', 'ci', 'scheduled', 'post_deploy', 'online_sampled_trace')",
            name="ck_evaluation_runs_trigger_type",
        ),
    )

    evaluation_run_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    created_by: Mapped[int] = mapped_column(big_int(), nullable=False)
    evaluation_dataset_id: Mapped[int | None] = mapped_column(big_int())
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'queued'"), default="queued", nullable=False
    )
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[int | None] = mapped_column(big_int())
    metrics_config: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    strategy_type: Mapped[str] = mapped_column(
        String(50),
        server_default=text(f"'{DEFAULT_RETRIEVAL_STRATEGY.value}'"),
        default=DEFAULT_RETRIEVAL_STRATEGY.value,
        nullable=False,
    )
    trigger_type: Mapped[str] = mapped_column(
        String(50), server_default=text("'manual'"), default="manual", nullable=False
    )
    retrieval_settings_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    strategy_metrics_summary_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    corpus_fingerprint: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationRunItem(Base, TimestampMixin):
    __tablename__ = "evaluation_run_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.evaluation_run_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["evaluation_case_id"],
            ["evaluation_cases.evaluation_case_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["retrieval_run_id"], ["retrieval_runs.retrieval_run_id"], ondelete="RESTRICT"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_evaluation_run_items_status",
        ),
        CheckConstraint(
            "answer_outcome IS NULL OR answer_outcome IN "
            "('answered', 'abstained', 'no_context', 'citation_error', "
            "'generation_error', 'retrieval_error')",
            name="ck_evaluation_run_items_answer_outcome",
        ),
        CheckConstraint(
            "(faithfulness_score IS NULL OR "
            "(faithfulness_score >= 0 AND faithfulness_score <= 1)) "
            "AND (groundedness_score IS NULL OR "
            "(groundedness_score >= 0 AND groundedness_score <= 1)) "
            "AND (citation_coverage IS NULL OR "
            "(citation_coverage >= 0 AND citation_coverage <= 1))",
            name="ck_evaluation_run_items_scores",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0", name="ck_evaluation_run_items_latency"
        ),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0) "
            "AND (total_tokens IS NULL OR total_tokens >= 0) "
            "AND (estimated_cost_usd IS NULL OR estimated_cost_usd >= 0) "
            "AND (generation_latency_ms IS NULL OR generation_latency_ms >= 0)",
            name="ck_evaluation_run_items_generation_non_negative",
        ),
        CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_evaluation_run_items_failed_error_code",
        ),
        CheckConstraint(
            f"strategy_type IN ({sql_literal_list(RETRIEVAL_STRATEGY_VALUES)})",
            name="ck_evaluation_run_items_strategy_type",
        ),
    )

    evaluation_run_item_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    evaluation_run_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    evaluation_case_id: Mapped[int | None] = mapped_column(big_int())
    retrieval_run_id: Mapped[int | None] = mapped_column(big_int())
    strategy_type: Mapped[str] = mapped_column(
        String(50),
        server_default=text(f"'{DEFAULT_RETRIEVAL_STRATEGY.value}'"),
        default=DEFAULT_RETRIEVAL_STRATEGY.value,
        nullable=False,
    )
    case_key: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'queued'"), default="queued", nullable=False
    )
    answer_outcome: Mapped[str | None] = mapped_column(String(30))
    faithfulness_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    groundedness_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    citation_coverage: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    generation_provider: Mapped[str | None] = mapped_column(String(50))
    generation_model: Mapped[str | None] = mapped_column(String(128))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    generation_latency_ms: Mapped[int | None] = mapped_column(Integer)
    latency_breakdown_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    metric_summary_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        ForeignKeyConstraint(["actor_user_id"], ["users.user_id"], ondelete="SET NULL"),
        pg_check("btrim(action_type) <> ''", "ck_audit_logs_action_type_not_empty"),
        pg_check("btrim(target_type) <> ''", "ck_audit_logs_target_type_not_empty"),
        pg_check("btrim(request_id) <> ''", "ck_audit_logs_request_id_not_empty"),
    )

    audit_log_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(big_int())
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[int | None] = mapped_column(big_int())
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    ip_address: Mapped[str | None] = mapped_column(inet())
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SystemSetting(Base, TimestampMixin):
    __tablename__ = "system_settings"
    __table_args__ = (
        ForeignKeyConstraint(["updated_by"], ["users.user_id"], ondelete="SET NULL"),
        pg_check("btrim(setting_key) <> ''", "ck_system_settings_key_not_empty"),
    )

    setting_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    setting_value: Mapped[Any] = mapped_column(jsonb(), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(big_int())


Index("ix_user_sessions_user_id", UserSession.user_id)
Index("ix_user_sessions_expires_at", UserSession.expires_at)
Index(
    "ix_chat_sessions_user_status_created",
    ChatSession.user_id,
    ChatSession.status,
    ChatSession.created_at.desc(),
)
Index(
    "ix_chat_sessions_ttl",
    ChatSession.ttl_expires_at,
    postgresql_where=ChatSession.temporary_flag.is_(True),
    sqlite_where=ChatSession.temporary_flag.is_(True),
)
Index(
    "ix_chat_messages_session_created",
    ChatMessage.chat_session_id,
    ChatMessage.created_at,
    ChatMessage.chat_message_id,
)
Index(
    "ux_chat_messages_client_message_id",
    ChatMessage.chat_session_id,
    ChatMessage.client_message_id,
    unique=True,
    postgresql_where=ChatMessage.client_message_id.is_not(None),
    sqlite_where=ChatMessage.client_message_id.is_not(None),
)
Index(
    "ix_summary_memories_session_created",
    SummaryMemory.chat_session_id,
    SummaryMemory.created_at.desc(),
)
Index(
    "ix_logical_documents_owner_status_created",
    LogicalDocument.owner_user_id,
    LogicalDocument.status,
    LogicalDocument.created_at.desc(),
)
Index(
    "ix_document_versions_logical_status",
    DocumentVersion.logical_document_id,
    DocumentVersion.status,
    DocumentVersion.created_at.desc(),
)
Index(
    "ux_document_versions_one_active",
    DocumentVersion.logical_document_id,
    unique=True,
    postgresql_where=DocumentVersion.is_active.is_(True),
    sqlite_where=DocumentVersion.is_active.is_(True),
)
Index(
    "ix_document_versions_active",
    DocumentVersion.logical_document_id,
    DocumentVersion.is_active,
    postgresql_where=DocumentVersion.is_active.is_(True),
    sqlite_where=DocumentVersion.is_active.is_(True),
)
Index(
    "ix_document_chunks_version_index", DocumentChunk.document_version_id, DocumentChunk.chunk_index
)
Index(
    "ix_document_chunks_content_fts",
    func.to_tsvector(literal_column("'simple'"), DocumentChunk.content_text),
    postgresql_using="gin",
).ddl_if(dialect="postgresql")
Index(
    "ix_document_chunks_content_fts_english",
    func.to_tsvector(literal_column("'english'"), DocumentChunk.content_text),
    postgresql_using="gin",
).ddl_if(dialect="postgresql")
Index("ix_jobs_status_priority_created", Job.status, Job.priority, Job.created_at)
Index(
    "ix_jobs_lease_expires",
    Job.lease_expires_at,
    postgresql_where=Job.status == "running",
    sqlite_where=Job.status == "running",
)
Index("ix_jobs_target", Job.target_type, Job.target_id)
Index(
    "ux_jobs_active_retry_per_source",
    Job.retry_of_job_id,
    unique=True,
    postgresql_where=Job.retry_of_job_id.is_not(None) & Job.status.in_(["queued", "running"]),
    sqlite_where=Job.retry_of_job_id.is_not(None) & Job.status.in_(["queued", "running"]),
)
Index(
    "ux_jobs_active_message_edit",
    Job.target_type,
    Job.target_id,
    unique=True,
    postgresql_where=(Job.job_type == "message_edit_regeneration")
    & (Job.target_type == "chat_message")
    & Job.status.in_(["queued", "running"]),
    sqlite_where=(Job.job_type == "message_edit_regeneration")
    & (Job.target_type == "chat_message")
    & Job.status.in_(["queued", "running"]),
)
Index(
    "ix_retrieval_runs_chat_created",
    RetrievalRun.chat_session_id,
    RetrievalRun.created_at.desc(),
    postgresql_where=RetrievalRun.chat_session_id.is_not(None),
    sqlite_where=RetrievalRun.chat_session_id.is_not(None),
)
Index("ix_retrieval_runs_status_created", RetrievalRun.status, RetrievalRun.created_at.desc())
Index(
    "ix_retrieval_runs_request_message",
    RetrievalRun.request_message_id,
    postgresql_where=RetrievalRun.request_message_id.is_not(None),
    sqlite_where=RetrievalRun.request_message_id.is_not(None),
)
Index(
    "ux_retrieval_run_items_run_rank",
    RetrievalRunItem.retrieval_run_id,
    RetrievalRunItem.rank_order,
    unique=True,
)
Index(
    "ux_retrieval_run_items_run_rerank_order",
    RetrievalRunItem.retrieval_run_id,
    RetrievalRunItem.rerank_order,
    unique=True,
    postgresql_where=RetrievalRunItem.rerank_order.is_not(None),
    sqlite_where=RetrievalRunItem.rerank_order.is_not(None),
)
Index("ix_retrieval_run_items_chunk", RetrievalRunItem.document_chunk_id)
Index("ix_retrieval_cache_entries_expires", RetrievalCacheEntry.expires_at)
Index(
    "ix_retrieval_cache_entries_namespace_strategy",
    RetrievalCacheEntry.cache_namespace,
    RetrievalCacheEntry.strategy_type,
    RetrievalCacheEntry.created_at.desc(),
)
Index("ux_citations_run_rank", Citation.retrieval_run_id, Citation.rank_order, unique=True)
Index("ix_citations_chunk", Citation.document_chunk_id)
Index("ix_evaluation_runs_status_created", EvaluationRun.status, EvaluationRun.created_at.desc())
Index(
    "ix_evaluation_runs_dataset_strategy",
    EvaluationRun.evaluation_dataset_id,
    EvaluationRun.strategy_type,
    EvaluationRun.created_at.desc(),
)
Index(
    "ix_evaluation_run_items_run_status",
    EvaluationRunItem.evaluation_run_id,
    EvaluationRunItem.status,
)
Index(
    "ix_evaluation_datasets_status_created",
    EvaluationDataset.status,
    EvaluationDataset.created_at.desc(),
)
Index(
    "ix_evaluation_cases_dataset_status",
    EvaluationCase.evaluation_dataset_id,
    EvaluationCase.status,
)
Index("ix_evaluation_run_items_case", EvaluationRunItem.evaluation_case_id)
Index("ix_audit_logs_created", AuditLog.created_at.desc())
Index("ix_audit_logs_action_created", AuditLog.action_type, AuditLog.created_at.desc())
Index("ix_audit_logs_target", AuditLog.target_type, AuditLog.target_id, AuditLog.created_at.desc())
