from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.db.models import (
    ChatMessage,
    ChatSession,
    ChatTag,
    Citation,
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    Role,
    SystemSetting,
    User,
)
from app.db.session import get_db
from app.main import create_app

ALLOWED_ORIGIN = "http://localhost:5173"
TEST_PASSWORD = "password"


@pytest.fixture
def chat_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        admin_role = Role(role_name="admin", description="Admin")
        viewer_role = Role(role_name="viewer", description="Viewer")
        db.add_all([admin_role, viewer_role])
        db.flush()
        password_hash = hash_password(TEST_PASSWORD)
        db.add_all(
            [
                User(
                    role_id=admin_role.role_id,
                    email="admin@example.com",
                    display_name="Admin",
                    password_hash=password_hash,
                    status="active",
                ),
                User(
                    role_id=viewer_role.role_id,
                    email="viewer@example.com",
                    display_name="Viewer",
                    password_hash=password_hash,
                    status="active",
                ),
            ]
        )
        db.add(
            SystemSetting(
                setting_key="chat.temporary_ttl_minutes",
                setting_value={"value": 30},
                description="Test TTL.",
            )
        )
        db.commit()

    def override_db() -> Iterator[Session]:
        with session_factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app), session_factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def login(client: TestClient, email: str = "viewer@example.com") -> str:
    csrf_response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert csrf_response.status_code == 200
    pre_auth_csrf = csrf_response.json()["data"]["csrf_token"]
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": pre_auth_csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def issue_csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def unsafe_headers(csrf_token: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN}


def assert_no_secret_fields(payload: Any) -> None:
    serialized = str(payload).lower()
    assert "password" not in serialized
    assert "password_hash" not in serialized
    assert "session_token" not in serialized
    assert "csrf_token" not in serialized
    assert "rag_session" not in serialized


