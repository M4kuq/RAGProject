from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.job_utils import LeaseLostError
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import (
    AuditLog,
    ChatMessage,
    ChatSession,
    ChatTag,
    Citation,
    DocumentChunk,
    DocumentVersion,
    Job,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    Role,
    SummaryMemory,
    User,
)
from app.repositories.chat_repository import ChatRepository
from app.workers.handlers.base import JobExecutionContext
from app.workers.handlers.temporary_chat_cleanup_handler import TemporaryChatCleanupHandler

WORKER_ID = "worker-1"


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with factory() as db:
        role = Role(role_name="viewer", description="Viewer")
        db.add(role)
        db.flush()
        db.add(
            User(
                role_id=role.role_id,
                email="viewer@example.com",
                display_name="Viewer",
                password_hash=hash_password("password"),
                status="active",
            )
        )
        db.commit()
    try:
        yield factory
    finally:
        engine.dispose()


def _user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == "viewer@example.com"))
    assert user is not None
    return user


def _running_job(
    db: Session,
    *,
    chat_session_id: int | None,
    locked_by: str = WORKER_ID,
) -> Job:
    now = datetime.now(UTC)
    job = Job(
        job_type="temporary_chat_cleanup",
        status="running",
        target_type="chat_session",
        target_id=chat_session_id,
        payload_json={},
        locked_by=locked_by,
        locked_at=now,
        lease_expires_at=now + timedelta(minutes=5),
        started_at=now,
    )
    db.add(job)
    db.flush()
    return job


def _context(job: Job, *, worker_instance_id: str = WORKER_ID) -> JobExecutionContext:
    return JobExecutionContext(
        job_id=job.job_id,
        job_type="temporary_chat_cleanup",
        target_type="chat_session",
        target_id=job.target_id,
        payload={},
        worker_instance_id=worker_instance_id,
    )


def _temporary_session(
    db: Session,
    *,
    ttl_expires_at: datetime | None,
    temporary_flag: bool = True,
) -> ChatSession:
    session = ChatSession(
        user_id=_user(db).user_id,
        title="temporary",
        status="active",
        temporary_flag=temporary_flag,
        ttl_expires_at=ttl_expires_at,
    )
    db.add(session)
    db.flush()
    return session


