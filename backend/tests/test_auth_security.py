from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import require_csrf
from app.core.config import Settings, get_settings
from app.core.csrf import make_pre_auth_state, new_csrf_token, verify_pre_auth_state
from app.core.errors import CsrfInvalid, PermissionDenied
from app.core.permissions import ensure_admin, ensure_viewer_or_admin
from app.core.security import (
    hash_password,
    hash_token,
    login_rate_limiter,
    new_token,
    verify_password,
    verify_token_hash,
)
from app.core.sessions import (
    client_ip,
    new_session_token,
    now_utc,
    truncate_user_agent,
)
from app.db.base import Base
from app.db.models import AuditLog, Role, User, UserSession
from app.db.session import get_db
from app.main import create_app
from app.services.audit_service import safe_metadata

ALLOWED_ORIGIN = "http://localhost:5173"
TEST_PASSWORD = "password"


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Iterator[None]:
    login_rate_limiter.reset_all()
    yield
    login_rate_limiter.reset_all()


@pytest.fixture
def auth_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    password_hash = hash_password(TEST_PASSWORD)
    with session_factory() as db:
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
                User(
                    role_id=viewer_role.role_id,
                    email="disabled@example.com",
                    display_name="Disabled",
                    password_hash=password_hash,
                    status="disabled",
                ),
            ]
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


def login_response(
    client: TestClient, email: str = "admin@example.com", password: str = TEST_PASSWORD
) -> Any:
    csrf_response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert csrf_response.status_code == 200
    csrf_token = csrf_response.json()["data"]["csrf_token"]
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200
    return response


def login_as(
    client: TestClient, email: str = "admin@example.com", password: str = TEST_PASSWORD
) -> dict[str, Any]:
    return login_response(client, email=email, password=password).json()


def issue_session_csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def test_password_and_token_helpers() -> None:
    password_hash = hash_password(TEST_PASSWORD)

    assert verify_password(TEST_PASSWORD, password_hash) is True
    assert verify_password("wrong-password", password_hash) is False
    assert verify_password(TEST_PASSWORD, "not-a-valid-hash") is False

    first = new_session_token()
    second = new_session_token()
    assert first.startswith("sess_")
    assert second.startswith("sess_")
    assert first != second
    assert len(first) >= 40
    assert hash_token(first) != first
    assert verify_token_hash(first, hash_token(first)) is True

    generic = new_token("csrf_", 32)
    assert generic.startswith("csrf_")


def test_pre_auth_csrf_signed_state_validation() -> None:
    raw_token = new_csrf_token()
    signed_state = make_pre_auth_state(raw_token)

    assert verify_pre_auth_state(raw_token, signed_state) is True
    with pytest.raises(CsrfInvalid):
        verify_pre_auth_state("csrf_wrong", signed_state)


def test_permissions_and_audit_metadata_redaction() -> None:
    assert ensure_admin("admin") == "admin"
    assert ensure_viewer_or_admin("viewer") == "viewer"
    with pytest.raises(PermissionDenied):
        ensure_admin("viewer")

    metadata = safe_metadata(
        {
            "error_code": "authentication_failed",
            "password": "raw",
            "session_token": "raw",
            "csrf_token": "raw",
            "credential_identifier": "irreversible",
        }
    )
    assert metadata["password"] == "[redacted]"
    assert metadata["session_token"] == "[redacted]"
    assert metadata["csrf_token"] == "[redacted]"
    assert metadata["credential_identifier"] == "irreversible"
    nested = safe_metadata(
        {
            "details": {
                "password": "raw",
                "items": [{"session_token": "raw"}],
                "long_value": "x" * 600,
            }
        }
    )
    assert nested["details"]["password"] == "[redacted]"  # type: ignore[index]
    assert nested["details"]["items"][0]["session_token"] == "[redacted]"  # type: ignore[index]
    assert "[truncated]" in nested["details"]["long_value"]  # type: ignore[index]


