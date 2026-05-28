from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routers.documents import document_service
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import AuditLog, DocumentChunk, DocumentVersion, Job, LogicalDocument, Role, User
from app.db.session import get_db
from app.ingest.embedding import (
    DocumentEmbeddingService,
    EmbeddingBatchConfig,
    FakeEmbeddingAdapter,
)
from app.ingest.qdrant import (
    DocumentIndexingService,
    InMemoryQdrantClient,
    QdrantCollectionConfig,
    QdrantVectorStore,
)
from app.main import create_app
from app.services.document_service import DocumentService
from app.services.url_fetch_service import UrlFetchResult
from app.storage.file_storage import LocalFileStorage
from app.workers.handlers.document_ingest_handler import DocumentIngestHandler
from app.workers.job_dispatcher import JobDispatcher
from app.workers.worker_config import WorkerConfig
from app.workers.worker_main import WorkerRunner

ALLOWED_ORIGIN = "http://localhost:5173"
TEST_PASSWORD = "password"


@pytest.fixture
def document_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[TestClient, sessionmaker[Session], Path]]:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "64")
    monkeypatch.setenv(
        "UPLOAD_ALLOWED_EXTENSIONS",
        '[".pdf",".docx",".txt",".md",".markdown",".csv",".xlsx",".pptx",".html",".htm",".xml"]',
    )
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_FAKE_DIMENSION", "4")
    monkeypatch.setenv("QDRANT_COLLECTION_NAME", "test_document_chunks")
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
        db.commit()

    def override_db() -> Iterator[Session]:
        with session_factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app), session_factory, tmp_path
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        engine.dispose()


def login(client: TestClient, email: str = "admin@example.com") -> str:
    csrf_response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert csrf_response.status_code == 200
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
        headers={
            "X-CSRF-Token": csrf_response.json()["data"]["csrf_token"],
            "Origin": ALLOWED_ORIGIN,
        },
    )
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def unsafe_headers(csrf_token: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN}


def issue_csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def storage_path(storage_root: Path, storage_key: str) -> Path:
    return storage_root.joinpath(*PurePosixPath(storage_key).parts)


def indexing_service() -> DocumentIndexingService:
    settings = get_settings()
    dimension = settings.effective_embedding_dimension
    return DocumentIndexingService(
        embedding_service=DocumentEmbeddingService(
            adapter=FakeEmbeddingAdapter(dimension=dimension),
            config=EmbeddingBatchConfig(
                dimension=dimension,
                batch_size=settings.embedding_batch_size,
            ),
        ),
        vector_store=QdrantVectorStore(
            client=InMemoryQdrantClient(),
            config=QdrantCollectionConfig(
                name=settings.qdrant_collection_name,
                vector_dimension=dimension,
                distance=settings.qdrant_distance,
            ),
            create_collection=True,
        ),
        upsert_batch_size=settings.qdrant_upsert_batch_size,
    )


def assert_no_sensitive_document_fields(payload: Any) -> None:
    serialized = str(payload).lower()
    assert "storage_key" not in serialized
    assert "storage/" not in serialized
    assert "password" not in serialized
    assert "session_token" not in serialized
    assert "csrf_token" not in serialized


