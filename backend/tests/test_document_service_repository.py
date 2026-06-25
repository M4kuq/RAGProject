from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.errors import DocumentArchived, DocumentVersionNotApprovable, ResourceNotFound
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import AuditLog, DocumentVersion, Job, LogicalDocument, Role, User
from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE
from app.repositories.document_repository import DocumentRepository
from app.repositories.job_repository import JobRepository
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
    monkeypatch.setenv(
        "UPLOAD_ALLOWED_EXTENSIONS",
        '[".pdf",".docx",".txt",".md",".markdown",".csv",".xlsx",".pptx"]',
    )
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


def test_document_repository_ingest_chunk_cleanup_and_metadata_updates(
    document_session_factory: tuple[sessionmaker[Session], Path],
) -> None:
    session_factory, storage_root = document_session_factory
    service = DocumentService(storage=LocalFileStorage(storage_root))
    repository = DocumentRepository()

    with session_factory() as db:
        user = admin_user(db)
        created = service.upload_document(
            db,
            user=user,
            title="Repository ingest",
            filename="repository.txt",
            content_type="text/plain",
            content=b"repository ingest content",
            request_id="repo-ingest-create",
        )
        document_version_id = created.document_version_id
        db.commit()

        repository.bulk_insert_chunks(
            db,
            chunks=[
                _chunk_row(document_version_id, 0, "alpha beta", "a"),
                _chunk_row(document_version_id, 1, "gamma delta", "b"),
            ],
        )
        db.commit()
        assert repository.count_chunks(db, document_version_id=document_version_id) == 2

        with pytest.raises(IntegrityError):
            repository.bulk_insert_chunks(
                db,
                chunks=[_chunk_row(document_version_id, 1, "duplicate", "c")],
            )
            db.commit()
        db.rollback()
        assert repository.count_chunks(db, document_version_id=document_version_id) == 2

        assert repository.delete_chunks(db, document_version_id=document_version_id) == 2
        repository.bulk_insert_chunks(
            db,
            chunks=[_chunk_row(document_version_id, 0, "retry content", "d")],
        )
        version = repository.get_version_by_id(
            db, document_version_id=document_version_id, for_update=True
        )
        assert version is not None
        repository.update_ingest_metadata(
            db,
            version=version,
            page_count=3,
            extractor_name="plain_text",
            extractor_version="1",
            updated_at=version.updated_at,
        )
        db.commit()

        updated = db.get(DocumentVersion, document_version_id)
        assert updated is not None
        assert updated.status == "processing"
        assert updated.error_code is None
        assert updated.page_count == 3
        assert updated.extractor_name == "plain_text"
        assert repository.count_chunks(db, document_version_id=document_version_id) == 1

        repository.mark_version_failed(
            db,
            version=updated,
            error_code="text_extraction_failed",
            updated_at=updated.updated_at,
        )
        assert repository.delete_chunks(db, document_version_id=document_version_id) == 1
        db.commit()

        failed = db.get(DocumentVersion, document_version_id)
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error_code == "text_extraction_failed"
        assert failed.page_count is None
        assert failed.extractor_name is None
        assert failed.extractor_version is None
        assert repository.count_chunks(db, document_version_id=document_version_id) == 0


def test_document_repository_retry_reset_clears_stale_ingest_metadata(
    document_session_factory: tuple[sessionmaker[Session], Path],
) -> None:
    session_factory, storage_root = document_session_factory
    service = DocumentService(storage=LocalFileStorage(storage_root))
    repository = DocumentRepository()

    with session_factory() as db:
        user = admin_user(db)
        created = service.upload_document(
            db,
            user=user,
            title="Retry metadata",
            filename="retry-metadata.txt",
            content_type="text/plain",
            content=b"retry metadata content",
            request_id="repo-ingest-metadata-reset",
        )
        version = repository.get_version_by_id(
            db, document_version_id=created.document_version_id, for_update=True
        )
        assert version is not None
        repository.update_ingest_metadata(
            db,
            version=version,
            page_count=4,
            extractor_name="plain_text",
            extractor_version="1",
            updated_at=version.updated_at,
        )
        db.commit()

        stale = repository.get_version_by_id(
            db, document_version_id=created.document_version_id, for_update=True
        )
        assert stale is not None
        repository.reset_version_for_ingest(db, version=stale, updated_at=stale.updated_at)
        db.commit()

        reset = db.get(DocumentVersion, created.document_version_id)
        assert reset is not None
        assert reset.status == "processing"
        assert reset.error_code is None
        assert reset.page_count is None
        assert reset.extractor_name is None
        assert reset.extractor_version is None


def test_document_service_upload_cleans_storage_when_db_flow_fails(
    document_session_factory: tuple[sessionmaker[Session], Path],
) -> None:
    session_factory, storage_root = document_session_factory
    service = DocumentService(
        job_repository=_FailingJobRepository(),
        storage=LocalFileStorage(storage_root),
    )

    with session_factory() as db:
        user = admin_user(db)
        with pytest.raises(RuntimeError):
            service.upload_document(
                db,
                user=user,
                title="Cleanup",
                filename="cleanup.txt",
                content_type="text/plain",
                content=b"cleanup content",
                request_id="storage-cleanup",
            )

    assert [path for path in storage_root.rglob("*") if path.is_file()] == []


