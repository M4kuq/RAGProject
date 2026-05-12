from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from docx import Document
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.job_utils import LeaseLostError
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import DocumentChunk, DocumentVersion, Job, LogicalDocument, Role, User
from app.ingest.extractors.base import (
    ExtractedDocument,
    ExtractedPage,
    ExtractionInputMetadata,
    ExtractionMetadata,
)
from app.ingest.extractors.dispatcher import ExtractorDispatcher
from app.repositories.document_repository import DocumentRepository
from app.repositories.job_repository import JobRepository
from app.storage.file_storage import LocalFileStorage
from app.workers.handlers.base import JobExecutionContext
from app.workers.handlers.document_ingest_handler import DocumentIngestHandler
from app.workers.job_dispatcher import JobDispatcher
from app.workers.worker_config import WorkerConfig
from app.workers.worker_main import WorkerRunner

TEST_PASSWORD = "password"


@pytest.fixture
def ingest_session_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[sessionmaker[Session], LocalFileStorage]]:
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "UPLOAD_ALLOWED_EXTENSIONS",
        '[".pdf",".docx",".txt",".md",".markdown",".csv"]',
    )
    monkeypatch.setenv("INGEST_CHUNK_SIZE_TOKENS", "5")
    monkeypatch.setenv("INGEST_CHUNK_OVERLAP_TOKENS", "1")
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        admin_role = Role(role_name="admin", description="Admin")
        db.add(admin_role)
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
        yield factory, LocalFileStorage(tmp_path)
    finally:
        get_settings.cache_clear()
        engine.dispose()


@pytest.mark.parametrize(
    ("file_name", "mime_type", "content"),
    [
        ("doc.txt", "text/plain", b"alpha beta gamma delta epsilon zeta"),
        ("doc.md", "text/markdown", b"# Intro\nalpha beta gamma\n## Next\ndelta epsilon zeta"),
        ("doc.csv", "text/csv", b"name,value\nalpha,1\nbeta,2\n"),
        ("doc.pdf", "application/pdf", None),
        (
            "doc.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            None,
        ),
    ],
)
def test_document_ingest_handler_success_for_supported_types(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
    tmp_path: Path,
    file_name: str,
    mime_type: str,
    content: bytes | None,
) -> None:
    session_factory, storage = ingest_session_factory
    if file_name.endswith(".docx"):
        content = _docx_bytes(tmp_path)
    if file_name.endswith(".pdf"):
        content = _minimal_pdf("alpha beta gamma delta")
    assert content is not None
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name=file_name,
        mime_type=mime_type,
        content=content,
    )

    result = _handler(session_factory, storage).handle(_context(version_id))

    assert result.status == "succeeded"
    with session_factory() as db:
        version = db.get(DocumentVersion, version_id)
        chunks = db.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == version_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
        assert version is not None
        assert version.status == "processing"
        assert version.error_code is None
        assert version.extractor_name is not None
        assert chunks
        assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
        assert all(chunk.chunk_hash and len(chunk.chunk_hash) == 64 for chunk in chunks)


def test_document_ingest_handler_missing_storage_file_fails_safely(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name="missing.txt",
        mime_type="text/plain",
        content=None,
    )

    result = _handler(session_factory, storage).handle(_context(version_id))

    assert result.status == "failed"
    assert result.error_code == "storage_file_missing"
    assert result.error_message == "Stored object was not found."
    _assert_failed_version(session_factory, version_id, "storage_file_missing")


def test_document_ingest_handler_rejects_invalid_payload_before_domain_update(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name="valid.txt",
        mime_type="text/plain",
        content=b"alpha beta",
    )

    invalid_bool = _handler(session_factory, storage).handle(
        JobExecutionContext(
            job_id=1,
            job_type="document_ingest",
            target_type="document_version",
            target_id=version_id,
            payload={"document_version_id": True},
            worker_instance_id="worker-1",
        )
    )
    mismatch = _handler(session_factory, storage).handle(
        JobExecutionContext(
            job_id=2,
            job_type="document_ingest",
            target_type="document_version",
            target_id=version_id + 1,
            payload={"document_version_id": version_id},
            worker_instance_id="worker-1",
        )
    )
    logical_document_mismatch = _handler(session_factory, storage).handle(
        JobExecutionContext(
            job_id=3,
            job_type="document_ingest",
            target_type="document_version",
            target_id=version_id,
            payload={"document_version_id": version_id, "logical_document_id": 999},
            worker_instance_id="worker-1",
        )
    )

    assert invalid_bool.status == "failed"
    assert invalid_bool.error_code == "validation_error"
    assert mismatch.status == "failed"
    assert mismatch.error_code == "validation_error"
    assert logical_document_mismatch.status == "failed"
    assert logical_document_mismatch.error_code == "validation_error"
    with session_factory() as db:
        version = db.get(DocumentVersion, version_id)
        assert version is not None
        assert version.status == "processing"
        assert version.error_code is None


