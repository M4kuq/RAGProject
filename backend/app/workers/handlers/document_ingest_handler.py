from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.job_utils import LeaseLostError
from app.db.session import SessionLocal
from app.ingest.chunking import Chunk, ChunkingConfig, ChunkingError, FixedTokenChunker
from app.ingest.extractors.base import ExtractedDocument, ExtractionError, ExtractionInputMetadata
from app.ingest.extractors.dispatcher import ExtractorDispatcher
from app.ingest.metadata import DocumentIngestMetadata, metadata_from_extracted_document
from app.repositories.document_repository import DocumentRepository
from app.repositories.job_repository import JobRepository
from app.storage.file_storage import LocalFileStorage
from app.workers.handlers.base import JobExecutionContext, JobHandlerResult

logger = logging.getLogger(__name__)

_SAFE_MESSAGES = {
    "validation_error": "Job payload is invalid.",
    "document_version_not_found": "Document version was not found.",
    "document_version_not_ingestable": "Document version cannot be ingested.",
    "storage_file_missing": "Stored object was not found.",
    "unsupported_file_type": "Unsupported type.",
    "mime_type_mismatch": "MIME type mismatch.",
    "text_extraction_failed": "Extraction failed.",
    "empty_extracted_text": "Extraction result is empty.",
    "chunking_failed": "Segmentation failed.",
    "no_chunks_created": "No segments were created.",
    "document_chunk_insert_failed": "Segment persistence failed.",
    "ingest_cleanup_failed": "Ingest cleanup failed.",
    "internal_error": "Document ingest failed.",
}


@dataclass(frozen=True)
class _VersionSnapshot:
    document_version_id: int
    logical_document_id: int
    file_name: str
    mime_type: str
    file_size_bytes: int
    storage_key: str | None
    status: str