def test_cleanup_deletes_expired_temporary_session_with_children(
    session_factory: sessionmaker[Session],
) -> None:
    handler = TemporaryChatCleanupHandler(session_factory=session_factory)
    now = datetime.now(UTC)
    with session_factory() as db:
        session = _temporary_session(db, ttl_expires_at=now - timedelta(minutes=1))
        chat_session_id = session.chat_session_id
        user_message = ChatMessage(
            chat_session_id=chat_session_id,
            role="user",
            content="cleanup request",
            client_message_id="cleanup-1",
        )
        db.add(user_message)
        db.flush()
        run = RetrievalRun(
            chat_session_id=chat_session_id,
            request_message_id=user_message.chat_message_id,
            status="succeeded",
            started_at=now,
            finished_at=now,
            top_k=1,
        )
        document = LogicalDocument(owner_user_id=_user(db).user_id, title="Doc")
        db.add_all([run, document])
        db.flush()
        version = DocumentVersion(
            logical_document_id=document.logical_document_id,
            version_no=1,
            content_hash="a" * 64,
            status="ready",
            is_active=True,
            file_name="doc.md",
            mime_type="text/markdown",
            file_size_bytes=3,
            storage_key="doc",
            created_by=_user(db).user_id,
        )
        db.add(version)
        db.flush()
        chunk = DocumentChunk(
            document_version_id=version.document_version_id,
            chunk_index=0,
            chunk_hash="b" * 64,
            content_text="chunk body",
        )
        db.add(chunk)
        db.flush()
        db.add_all(
            [
                ChatMessage(
                    chat_session_id=chat_session_id,
                    role="assistant",
                    content="cleanup answer",
                    linked_retrieval_run_id=run.retrieval_run_id,
                ),
                Citation(
                    retrieval_run_id=run.retrieval_run_id,
                    document_chunk_id=chunk.document_chunk_id,
                    snippet="chunk body",
                    display_label="doc.md",
                    rank_order=1,
                ),
                RetrievalRunItem(
                    retrieval_run_id=run.retrieval_run_id,
                    document_chunk_id=chunk.document_chunk_id,
                    retrieval_score=Decimal("0.900000"),
                    rerank_score=Decimal("0.950000"),
                    rank_order=1,
                    rerank_order=1,
                    selected_flag=True,
                    payload_snapshot={},
                ),
                ChatTag(chat_session_id=chat_session_id, tag_name="cleanup-test"),
                SummaryMemory(
                    chat_session_id=chat_session_id,
                    source_message_upto_id=user_message.chat_message_id,
                    summary_text="cleanup summary",
                ),
                AuditLog(
                    action_type="chat.temporary_session_created",
                    actor_user_id=_user(db).user_id,
                    request_id="seed",
                    target_type="chat_session",
                    target_id=chat_session_id,
                    metadata_json={},
                ),
            ]
        )
        job = _running_job(db, chat_session_id=chat_session_id)
        db.commit()
        context = _context(job)

    result = handler.handle(context)

    assert result.status == "succeeded"
    assert result.result_json == {
        "result": "deleted",
        "chat_session_id": chat_session_id,
        "deleted_message_count": 2,
        "deleted_retrieval_run_count": 1,
        "deleted_citation_count": 1,
        "deleted_summary_memory_count": 1,
    }
    with session_factory() as db:
        assert db.get(ChatSession, chat_session_id) is None
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.query(ChatTag).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.query(SummaryMemory).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.query(RetrievalRunItem).count() == 0
        assert db.query(Citation).count() == 0
        assert db.get(DocumentChunk, chunk.document_chunk_id) is not None
        audit_logs = db.scalars(select(AuditLog).order_by(AuditLog.audit_log_id)).all()
        assert [log.action_type for log in audit_logs] == [
            "chat.temporary_session_created",
            "temporary_chat_cleanup",
        ]
        cleanup_log = audit_logs[1]
        assert cleanup_log.actor_user_id is None
        assert cleanup_log.target_type == "chat_session"
        assert cleanup_log.target_id == chat_session_id
        assert cleanup_log.metadata_json == {
            "deleted_message_count": 2,
            "deleted_retrieval_run_count": 1,
            "deleted_citation_count": 1,
            "deleted_summary_memory_count": 1,
        }


def test_cleanup_noop_when_ttl_not_expired(
    session_factory: sessionmaker[Session],
) -> None:
    handler = TemporaryChatCleanupHandler(session_factory=session_factory)
    with session_factory() as db:
        session = _temporary_session(db, ttl_expires_at=datetime.now(UTC) + timedelta(minutes=30))
        job = _running_job(db, chat_session_id=session.chat_session_id)
        db.commit()
        context = _context(job)
        chat_session_id = session.chat_session_id

    result = handler.handle(context)

    assert result.status == "succeeded"
    assert result.result_json == {"result": "no_op", "chat_session_id": chat_session_id}
    with session_factory() as db:
        assert db.get(ChatSession, chat_session_id) is not None
        assert db.query(AuditLog).filter_by(action_type="temporary_chat_cleanup").count() == 0


def test_cleanup_noop_when_session_missing(
    session_factory: sessionmaker[Session],
) -> None:
    handler = TemporaryChatCleanupHandler(session_factory=session_factory)
    with session_factory() as db:
        job = _running_job(db, chat_session_id=999)
        db.commit()
        context = _context(job)

    result = handler.handle(context)

    assert result.status == "succeeded"
    assert result.result_json == {"result": "no_op", "chat_session_id": 999}


def test_cleanup_noop_for_non_temporary_session(
    session_factory: sessionmaker[Session],
) -> None:
    handler = TemporaryChatCleanupHandler(session_factory=session_factory)
    with session_factory() as db:
        session = _temporary_session(db, ttl_expires_at=None, temporary_flag=False)
        job = _running_job(db, chat_session_id=session.chat_session_id)
        db.commit()
        context = _context(job)
        chat_session_id = session.chat_session_id

    result = handler.handle(context)

    assert result.status == "succeeded"
    assert result.result_json == {"result": "no_op", "chat_session_id": chat_session_id}
    with session_factory() as db:
        assert db.get(ChatSession, chat_session_id) is not None


