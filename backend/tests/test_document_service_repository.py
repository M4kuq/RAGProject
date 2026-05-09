from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.errors import DocumentArchived, DocumentVersionNotApprovable, ResourceNotFound
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import AuditLog, DocumentVersion, Job, LogicalDocument, Role, User
from app.repositories.document_repository import DocumentRepository
from app.schemas.common import PaginationParams
from app.services.document_service import DocumentService
from app.storage.file_storage import LocalFileStorage

TEST_PASSWORD = "password"


@pytest.fixture
def document_session_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[sessionmaker[Session], Path]]:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("UPLOAD_MAX_BYTES", "1024")
    monkeypatch.setenv("UPLOAD_ALLOWED_EXTENSIONS", '[".pdf",".docx",".txt",".md",".csv"]')
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    with factory() as db:
        admin_role = Role(role_name="admin", description="Admin")
        viewer_role = Role(role_name="viewer", description="Viewer")
        db.add_all([admin_role, viewer_role])
        db.flush()
        db.add(
            User(
                role_id=admin_role.role_id,
                email="admin@example.com",
                display_name="Admin",
                password_hash=hash_password(TEST_PASSWORD),
                status="active",
            )
        )
        db.commit()
    try:
        yield factory, tmp_path
    finally:
        get_settings.cache_clear()
        engine.dispose()


def admin_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == "admin@example.com"))
    assert user is not None
    return user


def test_document_service_repository_upload_duplicate_approve_archive(
    document_session_factory: tuple[sessionmaker[Session], Path],
) -> None:
    session_factory, storage_root = document_session_factory
    service = DocumentService(storage=LocalFileStorage(storage_root))
    repository = DocumentRepository()
    content = b"service document"

    with session_factory() as db:
        user = admin_user(db)
        created = service.upload_document(
            db,
            user=user,
            title=None,
            filename="service.txt",
            content_type="text/plain",
            content=content,
            request_id="doc-service-create",
        )
        assert created.document.document_name == "service"
        assert created.version.version_no == 1
        assert created.version_status == "processing"
        logical_document_id = created.logical_document_id
        document_version_id = created.document_version_id

        rows, total = repository.list_documents(
            db,
            status="active",
            query="service",
            latest_version_filter=("processing", None),
            pagination=PaginationParams(),
        )
        assert total == 1
        assert rows[0].logical_document_id == logical_document_id
        conflicting, conflicting_meta = service.list_documents(
            db,
            status="archived",
            query=None,
            display_status="processing",
            pagination=PaginationParams(),
        )
        assert conflicting == []
        assert conflicting_meta.total == 0

        duplicate, created_flag = service.add_version(
            db,
            user=user,
            logical_document_id=logical_document_id,
            filename="service.txt",
            content_type="text/plain",
            content=content,
            request_id="doc-service-duplicate",
        )
        assert created_flag is False
        assert duplicate.status == "duplicate_content_skipped"
        assert duplicate.matched_document_version_id == document_version_id
        assert (
            db.query(DocumentVersion).filter_by(logical_document_id=logical_document_id).count()
            == 1
        )
        assert db.query(Job).filter_by(job_type="document_ingest").count() == 1

        version = db.get(DocumentVersion, document_version_id)
        assert version is not None
        with pytest.raises(DocumentVersionNotApprovable):
            service.approve_version(
                db,
                user=user,
                logical_document_id=logical_document_id,
                document_version_id=document_version_id,
                request_id="doc-service-approve-processing",
            )

        version = db.get(DocumentVersion, document_version_id)
        assert version is not None
        version.status = "ready"
        db.commit()
        approved = service.approve_version(
            db,
            user=user,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
            request_id="doc-service-approve",
        )
        assert approved.result_code == "approved"
        assert approved.active_version.is_active is True

        archived = service.archive_document(
            db,
            user=user,
            logical_document_id=logical_document_id,
            request_id="doc-service-archive",
        )
        assert archived.result_code == "archived"
        archived_document = db.get(LogicalDocument, logical_document_id)
        archived_version = db.get(DocumentVersion, document_version_id)
        assert archived_document is not None
        assert archived_version is not None
        assert archived_document.status == "archived"
        assert archived_version.is_active is False
        assert db.query(Job).filter_by(job_type="qdrant_mirror_update").count() == 2
        assert db.query(AuditLog).filter_by(action_type="document.archived").count() == 1

        with pytest.raises(DocumentArchived):
            service.add_version(
                db,
                user=user,
                logical_document_id=logical_document_id,
                filename="service-v2.txt",
                content_type="text/plain",
                content=b"new content",
                request_id="doc-service-archived-add",
            )
        with pytest.raises(ResourceNotFound):
            service.get_version_detail(
                db,
                logical_document_id=logical_document_id,
                document_version_id=document_version_id + 999,
            )
