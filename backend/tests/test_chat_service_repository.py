from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.errors import (
    ArchivedSessionReadonly,
    ResourceNotFound,
    TemporarySessionExpired,
    TemporarySessionNotArchivable,
    ValidationFailed,
)
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import (
    AuditLog,
    ChatMessage,
    ChatSession,
    ChatTag,
    RetrievalRun,
    Role,
    SummaryMemory,
    SystemSetting,
    User,
)
from app.rag.strategy import RetrievalStrategy
from app.repositories.chat_repository import ChatRepository
from app.repositories.retrieval_repository import CitationRecord, RetrievalRepository
from app.schemas.common import PaginationParams
from app.services.chat_service import ChatService


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
        admin_role = Role(role_name="admin", description="Admin")
        viewer_role = Role(role_name="viewer", description="Viewer")
        db.add_all([admin_role, viewer_role])
        db.flush()
        db.add_all(
            [
                User(
                    role_id=admin_role.role_id,
                    email="admin@example.com",
                    display_name="Admin",
                    password_hash=hash_password("password"),
                    status="active",
                ),
                User(
                    role_id=viewer_role.role_id,
                    email="viewer@example.com",
                    display_name="Viewer",
                    password_hash=hash_password("password"),
                    status="active",
                ),
            ]
        )
        db.add(
            SystemSetting(
                setting_key="chat.temporary_ttl_minutes",
                setting_value={"value": 45},
                description="Test TTL.",
            )
        )
        db.commit()
    try:
        yield factory
    finally:
        engine.dispose()


def users(db: Session) -> tuple[User, User]:
    admin = db.scalar(select(User).where(User.email == "admin@example.com"))
    viewer = db.scalar(select(User).where(User.email == "viewer@example.com"))
    assert admin is not None
    assert viewer is not None
    return admin, viewer


class RecordingRetrievalRepository(RetrievalRepository):
    def __init__(self) -> None:
        self.run_calls: list[list[int]] = []
        self.citation_calls: list[list[int]] = []

    def get_runs_by_ids(
        self,
        db: Session,
        *,
        retrieval_run_ids: list[int],
    ) -> dict[int, RetrievalRun]:
        self.run_calls.append(list(retrieval_run_ids))
        return {}

    def list_citations_for_runs(
        self,
        db: Session,
        *,
        retrieval_run_ids: list[int],
    ) -> dict[int, list[CitationRecord]]:
        self.citation_calls.append(list(retrieval_run_ids))
        return {}


def test_chat_service_create_session_title_ttl_and_validation(
    session_factory: sessionmaker[Session],
) -> None:
    service = ChatService()
    with session_factory() as db:
        _, viewer = users(db)

        normal = service.create_session(
            db,
            user=viewer,
            title=None,
            temporary_flag=False,
            request_id="chat-create-1",
        )
        assert normal.title == "新しい会話"
        assert normal.temporary_flag is False
        assert normal.ttl_expires_at is None
        assert normal.display_status == "active"

        before = datetime.now(UTC) + timedelta(minutes=44)
        temporary = service.create_session(
            db,
            user=viewer,
            title="  scratch  ",
            temporary_flag=True,
            request_id="chat-create-2",
        )
        after = datetime.now(UTC) + timedelta(minutes=46)
        assert temporary.title == "scratch"
        assert temporary.temporary_flag is True
        assert temporary.ttl_expires_at is not None
        assert before <= temporary.ttl_expires_at <= after
        assert temporary.display_status == "temporary"

        with pytest.raises(ValidationFailed):
            service.create_session(db, user=viewer, title="   ", temporary_flag=False)