def test_document_api_upload_duplicate_approve_archive_and_chunks(
    document_client: tuple[TestClient, sessionmaker[Session], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, storage_root = document_client

    unauthenticated = client.get("/api/v1/documents")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "auth_required"

    login(client, email="viewer@example.com")
    forbidden = client.get("/api/v1/documents")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "permission_denied"
    client.cookies.clear()

    csrf_token = login(client, email="admin@example.com")
    content = b"# Guide\nsmall body\n"
    upload = client.post(
        "/api/v1/documents",
        data={"title": " Guide "},
        files={"file": ("guide.md", content, "text/markdown")},
        headers=unsafe_headers(csrf_token),
    )
    assert upload.status_code == 201
    upload_body = upload.json()
    assert upload_body["data"]["version_status"] == "processing"
    assert upload_body["data"]["display_status"] == "processing"
    assert upload_body["data"]["document"]["active_version"] is None
    assert_no_sensitive_document_fields(upload_body)
    logical_document_id = int(upload_body["data"]["logical_document_id"])
    document_version_id = int(upload_body["data"]["document_version_id"])

    with session_factory() as db:
        document = db.get(LogicalDocument, logical_document_id)
        version = db.get(DocumentVersion, document_version_id)
        assert document is not None
        assert version is not None
        assert document.title == "Guide"
        assert version.version_no == 1
        assert version.status == "processing"
        assert version.is_active is False
        assert version.content_hash == hashlib.sha256(content).hexdigest()
        assert version.storage_key is not None
        assert storage_path(storage_root, version.storage_key).read_bytes() == content
        ingest_job = db.get(Job, upload_body["data"]["job_id"])
        assert ingest_job is not None
        assert ingest_job.job_type == "document_ingest"
        assert ingest_job.target_type == "document_version"
        assert ingest_job.target_id == document_version_id
        assert ingest_job.payload_json == {
            "logical_document_id": logical_document_id,
            "document_version_id": document_version_id,
            "requested_by_user_id": document.owner_user_id,
        }

    runner = WorkerRunner(
        config=WorkerConfig(
            poll_interval_seconds=0,
            batch_size=1,
            lease_duration=timedelta(minutes=5),
            lease_renew_interval_seconds=60,
            shutdown_grace_seconds=30,
            enabled_job_types=frozenset({"document_ingest"}),
            worker_instance_id="worker-1",
        ),
        session_factory=session_factory,
        dispatcher=JobDispatcher(
            {
                "document_ingest": DocumentIngestHandler(
                    session_factory=session_factory,
                    storage=LocalFileStorage(storage_root),
                    indexing_service=indexing_service(),
                )
            }
        ),
    )
    assert runner.run_once() == 1
    with session_factory() as db:
        ingest_job = db.get(Job, upload_body["data"]["job_id"])
        assert ingest_job is not None
        assert ingest_job.status == "succeeded"
        assert (
            db.query(DocumentChunk).filter_by(document_version_id=document_version_id).count() > 0
        )
        version = db.get(DocumentVersion, document_version_id)
        assert version is not None
        assert version.status == "ready"

    duplicate = client.post(
        f"/api/v1/documents/{logical_document_id}/versions",
        files={"file": ("guide.md", content, "text/markdown")},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["status"] == "duplicate_content_skipped"
    assert duplicate.json()["data"]["matched_document_version_id"] == document_version_id
    with session_factory() as db:
        assert (
            db.query(DocumentVersion).filter_by(logical_document_id=logical_document_id).count()
            == 1
        )
        assert db.query(Job).filter_by(job_type="document_ingest").count() == 1
        version = db.get(DocumentVersion, document_version_id)
        assert version is not None
        version.status = "ready"
        db.query(DocumentChunk).filter_by(document_version_id=document_version_id).delete()
        db.add(
            DocumentChunk(
                document_version_id=document_version_id,
                chunk_index=0,
                chunk_hash="a" * 64,
                content_text="x" * 240,
                token_count=10,
                char_count=240,
                page_from=1,
                page_to=1,
                section_title="Intro",
                modality="text",
            )
        )
        db.commit()

    pending = client.get("/api/v1/documents?display_status=pending_review")
    assert pending.status_code == 200
    assert pending.json()["meta"]["pagination"]["total"] == 1
    assert pending.json()["data"][0]["latest_version"]["display_status"] == "pending_review"
    assert pending.json()["data"][0]["active_version"] is None
    contradictory = client.get("/api/v1/documents?status=archived&display_status=pending_review")
    assert contradictory.status_code == 200
    assert contradictory.json()["data"] == []
    assert contradictory.json()["meta"]["pagination"]["total"] == 0

    approve = client.post(
        f"/api/v1/documents/{logical_document_id}/versions/{document_version_id}/approve",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert approve.status_code == 200
    assert approve.json()["data"]["result_code"] == "approved"
    assert approve.json()["data"]["active_version"]["display_status"] == "active"
    assert approve.json()["data"]["qdrant_mirror_job_id"] is not None

    approve_again = client.post(
        f"/api/v1/documents/{logical_document_id}/versions/{document_version_id}/approve",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert approve_again.status_code == 200
    assert approve_again.json()["data"]["result_code"] == "already_active"

    monkeypatch.setenv("INGEST_CHUNK_PREVIEW_CHARS", "500")
    get_settings.cache_clear()
    chunks = client.get(
        f"/api/v1/documents/{logical_document_id}/versions/{document_version_id}/chunks"
    )
    assert chunks.status_code == 200
    chunk = chunks.json()["data"][0]
    assert chunk["preview"] == "x" * 200
    assert chunk["preview_truncated"] is True
    assert "content_text" not in chunk

    archive = client.post(
        f"/api/v1/documents/{logical_document_id}/archive",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archive.status_code == 200
    assert archive.json()["data"]["result_code"] == "archived"
    assert archive.json()["data"]["retrieval_eligible"] is False
    with session_factory() as db:
        archived_document = db.get(LogicalDocument, logical_document_id)
        archived_version = db.get(DocumentVersion, document_version_id)
        assert archived_document is not None
        assert archived_version is not None
        assert archived_document.status == "archived"
        assert archived_version.is_active is False
        assert db.query(AuditLog).filter_by(action_type="document.archived").count() == 1

    archive_again = client.post(
        f"/api/v1/documents/{logical_document_id}/archive",
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archive_again.status_code == 200
    assert archive_again.json()["data"]["result_code"] == "already_archived"

    archived_version_add = client.post(
        f"/api/v1/documents/{logical_document_id}/versions",
        files={"file": ("new.md", b"# new\n", "text/markdown")},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert archived_version_add.status_code == 409
    assert archived_version_add.json()["error"]["code"] == "document_archived"


def test_document_api_upload_markdown_extension_cp932_and_worker_ingest(
    document_client: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, storage_root = document_client
    csrf_token = login(client, email="admin@example.com")
    content = "# Title\n".encode("cp932") + b"\x82\xa0\n"

    upload = client.post(
        "/api/v1/documents",
        data={"title": "Markdown CP932"},
        files={"file": ("guide.markdown", content, "text/markdown")},
        headers=unsafe_headers(csrf_token),
    )

    assert upload.status_code == 201
    body = upload.json()["data"]
    runner = WorkerRunner(
        config=WorkerConfig(
            poll_interval_seconds=0,
            batch_size=1,
            lease_duration=timedelta(minutes=5),
            lease_renew_interval_seconds=60,
            shutdown_grace_seconds=30,
            enabled_job_types=frozenset({"document_ingest"}),
            worker_instance_id="worker-1",
        ),
        session_factory=session_factory,
        dispatcher=JobDispatcher(
            {
                "document_ingest": DocumentIngestHandler(
                    session_factory=session_factory,
                    storage=LocalFileStorage(storage_root),
                    indexing_service=indexing_service(),
                )
            }
        ),
    )
    assert runner.run_once() == 1
    with session_factory() as db:
        job = db.get(Job, body["job_id"])
        assert job is not None
        assert job.status == "succeeded"
        version = db.get(DocumentVersion, body["document_version_id"])
        assert version is not None
        assert version.status == "ready"
        assert (
            db.query(DocumentChunk)
            .filter_by(document_version_id=body["document_version_id"])
            .count()
            > 0
        )


def test_document_api_url_ingest_creates_document_without_raw_body(
    document_client: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, session_factory, storage_root = document_client
    app = cast(FastAPI, client.app)
    service = DocumentService(
        storage=LocalFileStorage(storage_root),
        url_fetcher=_FakeUrlFetcher(),
    )
    app.dependency_overrides[document_service] = lambda: service
    csrf_token = login(client, email="admin@example.com")

    response = client.post(
        "/api/v1/documents/url",
        json={"url": "https://example.com/page?token=secret", "title": "URL Page"},
        headers=unsafe_headers(csrf_token),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["data"]["version_status"] == "processing"
    assert body["data"]["version"]["metadata_json"]["source_url"] == "https://example.com/page"
    assert "token=secret" not in str(body)
    assert "URL ingest alpha beta" not in str(body)
    logical_document_id = int(body["data"]["logical_document_id"])
    document_version_id = int(body["data"]["document_version_id"])

    runner = WorkerRunner(
        config=WorkerConfig(
            poll_interval_seconds=0,
            batch_size=1,
            lease_duration=timedelta(minutes=5),
            lease_renew_interval_seconds=60,
            shutdown_grace_seconds=30,
            enabled_job_types=frozenset({"document_ingest"}),
            worker_instance_id="worker-1",
        ),
        session_factory=session_factory,
        dispatcher=JobDispatcher(
            {
                "document_ingest": DocumentIngestHandler(
                    session_factory=session_factory,
                    storage=LocalFileStorage(storage_root),
                    indexing_service=indexing_service(),
                )
            }
        ),
    )
    assert runner.run_once() == 1
    with session_factory() as db:
        document = db.get(LogicalDocument, logical_document_id)
        version = db.get(DocumentVersion, document_version_id)
        chunk = db.scalar(
            select(DocumentChunk).where(DocumentChunk.document_version_id == document_version_id)
        )
        assert document is not None
        assert document.title == "URL Page"
        assert version is not None
        assert version.status == "ready"
        assert version.metadata_json is not None
        assert version.metadata_json["source_type"] == "url"
        assert chunk is not None
        assert chunk.metadata_json is not None
        assert chunk.metadata_json["source_url"] == "https://example.com/page"


class _FakeUrlFetcher:
    def fetch(self, url: str) -> UrlFetchResult:
        assert url == "https://example.com/page?token=secret"
        return UrlFetchResult(
            requested_url=url,
            final_url="https://example.com/page",
            safe_source_url="https://example.com/page",
            safe_final_url="https://example.com/page",
            content=b"<html><body><h1>Guide</h1><p>URL ingest alpha beta</p></body></html>",
            content_type="text/html",
            file_name="example.com-page.html",
            fetched_at=datetime.now(UTC),
            redirect_count=0,
        )


def test_document_api_upload_validation_errors(
    document_client: tuple[TestClient, sessionmaker[Session], Path],
) -> None:
    client, _, _ = document_client
    csrf_token = login(client, email="admin@example.com")

    missing_csrf = client.post(
        "/api/v1/documents",
        files={"file": ("guide.md", b"# ok\n", "text/markdown")},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_missing"

    too_large = client.post(
        "/api/v1/documents",
        files={"file": ("large.txt", b"x" * 65, "text/plain")},
        headers=unsafe_headers(csrf_token),
    )
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "payload_too_large"

    unsupported = client.post(
        "/api/v1/documents",
        files={"file": ("script.exe", b"MZ", "application/octet-stream")},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert unsupported.status_code == 415
    assert unsupported.json()["error"]["code"] == "unsupported_media_type"

    unsafe_pdf = client.post(
        "/api/v1/documents",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert unsafe_pdf.status_code == 415
    assert unsafe_pdf.json()["error"]["code"] == "unsafe_file_rejected"

    empty_file = client.post(
        "/api/v1/documents",
        files={"file": ("empty.txt", b"", "text/plain")},
        headers=unsafe_headers(issue_csrf(client)),
    )
    assert empty_file.status_code == 422
    assert empty_file.json()["error"]["code"] == "validation_error"