def test_document_repository_ready_failed_and_chunk_id_support(
    document_session_factory: tuple[sessionmaker[Session], Path],
) -> None:
    session_factory, storage_root = document_session_factory
    service = DocumentService(storage=LocalFileStorage(storage_root))
    repository = DocumentRepository()

    with session_factory() as db:
        user = admin_user(db)
        created = service.upload_document(
            db,
            user=user,
            title="Repository indexing",
            filename="repo-index.txt",
            content_type="text/plain",
            content=b"alpha beta",
            request_id="repo-indexing",
        )
        version = repository.get_version_by_id(
            db, document_version_id=created.document_version_id, for_update=True
        )
        assert version is not None
        repository.bulk_insert_chunks(
            db,
            chunks=[
                _chunk_row(created.document_version_id, 1, "beta", "b"),
                _chunk_row(created.document_version_id, 0, "alpha", "a"),
            ],
        )
        db.flush()

        chunk_ids = repository.chunk_ids_by_document_version(
            db, document_version_id=created.document_version_id
        )
        chunks = repository.list_chunks_for_embedding(
            db, document_version_id=created.document_version_id
        )
        repository.mark_version_ready(db, version=version, updated_at=version.updated_at)
        db.commit()

        assert chunk_ids == [chunk.document_chunk_id for chunk in chunks]
        assert [chunk.chunk_index for chunk in chunks] == [0, 1]
        ready_version = db.get(DocumentVersion, created.document_version_id)
        assert ready_version is not None
        assert ready_version.status == "ready"
        assert ready_version.error_code is None

        repository.mark_version_failed(
            db,
            version=ready_version,
            error_code="embedding_failed",
            updated_at=ready_version.updated_at,
        )
        db.commit()

        failed_version = db.get(DocumentVersion, created.document_version_id)
        assert failed_version is not None
        assert failed_version.status == "failed"
        assert failed_version.error_code == "embedding_failed"


def test_approve_replacement_version_requeues_old_and_new_graph_projection(
    document_session_factory: tuple[sessionmaker[Session], Path],
) -> None:
    session_factory, storage_root = document_session_factory
    service = DocumentService(storage=LocalFileStorage(storage_root))
    repository = DocumentRepository()

    with session_factory() as db:
        user = admin_user(db)
        document = repository.create_logical_document(
            db,
            owner_user_id=user.user_id,
            title="Versioned graph document",
        )
        old_version = repository.create_version(
            db,
            logical_document_id=document.logical_document_id,
            version_no=1,
            content_hash="a" * 64,
            file_name="old.txt",
            mime_type="text/plain",
            file_size_bytes=3,
            storage_key="old",
            created_by=user.user_id,
        )
        new_version = repository.create_version(
            db,
            logical_document_id=document.logical_document_id,
            version_no=2,
            content_hash="b" * 64,
            file_name="new.txt",
            mime_type="text/plain",
            file_size_bytes=3,
            storage_key="new",
            created_by=user.user_id,
        )
        old_version.status = "ready"
        old_version.is_active = True
        new_version.status = "ready"
        db.commit()

        approved = service.approve_version(
            db,
            user=user,
            logical_document_id=document.logical_document_id,
            document_version_id=new_version.document_version_id,
            request_id="doc-service-approve-replacement",
        )

        assert approved.result_code == "approved"
        assert approved.previous_active_document_version_id == old_version.document_version_id
        graph_jobs = (
            db.query(Job)
            .filter_by(job_type=GRAPH_INDEX_BUILD_JOB_TYPE)
            .order_by(Job.job_id.asc())
            .all()
        )
        assert [job.target_id for job in graph_jobs] == [
            old_version.document_version_id,
            new_version.document_version_id,
        ]
        assert [job.payload_json for job in graph_jobs] == [
            {
                "job_type": GRAPH_INDEX_BUILD_JOB_TYPE,
                "document_version_id": old_version.document_version_id,
                "reindex_policy": "replace_existing",
            },
            {
                "job_type": GRAPH_INDEX_BUILD_JOB_TYPE,
                "document_version_id": new_version.document_version_id,
                "reindex_policy": "replace_existing",
            },
        ]


def _chunk_row(
    document_version_id: int,
    chunk_index: int,
    content_text: str,
    hash_prefix: str,
) -> dict[str, object]:
    return {
        "document_version_id": document_version_id,
        "chunk_index": chunk_index,
        "chunk_hash": hash_prefix * 64,
        "content_text": content_text,
        "token_count": len(content_text.split()),
        "char_count": len(content_text),
        "page_from": None,
        "page_to": None,
        "section_title": None,
        "modality": "text",
    }


class _FailingJobRepository(JobRepository):
    def create_job(
        self,
        db: Session,
        *,
        job_type: str,
        target_type: str | None = None,
        target_id: int | None = None,
        payload_json: dict[str, object] | None = None,
        created_by: int | None = None,
        priority: int = 100,
    ) -> Job:
        raise RuntimeError("synthetic job creation failure")
