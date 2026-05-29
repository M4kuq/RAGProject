from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.responses import pagination_meta
from app.core.config import get_settings
from app.core.errors import (
    ActiveVersionConflict,
    DocumentArchived,
    DocumentVersionNotApprovable,
    ResourceNotFound,
    ValidationFailed,
)
from app.db.models import DocumentChunk, DocumentVersion, Job, LogicalDocument, User
from app.repositories.document_repository import DocumentRepository
from app.repositories.job_repository import JobRepository
from app.schemas.common import PaginationMeta, PaginationParams
from app.schemas.documents import (
    MAX_CHUNK_PREVIEW_LENGTH,
    MAX_DOCUMENT_TITLE_LENGTH,
    DocumentApproveResponse,
    DocumentArchiveResponse,
    DocumentChunkDiffItem,
    DocumentChunkDiffSide,
    DocumentChunkItem,
    DocumentDetail,
    DocumentDisplayStatus,
    DocumentItem,
    DocumentMetadataDiffItem,
    DocumentUploadResponse,
    DocumentUrlIngestRequest,
    DocumentVersionCompareResponse,
    DocumentVersionCompareSummary,
    DocumentVersionCreateResponse,
    DocumentVersionDetail,
    DocumentVersionSummary,
    normalize_document_title,
)
from app.services.audit_service import audit
from app.services.source_locator_service import (
    LocatedChunk,
    safe_chunk_metadata,
    source_side_for_chunk,
)
from app.services.url_fetch_service import UrlFetchResult, UrlFetchService, redact_url_for_display
from app.storage.file_storage import LocalFileStorage
from app.storage.validators import safe_title_from_file_name, validate_upload

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:^|\s)(?:export\s+)?"
    r"([A-Z0-9_.-]*(?:api[_-]?key|secret|password|token|credential)[A-Z0-9_.-]*)"
    r"\s*[:=]\s*\S+"
)
_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


class UrlFetcher(Protocol):
    def fetch(self, url: str) -> UrlFetchResult: ...


