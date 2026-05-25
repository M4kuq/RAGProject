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
from app.repositories.chat_repository import ChatRepository
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