def test_cleanup_raises_when_lease_lost(
    session_factory: sessionmaker[Session],
) -> None:
    handler = TemporaryChatCleanupHandler(session_factory=session_factory)
    with session_factory() as db:
        session = _temporary_session(db, ttl_expires_at=datetime.now(UTC) - timedelta(minutes=1))
        job = _running_job(db, chat_session_id=session.chat_session_id, locked_by="worker-2")
        db.commit()
        context = _context(job)
        chat_session_id = session.chat_session_id

    with pytest.raises(LeaseLostError):
        handler.handle(context)

    with session_factory() as db:
        assert db.get(ChatSession, chat_session_id) is not None


def test_cleanup_is_idempotent_on_second_run(
    session_factory: sessionmaker[Session],
) -> None:
    handler = TemporaryChatCleanupHandler(session_factory=session_factory)
    with session_factory() as db:
        session = _temporary_session(db, ttl_expires_at=datetime.now(UTC) - timedelta(minutes=1))
        job = _running_job(db, chat_session_id=session.chat_session_id)
        db.commit()
        context = _context(job)
        chat_session_id = session.chat_session_id

    first = handler.handle(context)
    second = handler.handle(context)

    assert first.status == "succeeded"
    assert first.result_json["result"] == "deleted"
    assert second.status == "succeeded"
    assert second.result_json == {"result": "no_op", "chat_session_id": chat_session_id}


def test_cleanup_resolves_session_id_from_payload(
    session_factory: sessionmaker[Session],
) -> None:
    handler = TemporaryChatCleanupHandler(session_factory=session_factory)
    with session_factory() as db:
        session = _temporary_session(db, ttl_expires_at=datetime.now(UTC) - timedelta(minutes=1))
        job = _running_job(db, chat_session_id=session.chat_session_id)
        db.commit()
        chat_session_id = session.chat_session_id
        context = JobExecutionContext(
            job_id=job.job_id,
            job_type="temporary_chat_cleanup",
            target_type="chat_session",
            target_id=None,
            payload={"chat_session_id": chat_session_id},
            worker_instance_id=WORKER_ID,
        )

    result = handler.handle(context)

    assert result.status == "succeeded"
    assert result.result_json["result"] == "deleted"
    with session_factory() as db:
        assert db.get(ChatSession, chat_session_id) is None


def test_cleanup_rejects_invalid_target(
    session_factory: sessionmaker[Session],
) -> None:
    handler = TemporaryChatCleanupHandler(session_factory=session_factory)
    wrong_type = JobExecutionContext(
        job_id=1,
        job_type="temporary_chat_cleanup",
        target_type="chat_message",
        target_id=1,
        payload={},
        worker_instance_id=WORKER_ID,
    )
    missing_id = JobExecutionContext(
        job_id=1,
        job_type="temporary_chat_cleanup",
        target_type="chat_session",
        target_id=None,
        payload={},
        worker_instance_id=WORKER_ID,
    )

    for context in (wrong_type, missing_id):
        result = handler.handle(context)
        assert result.status == "failed"
        assert result.error_code == "validation_error"


def test_cleanup_failure_rolls_back_and_reports_cleanup_failed(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_delete_session(self: ChatRepository, db: Session, *, session: ChatSession) -> None:
        raise RuntimeError("delete failed")

    monkeypatch.setattr(ChatRepository, "delete_session", failing_delete_session)
    handler = TemporaryChatCleanupHandler(session_factory=session_factory)
    with session_factory() as db:
        session = _temporary_session(db, ttl_expires_at=datetime.now(UTC) - timedelta(minutes=1))
        db.add(
            ChatMessage(
                chat_session_id=session.chat_session_id,
                role="user",
                content="cleanup request",
                client_message_id="cleanup-fail-1",
            )
        )
        job = _running_job(db, chat_session_id=session.chat_session_id)
        db.commit()
        context = _context(job)
        chat_session_id = session.chat_session_id

    result = handler.handle(context)

    assert result.status == "failed"
    assert result.error_code == "cleanup_failed"
    with session_factory() as db:
        assert db.get(ChatSession, chat_session_id) is not None
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 1
        assert db.query(AuditLog).filter_by(action_type="temporary_chat_cleanup").count() == 0
