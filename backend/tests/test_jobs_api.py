from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.db.models import DocumentVersion, Job, LogicalDocument, Role, User
from app.db.session import get_db
from app.main import create_app

ALLOWED_ORIGIN = "http://localhost:5173"
TEST_PASSWORD = "password"


@pytest.fixture
def jobs_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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


def test_jobs_api_requires_auth_and_admin(
    jobs_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = jobs_client

    assert client.get("/api/v1/jobs").status_code == 401
    _login_as(client, "viewer@example.com")
    forbidden = client.get("/api/v1/jobs")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "permission_denied"


def test_jobs_list_and_detail_return_redacted_payload(
    jobs_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = jobs_client
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        db.add(
            Job(
                job_id=1,
                job_type="document_ingest",
                status="failed",
                payload_json={
                    "document_version_id": 1,
                    "api_token": "secret-value",
                    "prompt": "hidden prompt",
                    "local_path": r"C:\Users\kei01\secret.txt",
                },
                result_json={
                    "handled": True,
                    "content": "raw document",
                    "storage_key": "documents/x",
                },
                error_code="job_handler_not_implemented",
                error_message=r"failed at C:\Users\kei01\secret.txt",
                started_at=now,
                finished_at=now,
            )
        )
        db.commit()

    _login_as(client, "admin@example.com")
    listing = client.get("/api/v1/jobs")
    detail = client.get("/api/v1/jobs/1")
    missing = client.get("/api/v1/jobs/999")

    assert listing.status_code == 200
    assert detail.status_code == 200
    assert missing.status_code == 404
    assert "secret-value" not in listing.text
    assert "hidden prompt" not in detail.text
    assert r"C:\Users\kei01" not in detail.text
    body = detail.json()
    assert body["data"]["payload_view"]["payload"]["api_token"] == "[REDACTED]"
    assert body["data"]["result_json"] == {
        "handled": True,
        "content": "[REDACTED]",
        "storage_key": "[REDACTED]",
    }
    assert body["data"]["error_message"] == "Job failed with a redacted error."
    assert body["data"]["payload_view"]["payload_redacted"] is True
    assert body["data"]["source_job_id"] is None
    assert "locked_by" not in body["data"]


def test_jobs_list_filters_paginates_and_rejects_invalid_status(
    jobs_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = jobs_client
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        db.add_all(
            [
                Job(
                    job_id=1,
                    job_type="document_ingest",
                    status="queued",
                    target_type="document_version",
                    target_id=10,
                    payload_json={},
                    created_at=now - timedelta(minutes=2),
                    updated_at=now - timedelta(minutes=2),
                ),
                Job(
                    job_id=2,
                    job_type="evaluation_run",
                    status="failed",
                    target_type="evaluation_run",
                    target_id=20,
                    payload_json={},
                    error_code="safe_failure",
                    error_message="safe",
                    started_at=now - timedelta(minutes=1),
                    finished_at=now,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        db.commit()

    _login_as(client, "admin@example.com")
    filtered = client.get(
        "/api/v1/jobs?status=failed&job_type=evaluation_run&target_type=evaluation_run&target_id=20"
    )
    invalid = client.get("/api/v1/jobs?status=invalid")

    assert filtered.status_code == 200
    body = filtered.json()
    assert [item["job_id"] for item in body["data"]] == [2]
    assert body["meta"]["pagination"] == {
        "page": 1,
        "page_size": 20,
        "total": 1,
        "has_next": False,
    }
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"


def test_retry_requires_csrf_and_creates_queued_retry(
    jobs_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = jobs_client
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        db.add(
            Job(
                job_id=1,
                job_type="document_ingest",
                status="failed",
                payload_json={"document_version_id": 1, "secret": "hidden"},
                error_code="job_handler_not_implemented",
                error_message="safe",
                started_at=now,
                finished_at=now,
            )
        )
        db.commit()

    _login_as(client, "admin@example.com")
    without_csrf = client.post("/api/v1/jobs/1/retry", headers={"Origin": ALLOWED_ORIGIN})
    assert without_csrf.status_code == 403
    assert without_csrf.json()["error"]["code"] == "csrf_missing"

    csrf_token = _session_csrf(client)
    response = client.post(
        "/api/v1/jobs/1/retry",
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["result_code"] == "retry_created"
    assert body["data"]["source_job_id"] == 1
    assert body["data"]["status"] == "queued"
    assert "hidden" not in response.text

    with session_factory() as db:
        retry_job = db.get(Job, body["data"]["job_id"])
        assert retry_job is not None
        assert retry_job.status == "queued"
        assert retry_job.retry_of_job_id == 1
        assert retry_job.started_at is None
        assert retry_job.payload_json == {
            "document_version_id": 1,
            "secret": "[REDACTED]",
            "requested_by_user_id": 1,
        }


def test_retry_requires_auth_and_admin(
    jobs_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = jobs_client
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        db.add(
            Job(
                job_id=1,
                job_type="document_ingest",
                status="failed",
                payload_json={},
                error_code="safe_failure",
                error_message="safe",
                started_at=now,
                finished_at=now,
            )
        )
        db.commit()

    unauthenticated = client.post("/api/v1/jobs/1/retry", headers={"Origin": ALLOWED_ORIGIN})
    assert unauthenticated.status_code == 401

    _login_as(client, "viewer@example.com")
    csrf_token = _session_csrf(client)
    forbidden = client.post(
        "/api/v1/jobs/1/retry",
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "permission_denied"


def test_document_ingest_retry_resets_failed_version_to_processing(
    jobs_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = jobs_client
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        admin = db.query(User).filter_by(email="admin@example.com").one()
        document = LogicalDocument(owner_user_id=admin.user_id, title="Failed ingest")
        db.add(document)
        db.flush()
        version = DocumentVersion(
            document_version_id=1,
            logical_document_id=document.logical_document_id,
            version_no=1,
            content_hash="0" * 64,
            status="failed",
            error_code="extract_failed",
            file_name="failed.txt",
            mime_type="text/plain",
            file_size_bytes=10,
            created_by=admin.user_id,
        )
        job = Job(
            job_id=1,
            job_type="document_ingest",
            status="failed",
            target_type="document_version",
            target_id=1,
            payload_json={"document_version_id": 1},
            error_code="extract_failed",
            error_message="safe",
            started_at=now,
            finished_at=now,
        )
        db.add_all([version, job])
        db.commit()

    _login_as(client, "admin@example.com")
    csrf_token = _session_csrf(client)
    response = client.post(
        "/api/v1/jobs/1/retry",
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 201
    assert response.json()["data"]["job_id"] != 1

    with session_factory() as db:
        stored_version = db.get(DocumentVersion, 1)
        assert stored_version is not None
        assert stored_version.status == "processing"
        assert stored_version.error_code is None


def test_retry_missing_job_returns_404_and_retry_chain_uses_original_source(
    jobs_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = jobs_client
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        db.add_all(
            [
                Job(
                    job_id=1,
                    job_type="document_ingest",
                    status="failed",
                    payload_json={"document_version_id": 1},
                    error_code="safe_failure",
                    error_message="safe",
                    started_at=now - timedelta(minutes=2),
                    finished_at=now - timedelta(minutes=1),
                ),
                Job(
                    job_id=2,
                    job_type="document_ingest",
                    status="failed",
                    payload_json={"document_version_id": 1},
                    retry_of_job_id=1,
                    retry_count=1,
                    error_code="safe_failure",
                    error_message="safe",
                    started_at=now - timedelta(minutes=1),
                    finished_at=now,
                ),
            ]
        )
        db.commit()

    _login_as(client, "admin@example.com")
    csrf_token = _session_csrf(client)
    missing = client.post(
        "/api/v1/jobs/999/retry",
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    response = client.post(
        "/api/v1/jobs/2/retry",
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )

    assert missing.status_code == 404
    assert response.status_code == 201
    body = response.json()["data"]
    assert body["source_job_id"] == 1
    assert body["retry_count"] == 2
    with session_factory() as db:
        retry = db.get(Job, body["job_id"])
        assert retry is not None
        assert retry.retry_of_job_id == 1


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "canceled"])
def test_retry_rejects_non_failed_statuses(
    jobs_client: tuple[TestClient, sessionmaker[Session]],
    status: str,
) -> None:
    client, session_factory = jobs_client
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        job = Job(job_id=1, job_type="document_ingest", status=status, payload_json={})
        if status == "running":
            job.locked_by = "worker"
            job.locked_at = now
            job.lease_expires_at = now + timedelta(minutes=5)
            job.started_at = now
        if status in {"succeeded", "canceled"}:
            job.started_at = now if status == "succeeded" else None
            job.finished_at = now
        db.add(job)
        db.commit()

    _login_as(client, "admin@example.com")
    csrf_token = _session_csrf(client)
    response = client.post(
        "/api/v1/jobs/1/retry",
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_not_ready"


def test_retry_rejects_active_retry(jobs_client: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, session_factory = jobs_client
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        db.add_all(
            [
                Job(
                    job_id=1,
                    job_type="document_ingest",
                    status="failed",
                    payload_json={},
                    error_code="safe_failure",
                    error_message="safe",
                    started_at=now,
                    finished_at=now,
                ),
                Job(
                    job_id=2,
                    job_type="document_ingest",
                    status="queued",
                    payload_json={},
                    retry_of_job_id=1,
                ),
            ]
        )
        db.commit()

    _login_as(client, "admin@example.com")
    csrf_token = _session_csrf(client)
    response = client.post(
        "/api/v1/jobs/1/retry",
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_active_retry_exists"


def _login_as(client: TestClient, email: str) -> dict[str, Any]:
    csrf = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert csrf.status_code == 200
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf.json()["data"]["csrf_token"], "Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200
    return response.json()


def _session_csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])
