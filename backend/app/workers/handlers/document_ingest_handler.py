from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.job_utils import LeaseLostError
from app.db.models import DocumentChunk
from app.db.session import SessionLocal
from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE
from app.graph.job_settings import graph_extractor_type_override, graph_indexing_enabled
from app.ingest.chunking import (
    Chunk,
    ChunkingConfig,
    ChunkingError,
    FixedTokenChunker,
    chunk_statistics,
)
from app.ingest.embedding import EmbeddingAdapterError
from app.ingest.extractors.base import ExtractedDocument, ExtractionError, ExtractionInputMetadata
from app.ingest.extractors.dispatcher import ExtractorDispatcher
from app.ingest.metadata import DocumentIngestMetadata, metadata_from_extracted_document
from app.ingest.qdrant import (
    DocumentIndexingService,
    QdrantStoreError,
    create_document_indexing_service,
)
from app.repositories.document_repository import DocumentRepository
from app.repositories.job_repository import JobRepository
from app.services.graph_index_service import GraphIndexService
from app.storage.file_storage import (
    DocumentStorage,
    DocumentStorageError,
    create_document_storage,
)
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
    "embedding_failed": "Embedding generation failed.",
    "embedding_dimension_mismatch": "Embedding dimension mismatch.",
    "embedding_empty_result": "Embedding result is empty.",
    "qdrant_unavailable": "Qdrant is unavailable.",
    "qdrant_collection_create_failed": "Qdrant collection create failed.",
    "qdrant_collection_dimension_mismatch": "Qdrant collection dimension mismatch.",
    "qdrant_upsert_failed": "Qdrant upsert failed.",
    "qdrant_cleanup_failed": "Qdrant cleanup failed.",
    "document_indexing_failed": "Document indexing failed.",
    "document_ready_update_failed": "Document ready update failed.",
    "internal_error": "Document ingest failed.",
}


@dataclass(frozen=True)
class _VersionSnapshot:
    document_version_id: int
    logical_document_id: int
    logical_document_status: str
    file_name: str
    mime_type: str
    file_size_bytes: int
    content_hash: str
    is_active: bool
    storage_key: str | None
    metadata_json: dict[str, object] | None
    status: str
    existing_chunk_ids: tuple[int, ...]


@dataclass(frozen=True)
class _LogicalDocumentIndexSnapshot:
    logical_document_id: int
    status: str


@dataclass(frozen=True)
class _ChunkSnapshot:
    document_chunk_id: int
    document_version_id: int
    chunk_index: int
    chunk_hash: str
    content_text: str
    token_count: int | None
    char_count: int | None
    page_from: int | None
    page_to: int | None
    section_title: str | None
    metadata_json: dict[str, object] | None
    modality: str
    created_at: datetime | None