def test_chat_service_owner_readonly_archive_tags_and_display_status(
    session_factory: sessionmaker[Session],
) -> None:
    service = ChatService()
    with session_factory() as db:
        admin, viewer = users(db)
        session = service.create_session(
            db,
            user=viewer,
            title="Design",
            temporary_flag=False,
            request_id="chat-create",
        )

        with pytest.raises(ResourceNotFound):
            service.get_session_detail(
                db,
                user=admin,
                chat_session_id=session.chat_session_id,
            )

        tag, created = service.add_tag(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            tag_name="  設計  ",
            request_id="tag-1",
        )
        assert created is True
        assert tag.result_code == "created"

        duplicate, duplicate_created = service.add_tag(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            tag_name="設計",
            request_id="tag-2",
        )
        assert duplicate_created is False
        assert duplicate.result_code == "already_exists"

        deleted = service.delete_tag(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            tag_name="設計",
            request_id="tag-3",
        )
        assert deleted.result_code == "deleted"
        missing = service.delete_tag(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            tag_name="設計",
            request_id="tag-4",
        )
        assert missing.result_code == "not_found_no_op"

        archived = service.archive_session(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            request_id="archive-1",
        )
        assert archived.result_code == "archived"
        archived_again = service.archive_session(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            request_id="archive-2",
        )
        assert archived_again.result_code == "already_archived"

        with pytest.raises(ArchivedSessionReadonly):
            service.update_session_title(
                db,
                user=viewer,
                chat_session_id=session.chat_session_id,
                title="readonly",
                request_id="title-readonly",
            )


def test_chat_service_temporary_expired_is_readonly_and_not_archivable(
    session_factory: sessionmaker[Session],
) -> None:
    service = ChatService()
    with session_factory() as db:
        _, viewer = users(db)
        session = service.create_session(
            db,
            user=viewer,
            title="temp",
            temporary_flag=True,
            request_id="temp",
        )
        row = db.get(ChatSession, session.chat_session_id)
        assert row is not None
        row.ttl_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

        detail = service.get_session_detail(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
        )
        assert detail.display_status == "temporary_expired"
        assert detail.mode == "temporary_expired"

        with pytest.raises(TemporarySessionExpired):
            service.update_session_title(
                db,
                user=viewer,
                chat_session_id=session.chat_session_id,
                title="expired",
                request_id="expired-title",
            )
        with pytest.raises(TemporarySessionExpired):
            service.add_tag(
                db,
                user=viewer,
                chat_session_id=session.chat_session_id,
                tag_name="x",
                request_id="expired-tag",
            )
        with pytest.raises(TemporarySessionNotArchivable):
            service.archive_session(
                db,
                user=viewer,
                chat_session_id=session.chat_session_id,
                request_id="expired-archive",
            )


def test_chat_service_delete_session_removes_messages_memory_and_retrieval_runs(
    session_factory: sessionmaker[Session],
) -> None:
    service = ChatService()
    with session_factory() as db:
        _, viewer = users(db)
        session = service.create_session(
            db,
            user=viewer,
            title="delete target",
            temporary_flag=False,
            request_id="delete-create",
        )
        chat_session_id = session.chat_session_id
        now = datetime.now(UTC)
        user_message = ChatMessage(
            chat_session_id=chat_session_id,
            role="user",
            content="delete request",
            client_message_id="delete-1",
        )
        db.add(user_message)
        db.flush()
        retrieval_run = RetrievalRun(
            chat_session_id=chat_session_id,
            request_message_id=user_message.chat_message_id,
            status="succeeded",
            started_at=now,
            finished_at=now,
            top_k=1,
        )
        db.add(retrieval_run)
        db.flush()
        db.add_all(
            [
                ChatMessage(
                    chat_session_id=chat_session_id,
                    role="assistant",
                    content="delete answer",
                    linked_retrieval_run_id=retrieval_run.retrieval_run_id,
                ),
                ChatTag(chat_session_id=chat_session_id, tag_name="delete-test"),
                SummaryMemory(
                    chat_session_id=chat_session_id,
                    source_message_upto_id=user_message.chat_message_id,
                    summary_text="delete summary",
                ),
            ]
        )
        db.commit()

        deleted = service.delete_session(
            db,
            user=viewer,
            chat_session_id=chat_session_id,
            request_id="delete-1",
        )

        assert deleted.result_code == "deleted"
        assert db.get(ChatSession, chat_session_id) is None
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.query(ChatTag).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.query(SummaryMemory).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.get(RetrievalRun, retrieval_run.retrieval_run_id) is None
        assert db.query(AuditLog).filter_by(action_type="chat.deleted").count() == 1