class DocumentService:
    def __init__(
        self,
        repository: DocumentRepository | None = None,
        job_repository: JobRepository | None = None,
        storage: LocalFileStorage | None = None,
        url_fetcher: UrlFetcher | None = None,
    ) -> None:
        self.repository = repository or DocumentRepository()
        self.job_repository = job_repository or JobRepository()
        self.storage = storage or LocalFileStorage()
        self.url_fetcher = url_fetcher or UrlFetchService()

    def list_documents(
        self,
        db: Session,
        *,
        status: str | None,
        query: str | None,
        display_status: str | None,
        pagination: PaginationParams,
    ) -> tuple[list[DocumentItem], PaginationMeta]:
        self._validate_document_status(status)
        self._validate_display_status(display_status)
        normalized_status = self._document_status_filter(status, display_status)
        if normalized_status is None:
            return [], pagination_meta(pagination, 0)
        normalized_query = query.strip() if query and query.strip() else None
        if normalized_query and len(normalized_query) > 255:
            raise ValidationFailed(details=[{"field": "q", "reason": "q is too long."}])

        documents, total = self.repository.list_documents(
            db,
            status=normalized_status,
            query=normalized_query,
            latest_version_filter=self._display_status_filter(display_status),
            pagination=pagination,
        )
        return self._document_items(db, documents), pagination_meta(pagination, total)

    def get_document_detail(self, db: Session, *, logical_document_id: int) -> DocumentDetail:
        document = self.repository.get_document(db, logical_document_id=logical_document_id)
        if document is None:
            raise ResourceNotFound()
        versions, _ = self.repository.list_versions(
            db, logical_document_id=document.logical_document_id, pagination=None
        )
        version_ids = [version.document_version_id for version in versions]
        chunk_counts = self.repository.chunk_counts_by_version_ids(
            db, document_version_ids=version_ids
        )
        item = self._document_items(db, [document])[0]
        return DocumentDetail(
            **item.model_dump(),
            versions=[
                self._version_summary(
                    document,
                    version,
                    chunk_count=chunk_counts.get(version.document_version_id, 0),
                )
                for version in versions
            ],
        )

    def upload_document(
        self,
        db: Session,
        *,
        user: User,
        title: str | None,
        filename: str | None,
        content_type: str | None,
        content: bytes,
        request_id: str | None,
    ) -> DocumentUploadResponse:
        settings = get_settings()
        upload = validate_upload(
            filename=filename,
            content_type=content_type,
            content=content,
            max_bytes=settings.upload_max_bytes,
            allowed_extensions=settings.upload_allowed_extensions,
        )
        normalized_title = self._normalize_title(
            title, fallback=safe_title_from_file_name(upload.file_name)
        )
        content_hash = hashlib.sha256(content).hexdigest()
        storage_key = self.storage.build_storage_key(file_name=upload.file_name)
        storage_saved = False
        committed = False
        try:
            document = self.repository.create_logical_document(
                db,
                owner_user_id=user.user_id,
                title=normalized_title,
            )
            version = self.repository.create_version(
                db,
                logical_document_id=document.logical_document_id,
                version_no=1,
                content_hash=content_hash,
                file_name=upload.file_name,
                mime_type=upload.mime_type,
                file_size_bytes=upload.file_size_bytes,
                storage_key=storage_key,
                created_by=user.user_id,
            )
            self.storage.save_bytes(storage_key=storage_key, content=content)
            storage_saved = True
            job = self._create_ingest_job(db, user=user, document=document, version=version)
            audit(
                db,
                action="document.uploaded",
                actor_user_id=user.user_id,
                request_id=request_id,
                target_type="document_version",
                target_id=version.document_version_id,
                metadata={
                    "logical_document_id": document.logical_document_id,
                    "document_version_id": version.document_version_id,
                    "file_size_bytes": upload.file_size_bytes,
                    "mime_type": upload.mime_type,
                },
            )
            db.commit()
            committed = True
            db.refresh(document)
            db.refresh(version)
            db.refresh(job)
        except Exception:
            db.rollback()
            if storage_saved and not committed:
                with suppress(Exception):
                    self.storage.delete(storage_key=storage_key)
            raise
        document_item = self._document_items(db, [document])[0]
        version_detail = self._version_detail(document, version, chunk_count=0)
        return DocumentUploadResponse(
            logical_document_id=document.logical_document_id,
            document_version_id=version.document_version_id,
            job_id=job.job_id,
            ingest_status="queued",
            version_status=version.status,  # type: ignore[arg-type]
            display_status=self.display_status(document, version),
            document=document_item,
            version=version_detail,
        )

    def ingest_url(
        self,
        db: Session,
        *,
        user: User,
        payload: DocumentUrlIngestRequest,
        request_id: str | None,
    ) -> DocumentUploadResponse:
        fetched = self.url_fetcher.fetch(payload.url)
        settings = get_settings()
        upload = validate_upload(
            filename=fetched.file_name,
            content_type=fetched.content_type,
            content=fetched.content,
            max_bytes=settings.document_url_fetch_max_bytes,
            allowed_extensions=settings.upload_allowed_extensions,
        )
        normalized_title = self._normalize_title(
            payload.title,
            fallback=_title_from_fetched_url(fetched.safe_final_url, upload.file_name),
        )
        content_hash = hashlib.sha256(fetched.content).hexdigest()
        storage_key = self.storage.build_storage_key(file_name=upload.file_name)
        version_metadata = _url_version_metadata(fetched)
        storage_saved = False
        committed = False
        try:
            document = self.repository.create_logical_document(
                db,
                owner_user_id=user.user_id,
                title=normalized_title,
            )
            version = self.repository.create_version(
                db,
                logical_document_id=document.logical_document_id,
                version_no=1,
                content_hash=content_hash,
                file_name=upload.file_name,
                mime_type=upload.mime_type,
                file_size_bytes=upload.file_size_bytes,
                storage_key=storage_key,
                created_by=user.user_id,
                metadata_json=version_metadata,
            )
            self.storage.save_bytes(storage_key=storage_key, content=fetched.content)
            storage_saved = True
            job = self._create_ingest_job(db, user=user, document=document, version=version)
            audit(
                db,
                action="document.url_ingested",
                actor_user_id=user.user_id,
                request_id=request_id,
                target_type="document_version",
                target_id=version.document_version_id,
                metadata={
                    "logical_document_id": document.logical_document_id,
                    "document_version_id": version.document_version_id,
                    "source_type": "url",
                    "source_url": fetched.safe_source_url,
                    "final_url": fetched.safe_final_url,
                    "file_size_bytes": upload.file_size_bytes,
                    "mime_type": upload.mime_type,
                },
            )
            db.commit()
            committed = True
            db.refresh(document)
            db.refresh(version)
            db.refresh(job)
        except Exception:
            db.rollback()
            if storage_saved and not committed:
                with suppress(Exception):
                    self.storage.delete(storage_key=storage_key)
            raise
        document_item = self._document_items(db, [document])[0]
        version_detail = self._version_detail(document, version, chunk_count=0)
        return DocumentUploadResponse(
            logical_document_id=document.logical_document_id,
            document_version_id=version.document_version_id,
            job_id=job.job_id,
            ingest_status="queued",
            version_status=version.status,  # type: ignore[arg-type]
            display_status=self.display_status(document, version),
            document=document_item,
            version=version_detail,
        )

    def add_version(
        self,
        db: Session,
        *,
        user: User,
        logical_document_id: int,
        filename: str | None,
        content_type: str | None,
        content: bytes,
        request_id: str | None,
    ) -> tuple[DocumentVersionCreateResponse, bool]:
        storage_key: str | None = None
        storage_saved = False
        committed = False
        try:
            document = self.repository.get_document(
                db, logical_document_id=logical_document_id, for_update=True
            )
            if document is None:
                raise ResourceNotFound()
            if document.status == "archived":
                raise DocumentArchived()

            settings = get_settings()
            upload = validate_upload(
                filename=filename,
                content_type=content_type,
                content=content,
                max_bytes=settings.upload_max_bytes,
                allowed_extensions=settings.upload_allowed_extensions,
            )
            content_hash = hashlib.sha256(content).hexdigest()

            duplicate = self.repository.get_version_by_hash(
                db,
                logical_document_id=logical_document_id,
                content_hash=content_hash,
            )
            if duplicate is not None:
                audit(
                    db,
                    action="document.duplicate_skipped",
                    actor_user_id=user.user_id,
                    request_id=request_id,
                    target_type="document_version",
                    target_id=duplicate.document_version_id,
                    metadata={
                        "logical_document_id": logical_document_id,
                        "matched_document_version_id": duplicate.document_version_id,
                        "matched_version_no": duplicate.version_no,
                    },
                )
                db.commit()
                return (
                    DocumentVersionCreateResponse(
                        status="duplicate_content_skipped",
                        logical_document_id=logical_document_id,
                        matched_document_version_id=duplicate.document_version_id,
                        matched_version_no=duplicate.version_no,
                        reason="duplicate_content",
                    ),
                    False,
                )

            next_version_no = (
                self.repository.max_version_no(db, logical_document_id=logical_document_id) + 1
            )
            storage_key = self.storage.build_storage_key(file_name=upload.file_name)
            now = self._now()
            version = self.repository.create_version(
                db,
                logical_document_id=logical_document_id,
                version_no=next_version_no,
                content_hash=content_hash,
                file_name=upload.file_name,
                mime_type=upload.mime_type,
                file_size_bytes=upload.file_size_bytes,
                storage_key=storage_key,
                created_by=user.user_id,
            )
            self.repository.touch_document(db, document=document, updated_at=now)
            self.storage.save_bytes(storage_key=storage_key, content=content)
            storage_saved = True
            job = self._create_ingest_job(db, user=user, document=document, version=version)
            audit(
                db,
                action="document.version_added",
                actor_user_id=user.user_id,
                request_id=request_id,
                target_type="document_version",
                target_id=version.document_version_id,
                metadata={
                    "logical_document_id": logical_document_id,
                    "document_version_id": version.document_version_id,
                    "version_no": version.version_no,
                    "file_size_bytes": upload.file_size_bytes,
                    "mime_type": upload.mime_type,
                },
            )
            db.commit()
            committed = True
            db.refresh(version)
            db.refresh(job)
        except Exception:
            db.rollback()
            if storage_saved and storage_key is not None and not committed:
                with suppress(Exception):
                    self.storage.delete(storage_key=storage_key)
            raise
        return (
            DocumentVersionCreateResponse(
                status="created",
                logical_document_id=logical_document_id,
                document_version_id=version.document_version_id,
                job_id=job.job_id,
                ingest_status="queued",
                version_status=version.status,  # type: ignore[arg-type]
                display_status=self.display_status(document, version),
                version=self._version_detail(document, version, chunk_count=0),
            ),
            True,
        )

    def list_versions(
        self,
        db: Session,
        *,
        logical_document_id: int,
        pagination: PaginationParams,
    ) -> tuple[list[DocumentVersionDetail], PaginationMeta]:
        document = self.repository.get_document(db, logical_document_id=logical_document_id)
        if document is None:
            raise ResourceNotFound()
        versions, total = self.repository.list_versions(
            db, logical_document_id=logical_document_id, pagination=pagination
        )
        chunk_counts = self.repository.chunk_counts_by_version_ids(
            db, document_version_ids=[version.document_version_id for version in versions]
        )
        return (
            [
                self._version_detail(
                    document,
                    version,
                    chunk_count=chunk_counts.get(version.document_version_id, 0),
                )
                for version in versions
            ],
            pagination_meta(pagination, total),
        )

    def get_version_detail(
        self,
        db: Session,
        *,
        logical_document_id: int,
        document_version_id: int,
    ) -> DocumentVersionDetail:
        document, version = self._get_document_and_version(
            db,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
        )
        return self._version_detail(
            document,
            version,
            chunk_count=self.repository.count_chunks(
                db, document_version_id=version.document_version_id
            ),
        )

    def compare_versions(
        self,
        db: Session,
        *,
        logical_document_id: int,
        base_version_id: int,
        target_version_id: int,
    ) -> DocumentVersionCompareResponse:
        document = self.repository.get_document(db, logical_document_id=logical_document_id)
        if document is None:
            raise ResourceNotFound()
        base_version = self.repository.get_version(
            db,
            logical_document_id=logical_document_id,
            document_version_id=base_version_id,
        )
        target_version = self.repository.get_version(
            db,
            logical_document_id=logical_document_id,
            document_version_id=target_version_id,
        )
        if base_version is None or target_version is None:
            raise ResourceNotFound()

        base_chunks = self.repository.list_all_chunks(
            db, document_version_id=base_version.document_version_id
        )
        target_chunks = self.repository.list_all_chunks(
            db, document_version_id=target_version.document_version_id
        )
        chunk_diff_items, counts, truncated = _chunk_diff(
            document=document,
            base_version=base_version,
            target_version=target_version,
            base_chunks=base_chunks,
            target_chunks=target_chunks,
        )
        metadata_diff = _metadata_diff(
            base_version=base_version,
            target_version=target_version,
            base_chunk_count=len(base_chunks),
            target_chunk_count=len(target_chunks),
        )
        return DocumentVersionCompareResponse(
            logical_document_id=logical_document_id,
            base_version=self._version_detail(document, base_version, chunk_count=len(base_chunks)),
            target_version=self._version_detail(
                document, target_version, chunk_count=len(target_chunks)
            ),
            summary=DocumentVersionCompareSummary(
                added_chunks=counts["added"],
                removed_chunks=counts["removed"],
                changed_chunks=counts["changed"],
                unchanged_chunks=counts["unchanged"],
                metadata_changed=any(item.changed for item in metadata_diff),
                diff_items_returned=len(chunk_diff_items),
                diff_items_truncated=truncated,
            ),
            metadata_diff=metadata_diff,
            chunk_diff_items=chunk_diff_items,
        )

    def list_chunks(
        self,
        db: Session,
        *,
        logical_document_id: int,
        document_version_id: int,
        pagination: PaginationParams,
    ) -> tuple[list[DocumentChunkItem], PaginationMeta]:
        self._get_document_and_version(
            db,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
        )
        chunks, total = self.repository.list_chunks(
            db,
            document_version_id=document_version_id,
            pagination=pagination,
        )
        return [self._chunk_item(chunk) for chunk in chunks], pagination_meta(pagination, total)

    def approve_version(
        self,
        db: Session,
        *,
        user: User,
        logical_document_id: int,
        document_version_id: int,
        request_id: str | None,
    ) -> DocumentApproveResponse:
        try:
            document, version = self._get_document_and_version(
                db,
                logical_document_id=logical_document_id,
                document_version_id=document_version_id,
                for_update=True,
            )
            if document.status == "archived":
                raise DocumentArchived()
            if version.is_active:
                db.commit()
                return DocumentApproveResponse(
                    logical_document_id=logical_document_id,
                    document_version_id=version.document_version_id,
                    version_no=version.version_no,
                    status=version.status,  # type: ignore[arg-type]
                    is_active=True,
                    display_status=self.display_status(document, version),
                    previous_active_document_version_id=None,
                    result_code="already_active",
                    active_version=self._version_detail(
                        document,
                        version,
                        chunk_count=self.repository.count_chunks(
                            db, document_version_id=version.document_version_id
                        ),
                    ),
                )
            if version.status != "ready":
                raise DocumentVersionNotApprovable()
            now = self._now()
            previous_active_id = self.repository.set_active_version(
                db,
                logical_document_id=logical_document_id,
                version=version,
                updated_at=now,
            )
            mirror_job = self._create_qdrant_mirror_job(
                db,
                user=user,
                target_type="logical_document",
                target_id=logical_document_id,
                payload={
                    "logical_document_id": logical_document_id,
                    "document_version_id": version.document_version_id,
                    "mirror_action": "sync_payload",
                    "requested_by_user_id": user.user_id,
                },
            )
            self.repository.touch_document(db, document=document, updated_at=now)
            audit(
                db,
                action="document.version_approved",
                actor_user_id=user.user_id,
                request_id=request_id,
                target_type="document_version",
                target_id=version.document_version_id,
                metadata={
                    "logical_document_id": logical_document_id,
                    "previous_active_document_version_id": previous_active_id,
                },
            )
            db.commit()
            db.refresh(version)
            db.refresh(mirror_job)
        except IntegrityError as exc:
            db.rollback()
            raise ActiveVersionConflict() from exc
        except Exception:
            db.rollback()
            raise
        return DocumentApproveResponse(
            logical_document_id=logical_document_id,
            document_version_id=version.document_version_id,
            version_no=version.version_no,
            status=version.status,  # type: ignore[arg-type]
            is_active=version.is_active,
            display_status=self.display_status(document, version),
            previous_active_document_version_id=previous_active_id,
            result_code="approved",
            active_version=self._version_detail(
                document,
                version,
                chunk_count=self.repository.count_chunks(
                    db, document_version_id=version.document_version_id
                ),
            ),
            qdrant_mirror_job_id=mirror_job.job_id,
        )

    def archive_document(
        self,
        db: Session,
        *,
        user: User,
        logical_document_id: int,
        request_id: str | None,
    ) -> DocumentArchiveResponse:
        try:
            document = self.repository.get_document(
                db, logical_document_id=logical_document_id, for_update=True
            )
            if document is None:
                raise ResourceNotFound()
            if document.status == "archived":
                db.commit()
                return DocumentArchiveResponse(
                    logical_document_id=logical_document_id,
                    status="archived",
                    display_status="archived",
                    result_code="already_archived",
                )
            self.repository.archive_document(db, document=document, archived_at=self._now())
            mirror_job = self._create_qdrant_mirror_job(
                db,
                user=user,
                target_type="logical_document",
                target_id=logical_document_id,
                payload={
                    "logical_document_id": logical_document_id,
                    "mirror_action": "mark_inactive",
                    "requested_by_user_id": user.user_id,
                },
            )
            audit(
                db,
                action="document.archived",
                actor_user_id=user.user_id,
                request_id=request_id,
                target_type="logical_document",
                target_id=logical_document_id,
                metadata={"logical_document_id": logical_document_id},
            )
            db.commit()
            db.refresh(mirror_job)
        except Exception:
            db.rollback()
            raise
        return DocumentArchiveResponse(
            logical_document_id=logical_document_id,
            status="archived",
            display_status="archived",
            result_code="archived",
            qdrant_mirror_job_id=mirror_job.job_id,
        )

    def display_status(
        self, document: LogicalDocument, version: DocumentVersion | None
    ) -> DocumentDisplayStatus:
        if document.status == "archived":
            return "archived"
        if version is None:
            return "active"
        if version.status == "failed":
            return "failed"
        if version.status == "processing":
            return "processing"
        if version.status == "ready" and version.is_active:
            return "active"
        if version.status == "ready" and not version.is_active:
            return "pending_review"
        return "archived"

    def _create_ingest_job(
        self,
        db: Session,
        *,
        user: User,
        document: LogicalDocument,
        version: DocumentVersion,
    ) -> Job:
        return self.job_repository.create_job(
            db,
            job_type="document_ingest",
            target_type="document_version",
            target_id=version.document_version_id,
            payload_json={
                "logical_document_id": document.logical_document_id,
                "document_version_id": version.document_version_id,
                "requested_by_user_id": user.user_id,
            },
            created_by=user.user_id,
        )

    def _create_qdrant_mirror_job(
        self,
        db: Session,
        *,
        user: User,
        target_type: str,
        target_id: int,
        payload: dict[str, object],
    ) -> Job:
        return self.job_repository.create_job(
            db,
            job_type="qdrant_mirror_update",
            target_type=target_type,
            target_id=target_id,
            payload_json=payload,
            created_by=user.user_id,
        )

    def _document_items(self, db: Session, documents: list[LogicalDocument]) -> list[DocumentItem]:
        document_ids = [document.logical_document_id for document in documents]
        active_versions = self.repository.active_versions_by_document_ids(
            db, logical_document_ids=document_ids
        )
        latest_versions = self.repository.latest_versions_by_document_ids(
            db, logical_document_ids=document_ids
        )
        return [
            DocumentItem(
                logical_document_id=document.logical_document_id,
                document_name=document.title,
                title=document.title,
                status=document.status,  # type: ignore[arg-type]
                display_status=self.display_status(
                    document, latest_versions.get(document.logical_document_id)
                ),
                latest_version=self._optional_version_summary(
                    document, latest_versions.get(document.logical_document_id)
                ),
                active_version=self._optional_version_summary(
                    document, active_versions.get(document.logical_document_id)
                ),
                created_at=self._aware_utc(document.created_at),
                updated_at=self._aware_utc(document.updated_at),
            )
            for document in documents
        ]

    def _optional_version_summary(
        self, document: LogicalDocument, version: DocumentVersion | None
    ) -> DocumentVersionSummary | None:
        if version is None:
            return None
        return self._version_summary(document, version)

    def _version_summary(
        self,
        document: LogicalDocument,
        version: DocumentVersion,
        *,
        chunk_count: int | None = None,
    ) -> DocumentVersionSummary:
        return DocumentVersionSummary(
            document_version_id=version.document_version_id,
            version_no=version.version_no,
            status=version.status,  # type: ignore[arg-type]
            is_active=version.is_active,
            display_status=self.display_status(document, version),
            file_name=version.file_name,
            mime_type=version.mime_type,
            file_size_bytes=version.file_size_bytes,
            page_count=version.page_count,
            content_hash=version.content_hash,
            error_code=version.error_code,
            metadata_json=_safe_version_metadata(version.metadata_json),
            chunk_count=chunk_count,
            created_at=self._aware_utc(version.created_at),
            updated_at=self._aware_utc(version.updated_at),
        )

    def _version_detail(
        self,
        document: LogicalDocument,
        version: DocumentVersion,
        *,
        chunk_count: int,
    ) -> DocumentVersionDetail:
        return DocumentVersionDetail(
            **self._version_summary(document, version, chunk_count=chunk_count).model_dump(),
            logical_document_id=document.logical_document_id,
        )

    def _chunk_item(self, chunk: DocumentChunk) -> DocumentChunkItem:
        preview_length = min(get_settings().ingest_chunk_preview_chars, MAX_CHUNK_PREVIEW_LENGTH)
        preview = chunk.content_text[:preview_length]
        return DocumentChunkItem(
            document_chunk_id=chunk.document_chunk_id,
            document_version_id=chunk.document_version_id,
            chunk_index=chunk.chunk_index,
            preview=preview,
            preview_truncated=len(chunk.content_text) > preview_length,
            page_from=chunk.page_from,
            page_to=chunk.page_to,
            section_title=chunk.section_title,
            metadata_json=_safe_chunk_metadata(chunk.metadata_json),
            token_count=chunk.token_count,
            char_count=chunk.char_count,
            modality=chunk.modality,  # type: ignore[arg-type]
            chunk_hash=chunk.chunk_hash,
            created_at=self._aware_utc(chunk.created_at),
        )

    def _get_document_and_version(
        self,
        db: Session,
        *,
        logical_document_id: int,
        document_version_id: int,
        for_update: bool = False,
    ) -> tuple[LogicalDocument, DocumentVersion]:
        document = self.repository.get_document(
            db, logical_document_id=logical_document_id, for_update=for_update
        )
        if document is None:
            raise ResourceNotFound()
        version = self.repository.get_version(
            db,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
            for_update=for_update,
        )
        if version is None:
            raise ResourceNotFound()
        return document, version

    def _normalize_title(self, title: str | None, *, fallback: str) -> str:
        try:
            return normalize_document_title(title, fallback=fallback)
        except ValueError as exc:
            raise ValidationFailed(details=[{"field": "title", "reason": str(exc)}]) from exc

    def _validate_document_status(self, status: str | None) -> None:
        if status is None:
            return
        if status not in {"active", "archived"}:
            raise ValidationFailed(details=[{"field": "status", "reason": "Invalid status."}])

    def _validate_display_status(self, display_status: str | None) -> None:
        if display_status is None:
            return
        if display_status not in {"active", "pending_review", "processing", "failed", "archived"}:
            raise ValidationFailed(
                details=[{"field": "display_status", "reason": "Invalid display_status."}]
            )

    def _display_status_filter(self, display_status: str | None) -> tuple[str | None, bool | None]:
        if display_status in {None, "archived"}:
            return None, None
        if display_status == "active":
            return "ready", True
        if display_status == "pending_review":
            return "ready", False
        return display_status, None

    def _document_status_filter(self, status: str | None, display_status: str | None) -> str | None:
        required_status = "archived" if display_status == "archived" else None
        if display_status in {"active", "pending_review", "processing", "failed"}:
            required_status = "active"
        if status is not None and required_status is not None and status != required_status:
            return None
        return status or required_status or "active"

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _aware_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def _safe_chunk_metadata(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    safe: dict[str, object] = {}
    allowed_keys = {
        "parent_child_schema_version",
        "structure_type",
        "chunk_level",
        "parent_chunk_key",
        "child_chunk_key",
        "parent_title",
        "sheet_name",
        "row_from",
        "row_to",
        "column_from",
        "column_to",
        "table_index",
        "slide_number",
        "slide_title",
        "shape_count",
        "table_count",
        "html_title",
        "heading_path",
        "element_type",
        "element_index",
        "xml_root",
        "xml_path",
        "element_name",
        "source_type",
        "source_url",
    }
    for key, item in value.items():
        if key not in allowed_keys:
            continue
        if isinstance(item, str):
            redacted = (
                _safe_url_metadata_string(item)
                if key == "source_url"
                else _safe_metadata_string(item)
            )
            if redacted:
                safe[key] = redacted
        elif isinstance(item, bool):
            safe[key] = item
        elif isinstance(item, int | float):
            safe[key] = item
    return safe or None


def _chunk_diff(
    *,
    document: LogicalDocument,
    base_version: DocumentVersion,
    target_version: DocumentVersion,
    base_chunks: list[DocumentChunk],
    target_chunks: list[DocumentChunk],
) -> tuple[list[DocumentChunkDiffItem], dict[str, int], bool]:
    settings = get_settings()
    preview_max = settings.document_diff_preview_max_chars
    max_items = settings.document_diff_max_items
    threshold = settings.document_diff_text_similarity_threshold
    matches: list[tuple[DocumentChunk | None, DocumentChunk | None, str]] = []
    unmatched_base = {chunk.document_chunk_id: chunk for chunk in base_chunks}
    unmatched_target = {chunk.document_chunk_id: chunk for chunk in target_chunks}

    by_hash: dict[str, deque[DocumentChunk]] = defaultdict(deque)
    for chunk in target_chunks:
        by_hash[chunk.chunk_hash].append(chunk)
    for base_chunk in base_chunks:
        candidates = by_hash.get(base_chunk.chunk_hash)
        while candidates:
            target_chunk = candidates.popleft()
            if target_chunk.document_chunk_id in unmatched_target:
                matches.append((base_chunk, target_chunk, "chunk_hash"))
                unmatched_base.pop(base_chunk.document_chunk_id, None)
                unmatched_target.pop(target_chunk.document_chunk_id, None)
                break

    _match_by_key(
        matches,
        unmatched_base,
        unmatched_target,
        key_factory=_structural_key,
        reason="structural_key",
    )
    _match_by_key(
        matches,
        unmatched_base,
        unmatched_target,
        key_factory=lambda chunk: f"chunk_index:{chunk.chunk_index}",
        reason="chunk_index_similarity",
        min_similarity=threshold,
    )

    for unmatched_base_chunk in sorted(unmatched_base.values(), key=lambda item: item.chunk_index):
        matches.append((unmatched_base_chunk, None, "unmatched"))
    for unmatched_target_chunk in sorted(
        unmatched_target.values(), key=lambda item: item.chunk_index
    ):
        matches.append((None, unmatched_target_chunk, "unmatched"))

    counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    items: list[DocumentChunkDiffItem] = []
    for matched_base_chunk, matched_target_chunk, reason in sorted(
        matches,
        key=_chunk_match_sort_key,
    ):
        diff_type, similarity = _chunk_diff_type(matched_base_chunk, matched_target_chunk)
        counts[diff_type] += 1
        if len(items) >= max_items:
            continue
        items.append(
            DocumentChunkDiffItem(
                diff_type=diff_type,  # type: ignore[arg-type]
                base_chunk=_diff_side(
                    document=document,
                    version=base_version,
                    chunk=matched_base_chunk,
                    preview_max=preview_max,
                )
                if matched_base_chunk is not None
                else None,
                target_chunk=_diff_side(
                    document=document,
                    version=target_version,
                    chunk=matched_target_chunk,
                    preview_max=preview_max,
                )
                if matched_target_chunk is not None
                else None,
                similarity_score=similarity,
                match_reason=reason,
            )
        )
    return items, counts, len(matches) > max_items


def _chunk_match_sort_key(
    item: tuple[DocumentChunk | None, DocumentChunk | None, str],
) -> tuple[int, int]:
    base_chunk, target_chunk, _reason = item
    if base_chunk is None and target_chunk is None:
        return 0, 0
    if target_chunk is not None:
        primary = target_chunk.chunk_index
    elif base_chunk is not None:
        primary = base_chunk.chunk_index
    else:
        primary = 0
    secondary = base_chunk.chunk_index if base_chunk is not None else -1
    return primary, secondary


def _match_by_key(
    matches: list[tuple[DocumentChunk | None, DocumentChunk | None, str]],
    unmatched_base: dict[int, DocumentChunk],
    unmatched_target: dict[int, DocumentChunk],
    *,
    key_factory: Callable[[DocumentChunk], str | None],
    reason: str,
    min_similarity: float | None = None,
) -> None:
    target_by_key: dict[str, deque[DocumentChunk]] = defaultdict(deque)
    for target_chunk in sorted(unmatched_target.values(), key=lambda item: item.chunk_index):
        key = key_factory(target_chunk)
        if key:
            target_by_key[key].append(target_chunk)
    for base_chunk in sorted(list(unmatched_base.values()), key=lambda item: item.chunk_index):
        key = key_factory(base_chunk)
        if not key:
            continue
        candidates = target_by_key.get(key)
        while candidates:
            target_chunk = candidates.popleft()
            if target_chunk.document_chunk_id not in unmatched_target:
                continue
            if (
                min_similarity is not None
                and _text_similarity(base_chunk, target_chunk) < min_similarity
            ):
                continue
            matches.append((base_chunk, target_chunk, reason))
            unmatched_base.pop(base_chunk.document_chunk_id, None)
            unmatched_target.pop(target_chunk.document_chunk_id, None)
            break


def _structural_key(chunk: DocumentChunk) -> str | None:
    metadata = safe_chunk_metadata(chunk.metadata_json)
    for key in ("child_chunk_key", "parent_chunk_key"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return f"{key}:{value}"
    structure_type = metadata.get("structure_type")
    if structure_type == "excel_sheet":
        return ":".join(
            str(part)
            for part in (
                "excel",
                metadata.get("sheet_name"),
                metadata.get("table_index"),
                metadata.get("row_from"),
                metadata.get("row_to"),
            )
            if part is not None
        )
    if structure_type == "powerpoint_slide":
        slide_number = metadata.get("slide_number")
        if isinstance(slide_number, int):
            return f"pptx:slide:{slide_number}:chunk:{chunk.chunk_index}"
    if structure_type == "html_section":
        heading_path = metadata.get("heading_path")
        element_index = metadata.get("element_index")
        if isinstance(heading_path, str):
            return f"html:{heading_path}:element:{element_index}:chunk:{chunk.chunk_index}"
    if structure_type == "xml_element":
        xml_path = metadata.get("xml_path")
        element_index = metadata.get("element_index")
        if isinstance(xml_path, str):
            return f"xml:{xml_path}:element:{element_index}:chunk:{chunk.chunk_index}"
    if chunk.section_title:
        return f"section:{chunk.section_title}:chunk:{chunk.chunk_index}"
    return None


def _chunk_diff_type(
    base_chunk: DocumentChunk | None, target_chunk: DocumentChunk | None
) -> tuple[str, float | None]:
    if base_chunk is None:
        return "added", None
    if target_chunk is None:
        return "removed", None
    if base_chunk.chunk_hash == target_chunk.chunk_hash:
        return "unchanged", 1.0
    similarity = _text_similarity(base_chunk, target_chunk)
    return "changed", round(similarity, 4)


def _text_similarity(base_chunk: DocumentChunk, target_chunk: DocumentChunk) -> float:
    base_text = " ".join(base_chunk.content_text.split())
    target_text = " ".join(target_chunk.content_text.split())
    if not base_text and not target_text:
        return 1.0
    return SequenceMatcher(None, base_text, target_text).ratio()


def _diff_side(
    *,
    document: LogicalDocument,
    version: DocumentVersion,
    chunk: DocumentChunk,
    preview_max: int,
) -> DocumentChunkDiffSide:
    located = LocatedChunk(citation=None, chunk=chunk, version=version, document=document)
    return source_side_for_chunk(located, preview_max_chars=preview_max)


def _metadata_diff(
    *,
    base_version: DocumentVersion,
    target_version: DocumentVersion,
    base_chunk_count: int,
    target_chunk_count: int,
) -> list[DocumentMetadataDiffItem]:
    base_metadata = _safe_version_metadata(base_version.metadata_json) or {}
    target_metadata = _safe_version_metadata(target_version.metadata_json) or {}
    field_values: list[tuple[str, object | None, object | None]] = [
        (
            "file_name",
            _safe_metadata_string(base_version.file_name),
            _safe_metadata_string(target_version.file_name),
        ),
        ("mime_type", base_version.mime_type, target_version.mime_type),
        ("file_size_bytes", base_version.file_size_bytes, target_version.file_size_bytes),
        ("page_count", base_version.page_count, target_version.page_count),
        ("extractor_name", base_version.extractor_name, target_version.extractor_name),
        ("extractor_version", base_version.extractor_version, target_version.extractor_version),
        ("status", base_version.status, target_version.status),
        ("is_active", base_version.is_active, target_version.is_active),
        ("chunk_count", base_chunk_count, target_chunk_count),
        ("source_type", base_metadata.get("source_type"), target_metadata.get("source_type")),
        ("source_url", base_metadata.get("source_url"), target_metadata.get("source_url")),
        ("final_url", base_metadata.get("final_url"), target_metadata.get("final_url")),
        ("content_type", base_metadata.get("content_type"), target_metadata.get("content_type")),
    ]
    diff_items: list[DocumentMetadataDiffItem] = []
    for field, base_value, target_value in field_values:
        safe_base_value = _metadata_diff_value(field, base_value)
        safe_target_value = _metadata_diff_value(field, target_value)
        diff_items.append(
            DocumentMetadataDiffItem(
                field=field,
                base_value=safe_base_value,
                target_value=safe_target_value,
                changed=safe_base_value != safe_target_value,
            )
        )
    return diff_items


def _metadata_diff_value(field: str, value: object | None) -> str | int | bool | None:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return str(round(value, 4))
    if field in {"source_url", "final_url"}:
        return _safe_url_metadata_string(str(value))
    return _safe_metadata_string(str(value))


def _safe_version_metadata(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    safe: dict[str, object] = {}
    allowed_keys = {
        "source_type",
        "source_url",
        "final_url",
        "fetched_at",
        "content_type",
        "redirect_count",
    }
    for key, item in value.items():
        if key not in allowed_keys:
            continue
        if isinstance(item, str):
            redacted = (
                _safe_url_metadata_string(item)
                if key in {"source_url", "final_url"}
                else _safe_metadata_string(item)
            )
            if redacted:
                safe[key] = redacted
        elif isinstance(item, bool):
            safe[key] = item
        elif isinstance(item, int | float):
            safe[key] = item
    return safe or None


def _safe_metadata_string(value: str) -> str:
    normalized = " ".join(value.replace("\x00", " ").split())
    if (
        _SECRET_ASSIGNMENT_RE.search(normalized)
        or _URL_RE.search(normalized)
        or _EMAIL_RE.search(normalized)
    ):
        return "redacted"
    return normalized[:120]


def _safe_url_metadata_string(value: str) -> str:
    normalized = " ".join(value.replace("\x00", " ").split())
    safe_url = redact_url_for_display(normalized)
    if safe_url != "redacted":
        return safe_url[:200]
    if _SECRET_ASSIGNMENT_RE.search(normalized) or _EMAIL_RE.search(normalized):
        return "redacted"
    return normalized[:200]


def _url_version_metadata(fetched: UrlFetchResult) -> dict[str, object]:
    return {
        "source_type": "url",
        "source_url": fetched.safe_source_url,
        "final_url": fetched.safe_final_url,
        "fetched_at": fetched.fetched_at.isoformat(),
        "content_type": fetched.content_type,
        "redirect_count": fetched.redirect_count,
    }


def _title_from_fetched_url(safe_url: str, file_name: str) -> str:
    if safe_url and safe_url != "redacted":
        without_scheme = safe_url.split("://", 1)[-1].strip("/")
        if without_scheme:
            return without_scheme[:MAX_DOCUMENT_TITLE_LENGTH]
    return safe_title_from_file_name(file_name)
