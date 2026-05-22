from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import ChatMessage, ChatSession, Role, SystemSetting, User
from app.db.session import get_db
from app.main import create_app

ALLOWED_ORIGIN = "http://localhost:5173"
TEST_PASSWORD = "password"


@pytest.fixture
def chat_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    get_settings.cache_clear()
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
        get_settings.cache_clear()
        engine.dispose()


def test_rag_ask_rejects_archived_expired_and_foreign_sessions_with_valid_payloads(
    chat_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = chat_client
    csrf_token = _login(client, email="viewer@example.com")
    active = client.post(
        "/api/v1/chat/sessions",
        json={"title": "rag target"},
        headers=_unsafe_headers(csrf_token),
    )
    assert active.status_code == 201
    active_id = int(active.json()["data"]["chat_session_id"])

    archived = client.post(
        f"/api/v1/chat/sessions/{active_id}/archive",
        headers=_unsafe_headers(_issue_csrf(client)),
    )
    assert archived.status_code == 200

    archived_ask = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": active_id,
            "client_message_id": "archived-ask",
            "message": "should not append",
        },
        headers=_unsafe_headers(_issue_csrf(client)),
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
        json={
            "chat_session_id": expired_id,
            "client_message_id": "expired-ask",
            "message": "should not append",
        },
        headers=_unsafe_headers(_issue_csrf(client)),
    )
    assert expired_ask.status_code == 409
    assert expired_ask.json()["error"]["code"] == "temporary_session_expired"

    other_ask = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": other_id,
            "client_message_id": "other-ask",
            "message": "should not append",
        },
        headers=_unsafe_headers(_issue_csrf(client)),
    )
    assert other_ask.status_code == 404
    assert other_ask.json()["error"]["code"] == "resource_not_found"

    with session_factory() as db:
        assert db.query(ChatMessage).filter_by(chat_session_id=active_id).count() == 0
        assert db.query(ChatMessage).filter_by(chat_session_id=expired_id).count() == 0
        assert db.query(ChatMessage).filter_by(chat_session_id=other_id).count() == 0


def _login(client: TestClient, *, email: str) -> str:
    csrf_token = _issue_csrf(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
        headers=_unsafe_headers(csrf_token),
    )
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def _issue_csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def _unsafe_headers(csrf_token: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN}