def test_chat_service_list_messages_batches_retrieval_metadata(
    session_factory: sessionmaker[Session],
) -> None:
    retrieval_repository = RecordingRetrievalRepository()
    service = ChatService(retrieval_repository=retrieval_repository)
    with session_factory() as db:
        _, viewer = users(db)
        session = service.create_session(
            db,
            user=viewer,
            title="batched metadata",
            temporary_flag=False,
            request_id="batch-create",
        )
        request_one = ChatMessage(
            chat_session_id=session.chat_session_id,
            role="user",
            content="question one",
            client_message_id="batch-1",
        )
        request_two = ChatMessage(
            chat_session_id=session.chat_session_id,
            role="user",
            content="question two",
            client_message_id="batch-2",
        )
        db.add_all([request_one, request_two])
        db.flush()
        now = datetime.now(UTC)
        run_one = RetrievalRun(
            chat_session_id=session.chat_session_id,
            request_message_id=request_one.chat_message_id,
            status="succeeded",
            started_at=now,
            finished_at=now,
            top_k=1,
        )
        run_two = RetrievalRun(
            chat_session_id=session.chat_session_id,
            request_message_id=request_two.chat_message_id,
            status="succeeded",
            started_at=now,
            finished_at=now,
            top_k=1,
        )
        db.add_all([run_one, run_two])
        db.flush()
        run_one_id = run_one.retrieval_run_id
        run_two_id = run_two.retrieval_run_id
        db.add_all(
            [
                ChatMessage(
                    chat_session_id=session.chat_session_id,
                    role="assistant",
                    content="answer one",
                    linked_retrieval_run_id=run_one_id,
                ),
                ChatMessage(
                    chat_session_id=session.chat_session_id,
                    role="assistant",
                    content="answer two",
                    linked_retrieval_run_id=run_two_id,
                ),
            ]
        )
        db.commit()

        messages, meta = service.list_messages(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            pagination=PaginationParams(page=1, page_size=20),
        )

    assert meta.total == 4
    assert [message.role for message in messages] == ["user", "user", "assistant", "assistant"]
    expected_run_ids = [run_one_id, run_two_id]
    assert retrieval_repository.run_calls == [expected_run_ids]
    assert retrieval_repository.citation_calls == [expected_run_ids]