class DocumentIngestHandler:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        repository: DocumentRepository | None = None,
        job_repository: JobRepository | None = None,
        storage: LocalFileStorage | None = None,
        dispatcher: ExtractorDispatcher | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository or DocumentRepository()
        self.job_repository = job_repository or JobRepository()
        self.storage = storage or LocalFileStorage()
        self.dispatcher = dispatcher or ExtractorDispatcher()
        self.settings = settings or get_settings()

    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        document_version_id = context.payload.get("document_version_id")
        if not _is_positive_int(document_version_id):
            return _failed("validation_error")
        document_version_id = cast(int, document_version_id)
        if context.target_type != "document_version" or context.target_id != document_version_id:
            return _failed("validation_error")

        snapshot_or_result = self._prepare_version(context, document_version_id)
        if isinstance(snapshot_or_result, JobHandlerResult):
            return snapshot_or_result
        snapshot = snapshot_or_result

        if snapshot.status == "ready":
            return JobHandlerResult.succeeded(
                {
                    "document_version_id": snapshot.document_version_id,
                    "logical_document_id": snapshot.logical_document_id,
                    "status": "already_ready",
                    "result_code": "no_op",
                }
            )
        if snapshot.storage_key is None:
            return self._fail_version(
                context, snapshot.document_version_id, "storage_file_missing"
            )

        try:
            if not self.storage.exists(storage_key=snapshot.storage_key):
                return self._fail_version(
                    context, snapshot.document_version_id, "storage_file_missing"
                )
            file_path = self.storage.resolve_path(storage_key=snapshot.storage_key)
            extracted = self._extract(file_path, snapshot)
            metadata = metadata_from_extracted_document(extracted)
            if metadata.text_char_count > self.settings.ingest_max_extracted_text_chars:
                return self._fail_version(
                    context, snapshot.document_version_id, "text_extraction_failed"
                )
            chunks = self._chunk(extracted, snapshot.document_version_id)
            return self._store_success(context, snapshot, metadata, chunks)
        except ExtractionError as exc:
            return self._fail_version(context, snapshot.document_version_id, exc.error_code)
        except ChunkingError as exc:
            return self._fail_version(context, snapshot.document_version_id, exc.error_code)
        except LeaseLostError:
            raise
        except Exception:
            logger.error(
                "document ingest failed with unsafe exception",
                extra={
                    "job_id": context.job_id,
                    "document_version_id": snapshot.document_version_id,
                    "logical_document_id": snapshot.logical_document_id,
                    "error_code": "internal_error",
                },
            )
            return self._fail_version(context, snapshot.document_version_id, "internal_error")

    def _prepare_version(
        self, context: JobExecutionContext, document_version_id: int
    ) -> _VersionSnapshot | JobHandlerResult:
        db = self.session_factory()
        try:
            version = self.repository.get_version_by_id(
                db, document_version_id=document_version_id, for_update=True
            )
            if version is None:
                db.rollback()
                return _failed("document_version_not_found")
            document = self.repository.get_document(
                db, logical_document_id=version.logical_document_id, for_update=True
            )
            if document is None:
                db.rollback()
                return _failed("document_version_not_found")

            payload_logical_document_id = context.payload.get("logical_document_id")
            if (
                payload_logical_document_id is not None
                and payload_logical_document_id != version.logical_document_id
            ):
                db.rollback()
                return _failed("validation_error")

            snapshot = _VersionSnapshot(
                document_version_id=version.document_version_id,
                logical_document_id=version.logical_document_id,
                file_name=version.file_name,
                mime_type=version.mime_type,
                file_size_bytes=version.file_size_bytes,
                storage_key=version.storage_key,
                status=version.status,
            )
            if document.status == "archived" or version.status == "archived":
                db.rollback()
                return _failed("document_version_not_ingestable")
            if version.status != "ready":
                self.job_repository.assert_ownership(
                    db,
                    job_id=context.job_id,
                    worker_instance_id=context.worker_instance_id,
                )
                self.repository.reset_version_for_ingest(
                    db, version=version, updated_at=_now()
                )
            db.commit()
            return snapshot
        except LeaseLostError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.error(
                "document ingest prepare failed",
                extra={"document_version_id": document_version_id, "error_code": "internal_error"},
            )
            return _failed("internal_error")
        finally:
            db.close()

    def _extract(self, file_path: Path, snapshot: _VersionSnapshot) -> ExtractedDocument:
        extractor = self.dispatcher.select(
            file_name=snapshot.file_name,
            mime_type=snapshot.mime_type,
        )
        return extractor.extract(
            file_path,
            ExtractionInputMetadata(
                file_name=snapshot.file_name,
                mime_type=snapshot.mime_type,
                file_size_bytes=snapshot.file_size_bytes,
            ),
        )

    def _chunk(self, extracted: ExtractedDocument, document_version_id: int) -> list[Chunk]:
        try:
            chunker = FixedTokenChunker(
                ChunkingConfig(
                    chunk_size_tokens=self.settings.ingest_chunk_size_tokens,
                    chunk_overlap_tokens=self.settings.ingest_chunk_overlap_tokens,
                )
            )
            return chunker.chunk(extracted, document_version_id=document_version_id)
        except ChunkingError:
            raise
        except Exception as exc:
            raise ChunkingError("chunking_failed", "Chunking failed.") from exc

    def _store_success(
        self,
        context: JobExecutionContext,
        snapshot: _VersionSnapshot,
        metadata: DocumentIngestMetadata,
        chunks: list[Chunk],
    ) -> JobHandlerResult:
        if not chunks:
            return self._fail_version(
                context, snapshot.document_version_id, "no_chunks_created"
            )
        db = self.session_factory()
        try:
            version = self.repository.get_version_by_id(
                db, document_version_id=snapshot.document_version_id, for_update=True
            )
            if version is None:
                db.rollback()
                return _failed("document_version_not_found")
            document = self.repository.get_document(
                db, logical_document_id=version.logical_document_id, for_update=True
            )
            if document is None:
                db.rollback()
                return _failed("document_version_not_found")
            if document.status == "archived" or version.status == "archived":
                db.rollback()
                return _failed("document_version_not_ingestable")
            self.job_repository.assert_ownership(
                db,
                job_id=context.job_id,
                worker_instance_id=context.worker_instance_id,
            )
            self.repository.delete_chunks(db, document_version_id=snapshot.document_version_id)
            self.repository.bulk_insert_chunks(
                db,
                chunks=[_chunk_row(chunk) for chunk in chunks],
            )
            stored_count = self.repository.count_chunks(
                db, document_version_id=snapshot.document_version_id
            )
            if stored_count != len(chunks):
                raise RuntimeError("chunk insert count mismatch")
            self.repository.update_ingest_metadata(
                db,
                version=version,
                page_count=metadata.page_count,
                extractor_name=metadata.extractor_name,
                extractor_version=metadata.extractor_version,
                updated_at=_now(),
            )
            db.commit()
        except LeaseLostError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.error(
                "document chunk insert failed",
                extra={
                    "document_version_id": snapshot.document_version_id,
                    "logical_document_id": snapshot.logical_document_id,
                    "error_code": "document_chunk_insert_failed",
                },
            )
            return self._fail_version(
                context, snapshot.document_version_id, "document_chunk_insert_failed"
            )
        finally:
            db.close()

        return JobHandlerResult.succeeded(
            {
                "document_version_id": snapshot.document_version_id,
                "logical_document_id": snapshot.logical_document_id,
                "chunk_count": len(chunks),
                "page_count": metadata.page_count,
                "status": "processing",
            }
        )

    def _fail_version(
        self, context: JobExecutionContext, document_version_id: int, error_code: str
    ) -> JobHandlerResult:
        db = self.session_factory()
        try:
            version = self.repository.get_version_by_id(
                db, document_version_id=document_version_id, for_update=True
            )
            if version is not None and version.status != "archived":
                document = self.repository.get_document(
                    db, logical_document_id=version.logical_document_id, for_update=True
                )
                if document is not None and document.status == "archived":
                    db.rollback()
                    return _failed("document_version_not_ingestable")
                self.job_repository.assert_ownership(
                    db,
                    job_id=context.job_id,
                    worker_instance_id=context.worker_instance_id,
                )
                self.repository.delete_chunks(db, document_version_id=document_version_id)
                self.repository.mark_version_failed(
                    db,
                    version=version,
                    error_code=error_code,
                    updated_at=_now(),
                )
            db.commit()
            return _failed(error_code)
        except LeaseLostError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.error(
                "document ingest cleanup failed",
                extra={
                    "document_version_id": document_version_id,
                    "error_code": "ingest_cleanup_failed",
                },
            )
            return _failed("ingest_cleanup_failed")
        finally:
            db.close()


def _chunk_row(chunk: Chunk) -> dict[str, object]:
    return {
        "document_version_id": chunk.document_version_id,
        "chunk_index": chunk.chunk_index,
        "chunk_hash": chunk.chunk_hash,
        "content_text": chunk.content_text,
        "token_count": chunk.token_count,
        "char_count": chunk.char_count,
        "page_from": chunk.page_from,
        "page_to": chunk.page_to,
        "section_title": chunk.section_title,
        "modality": chunk.modality,
    }


def _failed(error_code: str) -> JobHandlerResult:
    return JobHandlerResult.failed(
        error_code=error_code,
        error_message=_SAFE_MESSAGES.get(error_code, _SAFE_MESSAGES["internal_error"]),
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _now() -> datetime:
    return datetime.now(UTC)