def test_security_settings_validation() -> None:
    with pytest.raises(ValueError):
        Settings(session_cookie_samesite="none", session_cookie_secure=False)
    with pytest.raises(ValueError):
        Settings(
            app_env="production", session_secret="dev-only-change-me", session_cookie_secure=True
        )
    with pytest.raises(ValueError):
        Settings(app_env="production", session_secret="x" * 32, session_cookie_secure=False)
    with pytest.raises(ValueError):
        Settings(session_token_bytes=16)


def test_csrf_endpoint_issues_no_store_pre_auth_token(auth_client: tuple[TestClient, Any]) -> None:
    client, _ = auth_client

    response = client.get("/api/v1/auth/csrf", headers={"X-Request-ID": "csrf-1"})

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["data"]["csrf_token"].startswith("csrf_")
    assert response.json()["meta"]["request_id"] == "csrf-1"
    set_cookie = response.headers["set-cookie"]
    assert "rag_csrf=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


def test_login_sets_http_only_cookie_and_stores_only_hash(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = auth_client

    response = login_response(client)
    body = response.json()

    assert body["data"]["user"] == {
        "user_id": 1,
        "email": "admin@example.com",
        "display_name": "Admin",
        "role": "admin",
    }
    assert body["data"]["csrf_token"].startswith("csrf_")
    assert "password" not in str(body).lower()
    session_cookie = client.cookies.get("rag_session")
    assert session_cookie is not None
    set_cookie = response.headers["set-cookie"]
    assert "rag_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Secure" not in set_cookie

    with session_factory() as db:
        session = db.scalar(select(UserSession))
        assert session is not None
        assert session.session_token_hash
        assert session.session_token_hash != session_cookie
        assert verify_token_hash(session_cookie, session.session_token_hash)
        assert session.csrf_state_hash != session_cookie
        assert verify_token_hash(body["data"]["csrf_token"], session.csrf_state_hash)
        assert session.csrf_state_hash != body["data"]["csrf_token"]
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert admin is not None
        assert admin.last_login_at is not None
        audit = db.scalar(select(AuditLog).where(AuditLog.action_type == "auth.login_success"))
        assert audit is not None
        assert audit.actor_user_id == admin.user_id


def test_login_failure_is_generic_audited_and_rate_limited(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = auth_client

    def attempt(email: str, password: str) -> tuple[int, str, str]:
        csrf_token = client.get("/api/v1/auth/csrf").json()["data"]["csrf_token"]
        response = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
        )
        body = response.json()
        return response.status_code, body["error"]["code"], body["error"]["message"]

    wrong_password = attempt("admin@example.com", "wrong-password")
    unknown_email = attempt("missing@example.com", TEST_PASSWORD)
    disabled_user = attempt("disabled@example.com", TEST_PASSWORD)

    assert wrong_password == (401, "authentication_failed", "Authentication failed.")
    assert unknown_email == wrong_password
    assert disabled_user == wrong_password

    for _ in range(5):
        attempt("locked@example.com", "wrong-password")
    assert attempt("locked@example.com", "wrong-password")[0:2] == (
        429,
        "rate_limit_exceeded",
    )

    with session_factory() as db:
        failure_logs = db.scalars(
            select(AuditLog).where(AuditLog.action_type == "auth.login_failure")
        ).all()
        assert len(failure_logs) >= 4
        serialized = str([log.metadata_json for log in failure_logs])
        assert "wrong-password" not in serialized
        assert TEST_PASSWORD not in serialized
        assert "missing@example.com" not in serialized


def test_login_rejects_missing_invalid_csrf_and_wrong_origin(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_client

    missing = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "csrf_missing"

    csrf_token = client.get("/api/v1/auth/csrf").json()["data"]["csrf_token"]
    invalid = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": "csrf_invalid", "Origin": ALLOWED_ORIGIN},
    )
    assert invalid.status_code == 403
    assert invalid.json()["error"]["code"] == "csrf_invalid"

    missing_origin = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert missing_origin.status_code == 403
    assert missing_origin.json()["error"]["code"] == "csrf_invalid"

    wrong_origin = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://evil.local"},
    )
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["error"]["code"] == "csrf_invalid"
    assert csrf_token not in wrong_origin.text