def test_chat_service_list_messages_restores_graph_retrieval_summary(
    session_factory: sessionmaker[Session],
) -> None:
    service = ChatService()
    with session_factory() as db:
        _, viewer = users(db)
        session = service.create_session(
            db,
            user=viewer,
            title="graph metadata",
            temporary_flag=False,
            request_id="graph-summary-create",
        )
        success_request = ChatMessage(
            chat_session_id=session.chat_session_id,
            role="user",
            content="graph success question",
            client_message_id="graph-success-1",
        )
        fallback_request = ChatMessage(
            chat_session_id=session.chat_session_id,
            role="user",
            content="graph fallback question",
            client_message_id="graph-fallback-1",
        )
        db.add_all([success_request, fallback_request])
        db.flush()
        now = datetime.now(UTC)
        success_run = RetrievalRun(
            chat_session_id=session.chat_session_id,
            request_message_id=success_request.chat_message_id,
            status="succeeded",
            started_at=now,
            finished_at=now,
            top_k=1,
            strategy_type=RetrievalStrategy.GRAPH.value,
            strategy_decision_json={
                "selected_strategy": "graph_postgres",
                "execution_strategy": "graph",
                "fallback_used": False,
                "graph_store_provider": "postgres",
            },
            retrieval_score_summary={
                "graph_store_provider": "postgres",
                "graph_reason_codes": ["graph_search_completed"],
                "graph_fallback_used": False,
            },
        )
        fallback_run = RetrievalRun(
            chat_session_id=session.chat_session_id,
            request_message_id=fallback_request.chat_message_id,
            status="succeeded",
            started_at=now,
            finished_at=now,
            top_k=1,
            strategy_type=RetrievalStrategy.GRAPH.value,
            strategy_decision_json={
                "selected_strategy": "graph_neo4j",
                "execution_strategy": "hybrid",
                "fallback_used": False,
                "graph_requested_provider": "neo4j",
            },
            retrieval_score_summary={
                "fallback_used": True,
                "fallback_reason": "graph_no_evidence_fallback",
                "graph_store_provider": "postgres",
                "graph_reason_codes": [
                    "neo4j_connection_failed",
                    "graph_no_evidence_fallback",
                    "graph_fallback_dense",
                ],
            },
        )
        db.add_all([success_run, fallback_run])
        db.flush()
        db.add_all(
            [
                ChatMessage(
                    chat_session_id=session.chat_session_id,
                    role="assistant",
                    content="graph success answer",
                    linked_retrieval_run_id=success_run.retrieval_run_id,
                ),
                ChatMessage(
                    chat_session_id=session.chat_session_id,
                    role="assistant",
                    content="graph fallback answer",
                    linked_retrieval_run_id=fallback_run.retrieval_run_id,
                ),
            ]
        )
        db.commit()

        messages, _ = service.list_messages(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            pagination=PaginationParams(page=1, page_size=20),
        )

    summaries = [message.retrieval_summary for message in messages if message.role == "assistant"]
    assert len(summaries) == 2
    success_summary = summaries[0]
    fallback_summary = summaries[1]
    assert success_summary is not None
    assert success_summary.graph_store_provider == "postgres"
    assert success_summary.fallback_used is False
    assert success_summary.fallback_reason is None
    assert success_summary.graph_fallback_reason_codes == []
    assert fallback_summary is not None
    assert fallback_summary.graph_requested_provider == "neo4j"
    assert fallback_summary.graph_store_provider == "postgres"
    assert fallback_summary.fallback_used is True
    assert fallback_summary.fallback_reason == "neo4j_connection_failed"
    assert "neo4j_connection_failed" in fallback_summary.graph_fallback_reason_codes
    assert "graph_no_evidence_fallback" in fallback_summary.graph_fallback_reason_codes


