from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    Role,
    SystemSetting,
    User,
    UserSetting,
)
from app.services.seed import DEMO_DOCUMENT_TITLE, seed


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.skip("PostgreSQL schema assertions require a PostgreSQL DATABASE_URL")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        engine.dispose()
        pytest.skip("PostgreSQL schema assertions require a reachable database")
    yield engine
    engine.dispose()


def scalar_set(engine: Engine, sql: str) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text(sql)).scalars())


def assert_rejected(engine: Engine, sql: str, params: dict[str, object] | None = None) -> None:
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            with pytest.raises(IntegrityError):
                conn.execute(text(sql), params or {})
        finally:
            transaction.rollback()


def test_migration_head_tables_constraints_and_indexes(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0002_evaluation_results"

    expected_tables = {
        "roles",
        "users",
        "user_settings",
        "user_sessions",
        "chat_sessions",
        "chat_messages",
        "chat_tags",
        "summary_memories",
        "logical_documents",
        "document_versions",
        "document_chunks",
        "jobs",
        "retrieval_runs",
        "retrieval_run_items",
        "citations",
        "evaluation_runs",
        "evaluation_run_items",
        "evaluation_results",
        "audit_logs",
        "system_settings",
    }
    actual_tables = scalar_set(
        pg_engine,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """,
    )
    assert expected_tables <= actual_tables

    expected_constraints = {
        "ck_users_status",
        "ck_users_email_normalized",
        "uq_users_email",
        "ck_document_versions_status",
        "ck_document_versions_active_ready_only",
        "uq_document_versions_content_hash",
        "ck_jobs_running_required_fields",
        "ck_jobs_failed_error_code",
        "fk_citations_retrieval_item",
        "uq_evaluation_results_item_metric",
        "fk_chat_messages_linked_retrieval_run_same_session",
        "ck_audit_logs_request_id_not_empty",
    }
    actual_constraints = scalar_set(
        pg_engine,
        """
        SELECT conname
        FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace
        """,
    )
    assert expected_constraints <= actual_constraints

    expected_indexes = {
        "ux_chat_messages_client_message_id",
        "ux_document_versions_one_active",
        "ix_document_versions_active",
        "ix_jobs_status_priority_created",
        "ix_jobs_lease_expires",
        "ux_jobs_active_retry_per_source",
        "ux_jobs_active_message_edit",
        "ux_retrieval_run_items_run_rerank_order",
        "ix_evaluation_results_metric_score",
    }
    actual_indexes = scalar_set(
        pg_engine,
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
        """,
    )
    assert expected_indexes <= actual_indexes

    with pg_engine.connect() as conn:
        partial_index_defs = {
            row.indexname: row.indexdef.lower()
            for row in conn.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname IN (
                        'ux_document_versions_one_active',
                        'ix_jobs_lease_expires',
                        'ux_jobs_active_retry_per_source',
                        'ux_jobs_active_message_edit',
                        'ix_chat_sessions_user_status_created',
                        'ix_audit_logs_target'
                      )
                    """
                )
            )
        }
    assert "where" in partial_index_defs["ux_document_versions_one_active"]
    assert "is_active" in partial_index_defs["ux_document_versions_one_active"]
    assert "status" in partial_index_defs["ix_jobs_lease_expires"]
    assert "retry_of_job_id" in partial_index_defs["ux_jobs_active_retry_per_source"]
    assert "message_edit_regeneration" in partial_index_defs["ux_jobs_active_message_edit"]
    assert "created_at desc" in partial_index_defs["ix_chat_sessions_user_status_created"]
    assert "created_at desc" in partial_index_defs["ix_audit_logs_target"]


def test_seed_can_run_twice_without_duplicates(
    pg_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    indexing_service = _CapturingIndexingService()
    monkeypatch.setattr(
        "app.services.seed.create_document_indexing_service",
        lambda settings: indexing_service,
    )
    Session = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    with Session() as db:
        seed(db)
    with Session() as db:
        seed(db)

    with Session() as db:
        assert db.query(Role).filter(Role.role_name == "admin").count() == 1
        assert db.query(Role).filter(Role.role_name == "viewer").count() == 1
        assert db.query(User).filter(User.email == "admin@example.com").count() == 1
        assert db.query(User).filter(User.email == "viewer@example.com").count() == 1
        seed_user_ids = [
            user.user_id
            for user in db.query(User)
            .filter(User.email.in_(["admin@example.com", "viewer@example.com"]))
            .all()
        ]
        assert db.query(UserSetting).filter(UserSetting.user_id.in_(seed_user_ids)).count() == 2
        assert (
            db.query(SystemSetting).filter(SystemSetting.setting_key == "rag.fake_mode").count()
            == 1
        )
        assert (
            db.query(SystemSetting).filter(SystemSetting.setting_key == "jobs.retry_max").count()
            == 1
        )
        logical = db.query(LogicalDocument).filter_by(title=DEMO_DOCUMENT_TITLE).one()
        versions = (
            db.query(DocumentVersion)
            .filter_by(logical_document_id=logical.logical_document_id)
            .all()
        )
        assert versions
        active_versions = [version for version in versions if version.is_active]
        assert len(active_versions) == 1
        version = active_versions[0]
        assert version.status == "ready"
        assert (
            db.query(DocumentChunk)
            .filter_by(document_version_id=version.document_version_id)
            .count()
            == 1
        )

    indexed_titles = {call.title for call in indexing_service.calls}
    assert DEMO_DOCUMENT_TITLE in indexed_titles
    assert "LLM Paper Corpus for RAG Demo" in indexed_titles
    assert all(call.version_status == "ready" for call in indexing_service.calls)
    assert all(call.is_active is True for call in indexing_service.calls)
    assert any(
        call.title == "LLM Paper Corpus for RAG Demo" and call.chunk_count >= 100
        for call in indexing_service.calls
    )


def test_major_db_constraints_reject_invalid_data(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        role_id = conn.execute(
            text("SELECT role_id FROM roles WHERE role_name = 'viewer'")
        ).scalar_one()
        admin_user_id = conn.execute(
            text("SELECT user_id FROM users WHERE email = 'admin@example.com'")
        ).scalar_one()

    suffix = uuid.uuid4().hex
    assert_rejected(
        pg_engine,
        """
        INSERT INTO users (role_id, email, display_name, status)
        VALUES (:role_id, :email, 'Invalid Status', 'locked')
        """,
        {"role_id": role_id, "email": f"invalid-{suffix}@example.com"},
    )
    assert_rejected(
        pg_engine,
        """
        INSERT INTO users (role_id, email, display_name, status)
        VALUES (:role_id, 'admin@example.com', 'Duplicate Admin', 'active')
        """,
        {"role_id": role_id},
    )
    assert_rejected(
        pg_engine,
        """
        INSERT INTO jobs (job_type, status)
        VALUES ('document_ingest', 'paused')
        """,
    )

    with pg_engine.connect() as conn:
        transaction = conn.begin()
        try:
            logical_id = conn.execute(
                text(
                    """
                    INSERT INTO logical_documents (owner_user_id, title, status)
                    VALUES (:owner_user_id, :title, 'active')
                    RETURNING logical_document_id
                    """
                ),
                {"owner_user_id": admin_user_id, "title": f"constraint-doc-{suffix}"},
            ).scalar_one()
            common = {
                "logical_document_id": logical_id,
                "created_by": admin_user_id,
                "mime_type": "text/plain",
                "file_size_bytes": 1,
            }
            conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        logical_document_id, version_no, content_hash, status, is_active,
                        file_name, mime_type, file_size_bytes, created_by
                    )
                    VALUES (
                        :logical_document_id, 1, :content_hash, 'ready', TRUE,
                        'v1.txt', :mime_type, :file_size_bytes, :created_by
                    )
                    """
                ),
                common | {"content_hash": "a" * 64},
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO document_versions (
                            logical_document_id, version_no, content_hash, status, is_active,
                            file_name, mime_type, file_size_bytes, created_by
                        )
                        VALUES (
                            :logical_document_id, 2, :content_hash, 'ready', TRUE,
                            'v2.txt', :mime_type, :file_size_bytes, :created_by
                        )
                        """
                    ),
                    common | {"content_hash": "b" * 64},
                )
        finally:
            transaction.rollback()


def test_jobs_active_retry_partial_unique_index(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        try:
            source_job_id = conn.execute(
                text(
                    """
                    INSERT INTO jobs (
                        job_type, status, started_at, finished_at, error_code
                    )
                    VALUES ('document_ingest', 'failed', now(), now(), 'seed_test_failure')
                    RETURNING job_id
                    """
                )
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (job_type, status, retry_of_job_id)
                    VALUES ('document_ingest', 'queued', :source_job_id)
                    """
                ),
                {"source_job_id": source_job_id},
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO jobs (job_type, status, retry_of_job_id)
                        VALUES ('document_ingest', 'queued', :source_job_id)
                        """
                    ),
                    {"source_job_id": source_job_id},
                )
        finally:
            transaction.rollback()


def test_jobs_message_edit_active_partial_unique_index(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (job_type, status, target_type, target_id)
                    VALUES ('message_edit_regeneration', 'queued', 'chat_message', 100)
                    """
                )
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO jobs (job_type, status, target_type, target_id)
                        VALUES ('message_edit_regeneration', 'queued', 'chat_message', 100)
                        """
                    )
                )
        finally:
            transaction.rollback()

        transaction = conn.begin()
        try:
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (
                        job_type, status, target_type, target_id, started_at, finished_at
                    )
                    VALUES (
                        'message_edit_regeneration', 'succeeded', 'chat_message', 100,
                        now(), now()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (job_type, status, target_type, target_id)
                    VALUES ('message_edit_regeneration', 'queued', 'chat_message', 100)
                    """
                )
            )
        finally:
            transaction.rollback()


@dataclass(frozen=True)
class _IndexCall:
    title: str
    version_status: str
    is_active: bool
    chunk_count: int
    chunk_ids: list[int]


class _CapturingIndexingService:
    def __init__(self) -> None:
        self.calls: list[_IndexCall] = []

    def index_chunks(
        self,
        *,
        logical_document: Any,
        document_version: Any,
        chunks: list[Any],
    ) -> None:
        self.calls.append(
            _IndexCall(
                title=logical_document.title,
                version_status=document_version.status,
                is_active=document_version.is_active,
                chunk_count=len(chunks),
                chunk_ids=[chunk.document_chunk_id for chunk in chunks],
            )
        )