def test_pre_auth_csrf_cannot_be_reused_after_login(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = auth_client
    csrf_response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    pre_auth_token = csrf_response.json()["data"]["csrf_token"]
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": pre_auth_token, "Origin": ALLOWED_ORIGIN},
    )
    assert login.status_code == 200
    session_token = login.json()["data"]["csrf_token"]
    assert session_token != pre_auth_token

    rejected = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": pre_auth_token, "Origin": ALLOWED_ORIGIN},
    )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "csrf_invalid"

    ok = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": session_token, "Origin": ALLOWED_ORIGIN},
    )
    assert ok.status_code == 200


def test_me_logout_revoked_and_expired_sessions(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = auth_client

    unauthenticated = client.get("/api/v1/auth/me")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "auth_required"

    login_as(client)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["data"]["role"] == "admin"

    csrf_token = issue_session_csrf(client)
    logout = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert logout.status_code == 200
    assert logout.json()["data"] == {"status": "logged_out"}
    assert client.cookies.get("rag_session") is None
    assert client.get("/api/v1/auth/me").status_code == 401

    with session_factory() as db:
        session = db.scalar(select(UserSession))
        assert session is not None
        assert session.revoked_at is not None
        assert session.csrf_state_hash is None
        assert db.scalar(select(AuditLog).where(AuditLog.action_type == "auth.logout_success"))

    login_as(client)
    with session_factory() as db:
        session = db.scalars(select(UserSession).order_by(UserSession.expires_at.desc())).first()
        assert session is not None
        session.created_at = now_utc() - timedelta(hours=2)
        session.expires_at = now_utc() - timedelta(hours=1)
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_session_bound_csrf_and_rbac(
    auth_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = auth_client
    login_as(client, email="viewer@example.com")
    csrf_token = issue_session_csrf(client)

    with session_factory() as db:
        session = db.scalar(select(UserSession).where(UserSession.revoked_at.is_(None)))
        assert session is not None
        assert session.csrf_state_hash == hash_token(csrf_token)
        assert session.csrf_state_hash != csrf_token

    forbidden = client.get("/api/v1/jobs")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "permission_denied"

    client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    login_as(client, email="admin@example.com")
    admin_response = client.get("/api/v1/jobs")
    assert admin_response.status_code == 200


def test_forwarded_for_is_used_only_for_trusted_proxy(monkeypatch) -> None:
    class Client:
        host = "10.0.0.1"

    class RequestStub:
        client = Client()
        headers = {"X-Forwarded-For": "203.0.113.10, 10.0.0.1"}

    monkeypatch.setenv("TRUSTED_PROXY_IPS", "[]")
    get_settings.cache_clear()
    try:
        assert client_ip(RequestStub()) == "10.0.0.1"  # type: ignore[arg-type]
        monkeypatch.setenv("TRUSTED_PROXY_IPS", '["10.0.0.1"]')
        get_settings.cache_clear()
        assert client_ip(RequestStub()) == "203.0.113.10"  # type: ignore[arg-type]
    finally:
        get_settings.cache_clear()


def test_truncate_user_agent_normalizes_empty_to_none() -> None:
    assert truncate_user_agent(None) is None
    assert truncate_user_agent("") is None
    assert truncate_user_agent("Mozilla/5.0") == "Mozilla/5.0"
    assert truncate_user_agent("a" * 600) == "a" * 512


def test_csrf_dependency_can_be_overridden_for_existing_foundation_tests() -> None:
    app = create_app()
    app.dependency_overrides[require_csrf] = lambda: None
    try:
        assert app.dependency_overrides[require_csrf]() is None
    finally:
        app.dependency_overrides.clear()