def test_chat_repository_filters_pagination_messages_tags_and_archive(
    session_factory: sessionmaker[Session],
) -> None:
    repository = ChatRepository()
    with session_factory() as db:
        admin, viewer = users(db)
        now = datetime.now(UTC)
        own = ChatSession(user_id=viewer.user_id, title="Alpha design", status="active")
        archived = ChatSession(
            user_id=viewer.user_id,
            title="Alpha archived",
            status="archived",
            archived_at=now,
        )
        temporary = ChatSession(
            user_id=viewer.user_id,
            title="Alpha temporary",
            status="active",
            temporary_flag=True,
            ttl_expires_at=now + timedelta(minutes=10),
        )
        other = ChatSession(user_id=admin.user_id, title="Alpha other", status="active")
        db.add_all([own, archived, temporary, other])
        db.flush()
        db.add_all(
            [
                ChatMessage(chat_session_id=own.chat_session_id, role="user", content="hello"),
                ChatMessage(
                    chat_session_id=own.chat_session_id,
                    role="assistant",
                    content="answer",
                ),
            ]
        )
        db.commit()

        rows, total = repository.list_sessions_for_user(
            db,
            user_id=viewer.user_id,
            status="active",
            query="Alpha",
            pagination=PaginationParams(page=1, page_size=20),
        )
        assert total == 1
        assert [row.chat_session_id for row in rows] == [own.chat_session_id]

        archived_rows, archived_total = repository.list_sessions_for_user(
            db,
            user_id=viewer.user_id,
            status="archived",
            query=None,
            pagination=PaginationParams(page=1, page_size=20),
        )
        assert archived_total == 1
        assert archived_rows[0].chat_session_id == archived.chat_session_id

        messages, message_total = repository.list_messages(
            db,
            chat_session_id=own.chat_session_id,
            pagination=PaginationParams(),
        )
        assert message_total == 2
        assert [message.role for message in messages] == ["user", "assistant"]

        tag = repository.create_tag(
            db,
            chat_session_id=own.chat_session_id,
            tag_name="設計",
        )
        db.commit()
        assert tag is not None
        with pytest.raises(IntegrityError):
            repository.create_tag(
                db,
                chat_session_id=own.chat_session_id,
                tag_name="設計",
            )
        db.rollback()
        assert repository.list_tags(db, chat_session_id=own.chat_session_id)[0].tag_name == "設計"
        assert repository.delete_tag(
            db,
            chat_session_id=own.chat_session_id,
            tag_name="設計",
        )
        assert (
            repository.delete_tag(
                db,
                chat_session_id=own.chat_session_id,
                tag_name="設計",
            )
            is None
        )

        before_archive_update = own.updated_at
        archived_session = repository.archive_session(
            db,
            session=own,
            archived_at=now,
        )
        db.commit()
        assert archived_session.status == "archived"
        assert archived_session.archived_at == now
        assert archived_session.updated_at != before_archive_update


def test_chat_service_noop_operations_do_not_duplicate_side_effects(
    session_factory: sessionmaker[Session],
) -> None:
    service = ChatService()
    with session_factory() as db:
        _, viewer = users(db)
        session = service.create_session(
            db,
            user=viewer,
            title="No-op",
            temporary_flag=False,
            request_id="noop-create",
        )
        tag, created = service.add_tag(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            tag_name="secret-looking-tag",
            request_id="noop-tag-1",
        )
        assert created is True
        after_create = db.get(ChatSession, session.chat_session_id)
        assert after_create is not None
        updated_at_after_create = after_create.updated_at

        duplicate, duplicate_created = service.add_tag(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            tag_name="secret-looking-tag",
            request_id="noop-tag-2",
        )
        assert duplicate_created is False
        assert duplicate.result_code == "already_exists"
        assert db.query(ChatTag).filter_by(chat_session_id=session.chat_session_id).count() == 1
        after_duplicate = db.get(ChatSession, session.chat_session_id)
        assert after_duplicate is not None
        assert after_duplicate.updated_at == updated_at_after_create

        tag_audit = db.scalar(select(AuditLog).where(AuditLog.action_type == "chat.tag_added"))
        assert tag_audit is not None
        assert tag_audit.metadata_json is not None
        assert "tag_name" not in tag_audit.metadata_json
        assert tag_audit.metadata_json["tag_name_hash"]
        assert tag.result_code == "created"

        archived = service.archive_session(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            request_id="noop-archive-1",
        )
        assert archived.result_code == "archived"
        after_archive = db.get(ChatSession, session.chat_session_id)
        assert after_archive is not None
        archived_at = after_archive.archived_at
        updated_at = after_archive.updated_at

        already_archived = service.archive_session(
            db,
            user=viewer,
            chat_session_id=session.chat_session_id,
            request_id="noop-archive-2",
        )
        assert already_archived.result_code == "already_archived"
        after_noop_archive = db.get(ChatSession, session.chat_session_id)
        assert after_noop_archive is not None
        assert after_noop_archive.archived_at == archived_at
        assert after_noop_archive.updated_at == updated_at
        assert db.query(AuditLog).filter_by(action_type="chat.archived").count() == 1