class DocumentIngestHandler:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        repository: DocumentRepository | None = None,
        job_repository: JobRepository | None = None,
        storage: DocumentStorage | None = None,
        dispatcher: ExtractorDispatcher | None = None,
        settings: Settings | None = None,
        indexing_service: DocumentIndexingService | None = None,
        graph_index_service: GraphIndexService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository or DocumentRepository()
        self.job_repository = job_repository or JobRepository()
        self.settings = settings or get_settings()
        self.storage = storage or create_document_storage(self.settings)
        self.dispatcher = dispatcher or ExtractorDispatcher()
        self.indexing_service = indexing_service or create_document_indexing_service(self.settings)
        self.graph_index_service = graph_index_service or GraphIndexService()

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

        cleanup_result = self._cleanup_existing_artifacts(context, snapshot)
        if cleanup_result is not None:
            return cleanup_result

        if snapshot.storage_key is None:
            return self._fail_version(
                context,
                snapshot.document_version_id,
                "storage_file_missing",
            )

        try:
            if not self.storage.exists(storage_key=snapshot.storage_key):
                return self._fail_version(
                    context, snapshot.document_version_id, "storage_file_missing"
                )
            with self.storage.materialize(storage_key=snapshot.storage_key) as file_path:
                extracted = self._extract(file_path, snapshot)
            metadata = metadata_from_extracted_document(extracted)
            if metadata.text_char_count > self.settings.ingest_max_extracted_text_chars:
                return self._fail_version(
                    context, snapshot.document_version_id, "text_extraction_failed"
                )
            chunks = self._chunk(extracted, snapshot.document_version_id)
            stored_or_result = self._store_chunks(context, snapshot, metadata, chunks)
            if isinstance(stored_or_result, JobHandlerResult):
                return stored_or_result
            return self._embed_index_and_finalize(context, snapshot, metadata, stored_or_result)
        except ExtractionError as exc:
            return self._fail_version(context, snapshot.document_version_id, exc.error_code)
        except ChunkingError as exc:
            return self._fail_version(context, snapshot.document_version_id, exc.error_code)
        except DocumentStorageError as exc:
            error_code = (
                "storage_file_missing"
                if exc.error_code == "storage_file_missing"
                else "internal_error"
            )
            return self._fail_version(context, snapshot.document_version_id, error_code)
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
            if payload_logical_document_id is not None and not _is_positive_int(
                payload_logical_document_id
            ):
                db.rollback()
                return _failed("validation_error")
            if (
                payload_logical_document_id is not None
                and cast(int, payload_logical_document_id) != version.logical_document_id
            ):
                db.rollback()
                return _failed("validation_error")

            existing_chunk_ids = tuple(
                self.repository.chunk_ids_by_document_version(
                    db, document_version_id=document_version_id
                )
            )
            snapshot = _VersionSnapshot(
                document_version_id=version.document_version_id,
                logical_document_id=version.logical_document_id,
                logical_document_status=document.status,
                file_name=version.file_name,
                mime_type=version.mime_type,
                file_size_bytes=version.file_size_bytes,
                content_hash=version.content_hash,
                is_active=version.is_active,
                storage_key=version.storage_key,
                metadata_json=version.metadata_json,
                status=version.status,
                existing_chunk_ids=existing_chunk_ids,
            )
            if document.status == "archived" or version.status == "archived":
                if version.status != "archived":
                    self.job_repository.assert_ownership(
                        db,
                        job_id=context.job_id,
                        worker_instance_id=context.worker_instance_id,
                    )
                    self.repository.mark_version_failed(
                        db,
                        version=version,
                        error_code="document_version_not_ingestable",
                        updated_at=_now(),
                    )
                    db.commit()
                else:
                    db.rollback()
                return _failed("document_version_not_ingestable")
            if version.status != "ready":
                self.job_repository.assert_ownership(
                    db,
                    job_id=context.job_id,
                    worker_instance_id=context.worker_instance_id,
                )
                self.repository.reset_version_for_ingest(db, version=version, updated_at=_now())
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

    def _cleanup_existing_artifacts(
        self, context: JobExecutionContext, snapshot: _VersionSnapshot
    ) -> JobHandlerResult | None:
        if not snapshot.existing_chunk_ids:
            return None
        try:
            self.indexing_service.cleanup_document_points(
                document_version_id=snapshot.document_version_id,
                document_chunk_ids=snapshot.existing_chunk_ids,
            )
        except LeaseLostError:
            raise
        except Exception:
            logger.warning(
                "document ingest qdrant cleanup failed",
                extra={
                    "document_version_id": snapshot.document_version_id,
                    "chunk_count": len(snapshot.existing_chunk_ids),
                    "error_code": "qdrant_cleanup_failed",
                },
            )
            return self._mark_failed_after_cleanup(
                context,
                snapshot.document_version_id,
                "qdrant_cleanup_failed",
                delete_chunks=False,
            )

        db = self.session_factory()
        try:
            self.job_repository.assert_ownership(
                db,
                job_id=context.job_id,
                worker_instance_id=context.worker_instance_id,
            )
            self.repository.delete_chunks(db, document_version_id=snapshot.document_version_id)
            db.commit()
            return None
        except LeaseLostError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.error(
                "document ingest existing artifact cleanup failed",
                extra={
                    "document_version_id": snapshot.document_version_id,
                    "error_code": "ingest_cleanup_failed",
                },
            )
            return self._mark_failed_after_cleanup(
                context, snapshot.document_version_id, "ingest_cleanup_failed"
            )
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
                source_type=_snapshot_metadata_str(snapshot.metadata_json, "source_type"),
                source_url=_snapshot_metadata_str(snapshot.metadata_json, "source_url"),
                final_url=_snapshot_metadata_str(snapshot.metadata_json, "final_url"),
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

    def _store_chunks(
        self,
        context: JobExecutionContext,
        snapshot: _VersionSnapshot,
        metadata: DocumentIngestMetadata,
        chunks: list[Chunk],
    ) -> list[_ChunkSnapshot] | JobHandlerResult:
        if not chunks:
            return self._fail_version(context, snapshot.document_version_id, "no_chunks_created")
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
                return self._fail_version(
                    context,
                    snapshot.document_version_id,
                    "document_version_not_ingestable",
                )
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

        return self._load_chunk_snapshots(snapshot.document_version_id)

    def _embed_index_and_finalize(
        self,
        context: JobExecutionContext,
        snapshot: _VersionSnapshot,
        metadata: DocumentIngestMetadata,
        chunks: list[_ChunkSnapshot],
    ) -> JobHandlerResult:
        logical_document = _LogicalDocumentIndexSnapshot(
            logical_document_id=snapshot.logical_document_id,
            status=snapshot.logical_document_status,
        )
        try:
            indexed = self.indexing_service.index_chunks(
                logical_document=logical_document,
                document_version=snapshot,
                chunks=chunks,
            )
        except EmbeddingAdapterError as exc:
            return self._fail_version(context, snapshot.document_version_id, exc.error_code)
        except QdrantStoreError as exc:
            return self._fail_version(context, snapshot.document_version_id, exc.error_code)
        except Exception:
            logger.error(
                "document indexing failed",
                extra={
                    "document_version_id": snapshot.document_version_id,
                    "logical_document_id": snapshot.logical_document_id,
                    "error_code": "document_indexing_failed",
                },
            )
            return self._fail_version(
                context, snapshot.document_version_id, "document_indexing_failed"
            )

        try:
            self._mark_version_ready(context, snapshot.document_version_id, len(chunks))
        except LeaseLostError:
            raise
        except Exception:
            logger.error(
                "document ready update failed",
                extra={
                    "document_version_id": snapshot.document_version_id,
                    "logical_document_id": snapshot.logical_document_id,
                    "error_code": "document_ready_update_failed",
                },
            )
            cleanup_succeeded = self._cleanup_indexed_points(snapshot.document_version_id, chunks)
            return self._mark_failed_after_cleanup(
                context,
                snapshot.document_version_id,
                "document_ready_update_failed",
                delete_chunks=cleanup_succeeded,
            )

        chunk_stats = chunk_statistics(
            [chunk.char_count for chunk in chunks if chunk.char_count is not None]
        )
        return JobHandlerResult.succeeded(
            {
                "document_version_id": snapshot.document_version_id,
                "logical_document_id": snapshot.logical_document_id,
                "chunk_count": len(chunks),
                "indexed_count": indexed.indexed_count,
                "page_count": metadata.page_count,
                **chunk_stats.to_payload(),
                "status": "ready",
            }
        )

    def _mark_version_ready(
        self,
        context: JobExecutionContext,
        document_version_id: int,
        expected_chunk_count: int,
    ) -> None:
        db = self.session_factory()
        try:
            version = self.repository.get_version_by_id(
                db, document_version_id=document_version_id, for_update=True
            )
            if version is None:
                raise RuntimeError("document version missing during ready update")
            document = self.repository.get_document(
                db, logical_document_id=version.logical_document_id, for_update=True
            )
            if document is None or document.status == "archived" or version.status == "archived":
                raise RuntimeError("document version became non-ingestable")
            self.job_repository.assert_ownership(
                db,
                job_id=context.job_id,
                worker_instance_id=context.worker_instance_id,
            )
            stored_count = self.repository.count_chunks(
                db,
                document_version_id=document_version_id,
            )
            if stored_count != expected_chunk_count:
                raise RuntimeError("chunk count mismatch during ready update")
            self.repository.mark_version_ready(db, version=version, updated_at=_now())
            if graph_indexing_enabled(db):
                self.job_repository.create_job(
                    db,
                    job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
                    target_type="document_version",
                    target_id=document_version_id,
                    payload_json=self.graph_index_service.build_graph_index_job_payload(
                        document_version_id=document_version_id,
                        extractor_type=graph_extractor_type_override(db),
                    ),
                    priority=80,
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _fail_version(
        self,
        context: JobExecutionContext,
        document_version_id: int,
        error_code: str,
    ) -> JobHandlerResult:
        cleanup_ids = self._chunk_ids_for_cleanup(document_version_id)
        cleanup_succeeded = True
        if cleanup_ids:
            try:
                self.indexing_service.cleanup_document_points(
                    document_version_id=document_version_id,
                    document_chunk_ids=cleanup_ids,
                )
            except Exception:
                cleanup_succeeded = False
                logger.warning(
                    "document ingest qdrant cleanup failed after ingest failure",
                    extra={
                        "document_version_id": document_version_id,
                        "chunk_count": len(cleanup_ids),
                        "error_code": "qdrant_cleanup_failed",
                    },
                )
        return self._mark_failed_after_cleanup(
            context,
            document_version_id,
            error_code,
            delete_chunks=cleanup_succeeded,
        )

    def _mark_failed_after_cleanup(
        self,
        context: JobExecutionContext,
        document_version_id: int,
        error_code: str,
        *,
        delete_chunks: bool = True,
    ) -> JobHandlerResult:
        db = self.session_factory()
        try:
            version = self.repository.get_version_by_id(
                db, document_version_id=document_version_id, for_update=True
            )
            if version is not None and version.status != "archived":
                self.job_repository.assert_ownership(
                    db,
                    job_id=context.job_id,
                    worker_instance_id=context.worker_instance_id,
                )
                if delete_chunks:
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

    def _cleanup_indexed_points(
        self, document_version_id: int, chunks: Sequence[_ChunkSnapshot]
    ) -> bool:
        try:
            self.indexing_service.cleanup_document_points(
                document_version_id=document_version_id,
                document_chunk_ids=[chunk.document_chunk_id for chunk in chunks],
            )
            return True
        except Exception:
            logger.warning(
                "document ingest qdrant cleanup failed after ready update failure",
                extra={
                    "document_version_id": document_version_id,
                    "chunk_count": len(chunks),
                    "error_code": "qdrant_cleanup_failed",
                },
            )
            return False

    def _chunk_ids_for_cleanup(self, document_version_id: int) -> list[int]:
        db = self.session_factory()
        try:
            return self.repository.chunk_ids_by_document_version(
                db, document_version_id=document_version_id
            )
        finally:
            db.close()

    def _load_chunk_snapshots(self, document_version_id: int) -> list[_ChunkSnapshot]:
        db = self.session_factory()
        try:
            chunks = self.repository.list_chunks_for_embedding(
                db, document_version_id=document_version_id
            )
            return [_chunk_snapshot(chunk) for chunk in chunks]
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
        "metadata_json": chunk.metadata_json,
        "modality": chunk.modality,
    }


def _chunk_snapshot(chunk: DocumentChunk) -> _ChunkSnapshot:
    return _ChunkSnapshot(
        document_chunk_id=chunk.document_chunk_id,
        document_version_id=chunk.document_version_id,
        chunk_index=chunk.chunk_index,
        chunk_hash=chunk.chunk_hash,
        content_text=chunk.content_text,
        token_count=chunk.token_count,
        char_count=chunk.char_count,
        page_from=chunk.page_from,
        page_to=chunk.page_to,
        section_title=chunk.section_title,
        metadata_json=chunk.metadata_json,
        modality=chunk.modality,
        created_at=chunk.created_at,
    )


def _failed(error_code: str) -> JobHandlerResult:
    return JobHandlerResult.failed(
        error_code=error_code,
        error_message=_SAFE_MESSAGES.get(error_code, _SAFE_MESSAGES["internal_error"]),
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _now() -> datetime:
    return datetime.now(UTC)


def _snapshot_metadata_str(value: object, key: str) -> str | None:
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    return item if isinstance(item, str) and item else None