def test_chat_api_requires_auth_and_csrf_only_for_unsafe_methods(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = chat_client

    unauthenticated_get = client.get("/api/v1/chat/sessions")
    assert unauthenticated_get.status_code == 401
    assert unauthenticated_get.json()["error"]["code"] == "auth_required"

    unauthenticated_post = client.post("/api/v1/chat/sessions", json={})
    assert unauthenticated_post.status_code == 401
    assert unauthenticated_post.json()["error"]["code"] == "auth_required"

    login(client)
    get_without_csrf = client.get("/api/v1/chat/sessions")
    assert get_without_csrf.status_code == 200

    post_without_csrf = client.post("/api/v1/chat/sessions", json={})
    assert post_without_csrf.status_code == 403
    assert post_without_csrf.json()["error"]["code"] == "csrf_missing"


def test_chat_api_csrf_required_for_all_mutating_routes(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = chat_client
    csrf_token = login(client)
    created = client.post(
        "/api/v1/chat/sessions",
        json={"title": "csrf target"},
        headers=unsafe_headers(csrf_token),
    )
    assert created.status_code == 201
    session_id = int(created.json()["data"]["chat_session_id"])

    requests = [
        client.patch(f"/api/v1/chat/sessions/{session_id}", json={"title": "missing"}),
        client.post(f"/api/v1/chat/sessions/{session_id}/archive"),
        client.delete(f"/api/v1/chat/sessions/{session_id}"),
        client.post(f"/api/v1/chat/sessions/{session_id}/tags", json={"tag_name": "x"}),
        client.delete(f"/api/v1/chat/sessions/{session_id}/tags/x"),
    ]
    for response in requests:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_missing"

    invalid = client.patch(
        f"/api/v1/chat/sessions/{session_id}",
        json={"title": "invalid csrf"},
        headers=unsafe_headers("csrf_invalid"),
    )
    assert invalid.status_code == 403
    assert invalid.json()["error"]["code"] == "csrf_invalid"


def test_chat_api_owner_mismatch_rejects_mutating_and_message_routes(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = chat_client
    login(client, email="viewer@example.com")
    with session_factory() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert admin is not None
        other = ChatSession(user_id=admin.user_id, title="Other user", status="active")
        db.add(other)
        db.flush()
        db.add(ChatMessage(chat_session_id=other.chat_session_id, role="user", content="hidden"))
        db.commit()
        other_id = other.chat_session_id

    csrf_token = issue_csrf(client)
    checks = [
        client.get(f"/api/v1/chat/sessions/{other_id}/messages"),
        client.patch(
            f"/api/v1/chat/sessions/{other_id}",
            json={"title": "nope"},
            headers=unsafe_headers(csrf_token),
        ),
        client.post(
            f"/api/v1/chat/sessions/{other_id}/archive",
            headers=unsafe_headers(issue_csrf(client)),
        ),
        client.delete(
            f"/api/v1/chat/sessions/{other_id}",
            headers=unsafe_headers(issue_csrf(client)),
        ),
        client.post(
            f"/api/v1/chat/sessions/{other_id}/tags",
            json={"tag_name": "x"},
            headers=unsafe_headers(issue_csrf(client)),
        ),
        client.delete(
            f"/api/v1/chat/sessions/{other_id}/tags/x",
            headers=unsafe_headers(issue_csrf(client)),
        ),
    ]
    for response in checks:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "resource_not_found"


def test_chat_api_sessions_pagination_status_and_query_filters(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = chat_client
    login(client, email="viewer@example.com")
    with session_factory() as db:
        viewer = db.scalar(select(User).where(User.email == "viewer@example.com"))
        assert viewer is not None
        now = datetime.now(UTC)
        db.add_all(
            [
                ChatSession(user_id=viewer.user_id, title="Alpha older", updated_at=now),
                ChatSession(
                    user_id=viewer.user_id,
                    title="Alpha newer",
                    updated_at=now + timedelta(minutes=1),
                ),
                ChatSession(user_id=viewer.user_id, title="Beta active", updated_at=now),
                ChatSession(
                    user_id=viewer.user_id,
                    title="Alpha archived",
                    status="archived",
                    archived_at=now,
                    updated_at=now + timedelta(minutes=2),
                ),
                ChatSession(
                    user_id=viewer.user_id,
                    title="Alpha temporary",
                    temporary_flag=True,
                    ttl_expires_at=now + timedelta(minutes=30),
                    updated_at=now + timedelta(minutes=3),
                ),
            ]
        )
        db.commit()

    first_page = client.get("/api/v1/chat/sessions?status=active&q=Alpha&page=1&page_size=1")
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert first_body["meta"]["pagination"] == {
        "page": 1,
        "page_size": 1,
        "total": 2,
        "has_next": True,
    }
    assert first_body["data"][0]["title"] == "Alpha newer"

    second_page = client.get("/api/v1/chat/sessions?status=active&q=Alpha&page=2&page_size=1")
    assert second_page.status_code == 200
    assert second_page.json()["data"][0]["title"] == "Alpha older"
    assert second_page.json()["meta"]["pagination"]["has_next"] is False

    archived = client.get("/api/v1/chat/sessions?status=archived&q=Alpha")
    assert archived.status_code == 200
    assert [item["title"] for item in archived.json()["data"]] == ["Alpha archived"]


def test_chat_api_sessions_messages_tags_archive_contract(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = chat_client
    csrf_token = login(client, email="viewer@example.com")

    create = client.post(
        "/api/v1/chat/sessions",
        json={},
        headers=unsafe_headers(csrf_token),
    )
    assert create.status_code == 201
    created = create.json()
    assert created["data"]["title"] == "新しい会話"
    assert created["data"]["status"] == "active"
    assert created["data"]["temporary_flag"] is False
    assert created["data"]["ttl_expires_at"] is None
    assert_no_secret_fields(created)
    session_id = int(created["data"]["chat_session_id"])

    temporary = client.post(
        "/api/v1/chat/sessions",
        json={"title": " temporary ", "temporary_flag": True},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert temporary.status_code == 201
    assert temporary.json()["data"]["temporary_flag"] is True
    assert temporary.json()["data"]["ttl_expires_at"] is not None
    temporary_id = int(temporary.json()["data"]["chat_session_id"])

    with session_factory() as db:
        viewer = db.scalar(select(User).where(User.email == "viewer@example.com"))
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert viewer is not None
        assert admin is not None
        other = ChatSession(user_id=admin.user_id, title="Other user", status="active")
        db.add(other)
        db.flush()
        db.add_all(
            [
                ChatMessage(
                    chat_session_id=session_id,
                    role="user",
                    content="message body for response only",
                    client_message_id="msg-1",
                ),
                ChatMessage(
                    chat_session_id=session_id,
                    role="assistant",
                    content="assistant response",
                ),
            ]
        )
        db.commit()
        other_id = other.chat_session_id

    list_response = client.get("/api/v1/chat/sessions?page=1&page_size=20")
    assert list_response.status_code == 200
    list_body = list_response.json()
    returned_ids = {item["chat_session_id"] for item in list_body["data"]}
    assert session_id in returned_ids
    assert temporary_id not in returned_ids
    assert other_id not in returned_ids
    assert list_body["meta"]["pagination"]["total"] == 1

    detail = client.get(f"/api/v1/chat/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["mode"] == "active"

    other_detail = client.get(f"/api/v1/chat/sessions/{other_id}")
    assert other_detail.status_code == 404
    assert other_detail.json()["error"]["code"] == "resource_not_found"

    updated = client.patch(
        f"/api/v1/chat/sessions/{session_id}",
        json={"title": " 設計レビュー相談 "},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["title"] == "設計レビュー相談"

    tag = client.post(
        f"/api/v1/chat/sessions/{session_id}/tags",
        json={"tag_name": " 設計 "},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert tag.status_code == 201
    assert tag.json()["data"]["result_code"] == "created"
    duplicate_tag = client.post(
        f"/api/v1/chat/sessions/{session_id}/tags",
        json={"tag_name": "設計"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert duplicate_tag.status_code == 200
    assert duplicate_tag.json()["data"]["result_code"] == "already_exists"

    delete_tag = client.delete(
        f"/api/v1/chat/sessions/{session_id}/tags/%E8%A8%AD%E8%A8%88",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert delete_tag.status_code == 200
    assert delete_tag.json()["data"]["result_code"] == "deleted"
    missing_tag = client.delete(
        f"/api/v1/chat/sessions/{session_id}/tags/%E8%A8%AD%E8%A8%88",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert missing_tag.status_code == 200
    assert missing_tag.json()["data"]["result_code"] == "not_found_no_op"

    messages = client.get(f"/api/v1/chat/sessions/{session_id}/messages")
    assert messages.status_code == 200
    message_body = messages.json()
    assert len(message_body["data"]) == 2
    assert "linked_retrieval_run_id" not in message_body["data"][0]
    assert message_body["data"][0]["content"] == "message body for response only"
    assert message_body["meta"]["pagination"]["total"] == 2
    assert_no_secret_fields(message_body)

    viewer_lineage = client.get(
        f"/api/v1/chat/sessions/{session_id}/messages?include_internal_lineage=true"
    )
    assert viewer_lineage.status_code == 403
    assert viewer_lineage.json()["error"]["code"] == "permission_denied"

    archived = client.post(
        f"/api/v1/chat/sessions/{session_id}/archive",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archived.status_code == 200
    assert archived.json()["data"]["result_code"] == "archived"
    archived_again = client.post(
        f"/api/v1/chat/sessions/{session_id}/archive",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archived_again.status_code == 200
    assert archived_again.json()["data"]["result_code"] == "already_archived"

    archived_update = client.patch(
        f"/api/v1/chat/sessions/{session_id}",
        json={"title": "readonly"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archived_update.status_code == 409
    assert archived_update.json()["error"]["code"] == "archived_session_readonly"

    archived_tag = client.post(
        f"/api/v1/chat/sessions/{session_id}/tags",
        json={"tag_name": "readonly"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archived_tag.status_code == 409
    assert archived_tag.json()["error"]["code"] == "archived_session_readonly"
    archived_delete_tag = client.delete(
        f"/api/v1/chat/sessions/{session_id}/tags/readonly",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archived_delete_tag.status_code == 409
    assert archived_delete_tag.json()["error"]["code"] == "archived_session_readonly"

    temp_archive = client.post(
        f"/api/v1/chat/sessions/{temporary_id}/archive",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert temp_archive.status_code == 409
    assert temp_archive.json()["error"]["code"] == "temporary_session_not_archivable"


def test_chat_api_delete_session_removes_owned_chat_from_postgres(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = chat_client
    csrf_token = login(client, email="viewer@example.com")
    created = client.post(
        "/api/v1/chat/sessions",
        json={"title": "delete target"},
        headers=unsafe_headers(csrf_token),
    )
    assert created.status_code == 201
    session_id = int(created.json()["data"]["chat_session_id"])

    with session_factory() as db:
        db.add_all(
            [
                ChatMessage(chat_session_id=session_id, role="user", content="remove me"),
                ChatTag(chat_session_id=session_id, tag_name="delete-test"),
            ]
        )
        db.commit()

    deleted = client.delete(
        f"/api/v1/chat/sessions/{session_id}",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["result_code"] == "deleted"

    missing = client.get(f"/api/v1/chat/sessions/{session_id}")
    assert missing.status_code == 404
    with session_factory() as db:
        assert db.get(ChatSession, session_id) is None
        assert db.query(ChatMessage).filter_by(chat_session_id=session_id).count() == 0
        assert db.query(ChatTag).filter_by(chat_session_id=session_id).count() == 0


def test_chat_messages_include_persisted_rag_citations_and_confidence(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = chat_client
    csrf_token = login(client, email="viewer@example.com")
    created = client.post(
        "/api/v1/chat/sessions",
        json={"title": "citation history"},
        headers=unsafe_headers(csrf_token),
    )
    assert created.status_code == 201
    session_id = int(created.json()["data"]["chat_session_id"])

    with session_factory() as db:
        viewer = db.scalar(select(User).where(User.email == "viewer@example.com"))
        assert viewer is not None
        document = LogicalDocument(
            owner_user_id=viewer.user_id,
            title="Phase1 seed",
            status="active",
        )
        db.add(document)
        db.flush()
        version = DocumentVersion(
            logical_document_id=document.logical_document_id,
            version_no=1,
            content_hash="a" * 64,
            status="ready",
            is_active=True,
            file_name="phase1-seed.md",
            mime_type="text/markdown",
            file_size_bytes=128,
            created_by=viewer.user_id,
        )
        db.add(version)
        db.flush()
        chunk = DocumentChunk(
            document_version_id=version.document_version_id,
            chunk_index=0,
            chunk_hash="b" * 64,
            content_text="Phase1 uses Qdrant as the vector database.",
            section_title="Architecture",
            modality="text",
        )
        db.add(chunk)
        db.flush()
        user_message = ChatMessage(
            chat_session_id=session_id,
            role="user",
            content="What vector database is used?",
            client_message_id="msg-citation-history",
        )
        db.add(user_message)
        db.flush()
        now = datetime.now(UTC)
        run = RetrievalRun(
            chat_session_id=session_id,
            request_message_id=user_message.chat_message_id,
            status="succeeded",
            started_at=now,
            finished_at=now,
            top_k=1,
            answer_confidence=Decimal("0.810000"),
            groundedness_score=Decimal("0.920000"),
            confidence_label="High",
        )
        db.add(run)
        db.flush()
        db.add(
            RetrievalRunItem(
                retrieval_run_id=run.retrieval_run_id,
                document_chunk_id=chunk.document_chunk_id,
                retrieval_score=Decimal("0.900000"),
                rerank_score=Decimal("0.950000"),
                rank_order=1,
                rerank_order=1,
                selected_flag=True,
                payload_snapshot={"source_label": "phase1-seed.md"},
            )
        )
        db.add(
            ChatMessage(
                chat_session_id=session_id,
                role="assistant",
                content="Phase1 uses Qdrant [1].",
                linked_retrieval_run_id=run.retrieval_run_id,
            )
        )
        db.add(
            Citation(
                retrieval_run_id=run.retrieval_run_id,
                document_chunk_id=chunk.document_chunk_id,
                snippet="Phase1 uses Qdrant as the vector database.",
                display_label="phase1-seed.md",
                rank_order=1,
            )
        )
        db.commit()

    messages = client.get(f"/api/v1/chat/sessions/{session_id}/messages")

    assert messages.status_code == 200
    body = messages.json()
    assistant = body["data"][1]
    assert "linked_retrieval_run_id" not in assistant
    assert assistant["confidence"]["confidence_label"] == "High"
    assert assistant["confidence"]["confidence_basis"] == "retrieval_signals"
    assert assistant["citations"][0]["source_label"] == "phase1-seed.md"
    assert assistant["citations"][0]["section_title"] == "Architecture"
    assert assistant["citations"][0]["old_version_flag"] is False


def test_chat_api_temporary_expired_readonly_and_admin_lineage(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = chat_client
    csrf_token = login(client, email="viewer@example.com")
    temporary = client.post(
        "/api/v1/chat/sessions",
        json={"temporary_flag": True},
        headers=unsafe_headers(csrf_token),
    )
    assert temporary.status_code == 201
    temporary_id = int(temporary.json()["data"]["chat_session_id"])

    with session_factory() as db:
        session = db.get(ChatSession, temporary_id)
        assert session is not None
        session.ttl_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.add(ChatMessage(chat_session_id=temporary_id, role="user", content="expired message"))
        db.commit()

    detail = client.get(f"/api/v1/chat/sessions/{temporary_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["display_status"] == "temporary_expired"
    messages = client.get(f"/api/v1/chat/sessions/{temporary_id}/messages")
    assert messages.status_code == 200
    assert messages.json()["data"][0]["content"] == "expired message"

    expired_update = client.patch(
        f"/api/v1/chat/sessions/{temporary_id}",
        json={"title": "expired"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert expired_update.status_code == 409
    assert expired_update.json()["error"]["code"] == "temporary_session_expired"

    expired_tag = client.post(
        f"/api/v1/chat/sessions/{temporary_id}/tags",
        json={"tag_name": "expired"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert expired_tag.status_code == 409
    assert expired_tag.json()["error"]["code"] == "temporary_session_expired"
    expired_delete_tag = client.delete(
        f"/api/v1/chat/sessions/{temporary_id}/tags/expired",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert expired_delete_tag.status_code == 409
    assert expired_delete_tag.json()["error"]["code"] == "temporary_session_expired"

    client.post(
        "/api/v1/auth/logout",
        headers=unsafe_headers(issue_csrf(client)),
    )
    admin_csrf = login(client, email="admin@example.com")
    admin_session = client.post(
        "/api/v1/chat/sessions",
        json={"title": "admin"},
        headers=unsafe_headers(admin_csrf),
    )
    admin_session_id = int(admin_session.json()["data"]["chat_session_id"])
    with session_factory() as db:
        db.add(ChatMessage(chat_session_id=admin_session_id, role="assistant", content="admin msg"))
        db.commit()

    admin_lineage = client.get(
        f"/api/v1/chat/sessions/{admin_session_id}/messages?include_internal_lineage=true"
    )
    assert admin_lineage.status_code == 200
    assert "linked_retrieval_run_id" in admin_lineage.json()["data"][0]


def test_rag_ask_cannot_append_to_foreign_archived_or_expired_sessions(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = chat_client
    csrf_token = login(client, email="viewer@example.com")
    active = client.post(
        "/api/v1/chat/sessions",
        json={"title": "rag target"},
        headers=unsafe_headers(csrf_token),
    )
    assert active.status_code == 201
    active_id = int(active.json()["data"]["chat_session_id"])

    archived = client.post(
        f"/api/v1/chat/sessions/{active_id}/archive",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archived.status_code == 200
    archived_ask = client.post(
        "/api/v1/rag/ask",
        json={"chat_session_id": active_id, "question": "should not append"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archived_ask.status_code == 409
    assert archived_ask.json()["error"]["code"] == "archived_session_readonly"

    with session_factory() as db:
        viewer = db.scalar(select(User).where(User.email == "viewer@example.com"))
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert viewer is not None
        assert admin is not None
        expired = ChatSession(
            user_id=viewer.user_id,
            title="expired rag target",
            temporary_flag=True,
            ttl_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        other = ChatSession(user_id=admin.user_id, title="other rag target")
        db.add_all([expired, other])
        db.commit()
        expired_id = expired.chat_session_id
        other_id = other.chat_session_id

    expired_ask = client.post(
        "/api/v1/rag/ask",
        json={"chat_session_id": expired_id, "question": "should not append"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert expired_ask.status_code == 409
    assert expired_ask.json()["error"]["code"] == "temporary_session_expired"

    other_ask = client.post(
        "/api/v1/rag/ask",
        json={"chat_session_id": other_id, "question": "should not append"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert other_ask.status_code == 404
    assert other_ask.json()["error"]["code"] == "resource_not_found"

    with session_factory() as db:
        assert db.query(ChatMessage).filter_by(chat_session_id=active_id).count() == 0
        assert db.query(ChatMessage).filter_by(chat_session_id=expired_id).count() == 0
        assert db.query(ChatMessage).filter_by(chat_session_id=other_id).count() == 0


def test_chat_api_validation_errors_for_title_tag_filters_and_pagination(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = chat_client
    csrf_token = login(client)

    empty_title = client.post(
        "/api/v1/chat/sessions",
        json={"title": "   "},
        headers=unsafe_headers(csrf_token),
    )
    assert empty_title.status_code == 422
    assert empty_title.json()["error"]["code"] == "validation_error"

    created = client.post(
        "/api/v1/chat/sessions",
        json={"title": "valid"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert created.status_code == 201
    session_id = int(created.json()["data"]["chat_session_id"])

    empty_tag = client.post(
        f"/api/v1/chat/sessions/{session_id}/tags",
        json={"tag_name": "   "},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert empty_tag.status_code == 422
    assert empty_tag.json()["error"]["code"] == "validation_error"

    slash_tag = client.post(
        f"/api/v1/chat/sessions/{session_id}/tags",
        json={"tag_name": "cannot/delete"},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert slash_tag.status_code == 422
    assert slash_tag.json()["error"]["code"] == "validation_error"

    invalid_status = client.get("/api/v1/chat/sessions?status=deleted")
    assert invalid_status.status_code == 422
    assert invalid_status.json()["error"]["code"] == "validation_error"

    invalid_page_size = client.get("/api/v1/chat/sessions?page_size=101")
    assert invalid_page_size.status_code == 422
    assert invalid_page_size.json()["error"]["code"] == "validation_error"