def test_document_ingest_handler_ready_version_is_noop_and_preserves_chunks(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name="ready.txt",
        mime_type="text/plain",
        content=b"ready alpha beta",
        status="ready",
    )
    with session_factory() as db:
        db.add(
            DocumentChunk(
                document_version_id=version_id,
                chunk_index=0,
                chunk_hash="c" * 64,
                content_text="existing chunk",
            )
        )
        db.commit()

    result = _handler(session_factory, storage).handle(_context(version_id))

    assert result.status == "succeeded"
    assert result.result_json["result_code"] == "no_op"
    with session_factory() as db:
        version = db.get(DocumentVersion, version_id)
        assert version is not None
        assert version.status == "ready"
        chunks = db.scalars(select(DocumentChunk)).all()
        assert len(chunks) == 1
        assert chunks[0].content_text == "existing chunk"


def test_document_ingest_handler_unsupported_and_empty_text_fail_safely(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    unsupported_id = _create_document_version(
        session_factory,
        storage,
        file_name="script.exe",
        mime_type="application/octet-stream",
        content=b"MZ",
    )
    empty_id = _create_document_version(
        session_factory,
        storage,
        file_name="empty.txt",
        mime_type="text/plain",
        content=b"   \n",
    )

    unsupported = _handler(session_factory, storage).handle(_context(unsupported_id))
    empty = _handler(session_factory, storage).handle(_context(empty_id))

    assert unsupported.status == "failed"
    assert unsupported.error_code == "unsupported_file_type"
    assert empty.status == "failed"
    assert empty.error_code == "empty_extracted_text"
    _assert_failed_version(session_factory, unsupported_id, "unsupported_file_type")
    _assert_failed_version(session_factory, empty_id, "empty_extracted_text")


def test_document_ingest_handler_chunking_zero_failure_cleans_partial_chunks(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name="blank.txt",
        mime_type="text/plain",
        content=b"ignored",
    )
    with session_factory() as db:
        db.add(
            DocumentChunk(
                document_version_id=version_id,
                chunk_index=0,
                chunk_hash="a" * 64,
                content_text="old",
            )
        )
        db.commit()

    handler = DocumentIngestHandler(
        session_factory=session_factory,
        storage=storage,
        dispatcher=cast(ExtractorDispatcher, _WhitespaceDispatcher()),
        settings=get_settings(),
    )
    result = handler.handle(_context(version_id))

    assert result.status == "failed"
    assert result.error_code == "no_chunks_created"
    with session_factory() as db:
        assert db.query(DocumentChunk).filter_by(document_version_id=version_id).count() == 0
    _assert_failed_version(session_factory, version_id, "no_chunks_created")


def test_document_ingest_handler_insert_failure_marks_failed_and_cleans_chunks(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name="insert-failure.txt",
        mime_type="text/plain",
        content=b"alpha beta gamma delta",
    )
    with session_factory() as db:
        db.add(
            DocumentChunk(
                document_version_id=version_id,
                chunk_index=0,
                chunk_hash="d" * 64,
                content_text="old chunk",
            )
        )
        db.commit()

    handler = DocumentIngestHandler(
        session_factory=session_factory,
        repository=_FailingInsertDocumentRepository(),
        job_repository=cast(JobRepository, _NoopJobRepository()),
        storage=storage,
        settings=get_settings(),
    )
    result = handler.handle(_context(version_id))

    assert result.status == "failed"
    assert result.error_code == "document_chunk_insert_failed"
    _assert_failed_version(session_factory, version_id, "document_chunk_insert_failed")


def test_document_ingest_handler_extracted_text_over_limit_fails_safely(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory, storage = ingest_session_factory
    monkeypatch.setenv("INGEST_MAX_EXTRACTED_TEXT_CHARS", "3")
    get_settings.cache_clear()
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name="too-long.txt",
        mime_type="text/plain",
        content=b"alpha beta",
    )

    result = _handler(session_factory, storage).handle(_context(version_id))

    assert result.status == "failed"
    assert result.error_code == "text_extraction_failed"
    _assert_failed_version(session_factory, version_id, "text_extraction_failed")


def test_document_ingest_handler_lease_lost_blocks_domain_write(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name="lease.txt",
        mime_type="text/plain",
        content=b"alpha beta",
    )
    handler = DocumentIngestHandler(
        session_factory=session_factory,
        job_repository=cast(JobRepository, _LeaseLostJobRepository()),
        storage=storage,
        settings=get_settings(),
    )

    with pytest.raises(LeaseLostError):
        handler.handle(_context(version_id))

    with session_factory() as db:
        version = db.get(DocumentVersion, version_id)
        assert version is not None
        assert version.status == "processing"
        assert version.error_code is None
        assert db.query(DocumentChunk).filter_by(document_version_id=version_id).count() == 0


def test_document_ingest_handler_rechecks_archived_document_before_final_write(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name="archive-race.txt",
        mime_type="text/plain",
        content=b"alpha beta",
    )
    handler = DocumentIngestHandler(
        session_factory=session_factory,
        job_repository=cast(JobRepository, _NoopJobRepository()),
        storage=storage,
        dispatcher=cast(ExtractorDispatcher, _ArchivingDispatcher(session_factory)),
        settings=get_settings(),
    )

    result = handler.handle(_context(version_id))

    assert result.status == "failed"
    assert result.error_code == "document_version_not_ingestable"
    with session_factory() as db:
        version = db.get(DocumentVersion, version_id)
        assert version is not None
        assert version.status == "processing"
        assert db.query(DocumentChunk).filter_by(document_version_id=version_id).count() == 0


def test_document_ingest_handler_retry_cleans_existing_chunks_and_reinserts(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    version_id = _create_document_version(
        session_factory,
        storage,
        file_name="retry.txt",
        mime_type="text/plain",
        content=b"new alpha beta gamma delta epsilon",
        status="failed",
        error_code="text_extraction_failed",
    )
    with session_factory() as db:
        db.add(
            DocumentChunk(
                document_version_id=version_id,
                chunk_index=0,
                chunk_hash="b" * 64,
                content_text="old chunk",
            )
        )
        db.commit()

    result = _handler(session_factory, storage).handle(_context(version_id))

    assert result.status == "succeeded"
    with session_factory() as db:
        version = db.get(DocumentVersion, version_id)
        chunks = db.scalars(
            select(DocumentChunk).where(DocumentChunk.document_version_id == version_id)
        ).all()
        assert version is not None
        assert version.status == "processing"
        assert version.error_code is None
        assert chunks
        assert all(chunk.content_text != "old chunk" for chunk in chunks)


def test_worker_single_iteration_processes_document_ingest_success_and_failure(
    ingest_session_factory: tuple[sessionmaker[Session], LocalFileStorage],
) -> None:
    session_factory, storage = ingest_session_factory
    success_version_id = _create_document_version(
        session_factory,
        storage,
        file_name="worker.txt",
        mime_type="text/plain",
        content=b"worker alpha beta gamma delta epsilon",
    )
    failure_version_id = _create_document_version(
        session_factory,
        storage,
        file_name="worker-missing.txt",
        mime_type="text/plain",
        content=None,
    )
    with session_factory() as db:
        db.add_all(
            [
                Job(
                    job_id=1,
                    job_type="document_ingest",
                    status="queued",
                    target_type="document_version",
                    target_id=success_version_id,
                    payload_json={"document_version_id": success_version_id},
                ),
                Job(
                    job_id=2,
                    job_type="document_ingest",
                    status="queued",
                    target_type="document_version",
                    target_id=failure_version_id,
                    payload_json={"document_version_id": failure_version_id},
                ),
            ]
        )
        db.commit()

    dispatcher = JobDispatcher(
        {"document_ingest": _handler(session_factory, storage, enforce_lease=True)}
    )
    runner = WorkerRunner(
        config=_worker_config(batch_size=2),
        session_factory=session_factory,
        dispatcher=dispatcher,
    )

    assert runner.run_once() == 2

    with session_factory() as db:
        jobs = {job.job_id: job for job in db.scalars(select(Job)).all()}
        assert jobs[1].status == "succeeded"
        assert jobs[1].result_json == {
            "document_version_id": success_version_id,
            "logical_document_id": 1,
            "chunk_count": 2,
            "page_count": None,
            "status": "processing",
        }
        assert jobs[2].status == "failed"
        assert jobs[2].error_code == "storage_file_missing"
        assert (
            db.query(DocumentChunk)
            .filter_by(document_version_id=success_version_id)
            .count()
            > 0
        )
        failed_version = db.get(DocumentVersion, failure_version_id)
        assert failed_version is not None
        assert failed_version.status == "failed"


class _WhitespaceExtractor:
    name = "whitespace"
    version = "1"

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        return ExtractedDocument(
            pages=[ExtractedPage("   ", page_number=None)],
            metadata=ExtractionMetadata(
                extractor_name=self.name,
                extractor_version=self.version,
                page_count=None,
            ),
        )


class _WhitespaceDispatcher:
    def select(self, *, file_name: str, mime_type: str) -> _WhitespaceExtractor:
        return _WhitespaceExtractor()


class _ArchivingExtractor:
    name = "archiving"
    version = "1"

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def extract(self, file_path: Path, metadata: ExtractionInputMetadata) -> ExtractedDocument:
        with self.session_factory() as db:
            document = db.get(LogicalDocument, 1)
            assert document is not None
            document.status = "archived"
            document.archived_at = datetime.now(UTC)
            db.commit()
        return ExtractedDocument(
            pages=[ExtractedPage("alpha beta", page_number=None)],
            metadata=ExtractionMetadata(
                extractor_name=self.name,
                extractor_version=self.version,
                page_count=None,
            ),
        )


class _ArchivingDispatcher:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def select(self, *, file_name: str, mime_type: str) -> _ArchivingExtractor:
        return _ArchivingExtractor(self.session_factory)


def _handler(
    session_factory: sessionmaker[Session],
    storage: LocalFileStorage,
    *,
    enforce_lease: bool = False,
) -> DocumentIngestHandler:
    return DocumentIngestHandler(
        session_factory=session_factory,
        job_repository=None
        if enforce_lease
        else cast(JobRepository, _NoopJobRepository()),
        storage=storage,
        settings=get_settings(),
    )


def _context(document_version_id: int) -> JobExecutionContext:
    return JobExecutionContext(
        job_id=100 + document_version_id,
        job_type="document_ingest",
        target_type="document_version",
        target_id=document_version_id,
        payload={"document_version_id": document_version_id, "logical_document_id": 1},
        worker_instance_id="worker-1",
    )


def _create_document_version(
    session_factory: sessionmaker[Session],
    storage: LocalFileStorage,
    *,
    file_name: str,
    mime_type: str,
    content: bytes | None,
    status: str = "processing",
    error_code: str | None = None,
) -> int:
    storage_key = storage.build_storage_key(file_name=file_name)
    if content is not None:
        storage.save_bytes(storage_key=storage_key, content=content)
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert user is not None
        document = db.scalar(
            select(LogicalDocument).where(LogicalDocument.logical_document_id == 1)
        )
        if document is None:
            document = LogicalDocument(
                logical_document_id=1,
                owner_user_id=user.user_id,
                title="Ingest",
            )
            db.add(document)
            db.flush()
        version_no = db.query(DocumentVersion).count() + 1
        version = DocumentVersion(
            logical_document_id=document.logical_document_id,
            version_no=version_no,
            content_hash=f"{version_no:064x}",
            status=status,
            error_code=error_code,
            file_name=file_name,
            mime_type=mime_type,
            file_size_bytes=len(content or b""),
            storage_key=storage_key,
            created_by=user.user_id,
        )
        db.add(version)
        db.commit()
        return int(version.document_version_id)


def _assert_failed_version(
    session_factory: sessionmaker[Session], document_version_id: int, error_code: str
) -> None:
    with session_factory() as db:
        version = db.get(DocumentVersion, document_version_id)
        assert version is not None
        assert version.status == "failed"
        assert version.error_code == error_code
        assert (
            db.query(DocumentChunk)
            .filter_by(document_version_id=document_version_id)
            .count()
            == 0
    )


class _NoopJobRepository:
    def assert_ownership(self, db: Session, *, job_id: int, worker_instance_id: str) -> None:
        return None


class _LeaseLostJobRepository:
    def assert_ownership(self, db: Session, *, job_id: int, worker_instance_id: str) -> None:
        raise LeaseLostError(f"Lease lost for job_id={job_id}")


class _FailingInsertDocumentRepository(DocumentRepository):
    def bulk_insert_chunks(
        self,
        db: Session,
        *,
        chunks: Sequence[Mapping[str, object]],
    ) -> None:
        raise RuntimeError("safe synthetic insert failure")


def _docx_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "fixture.docx"
    document = Document()
    document.add_paragraph("alpha beta gamma delta")
    document.save(path)
    return path.read_bytes()


def _minimal_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        b"5 0 obj << /Length "
        + str(len(stream)).encode("ascii")
        + b" >> stream\n"
        + stream
        + b"\nendstream endobj\n",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(content))
        content.extend(obj)
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        (
            f"trailer << /Root 1 0 R /Size {len(objects) + 1} >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(content)


def _worker_config(batch_size: int = 1) -> WorkerConfig:
    return WorkerConfig(
        poll_interval_seconds=0,
        batch_size=batch_size,
        lease_duration=timedelta(minutes=5),
        lease_renew_interval_seconds=60,
        shutdown_grace_seconds=30,
        enabled_job_types=frozenset({"document_ingest"}),
        worker_instance_id="worker-1",
    )
