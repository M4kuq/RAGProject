from __future__ import annotations

import hashlib
from datetime import UTC, datetime

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
    DocumentApproveResponse,
    DocumentArchiveResponse,
    DocumentChunkItem,
    DocumentDetail,
    DocumentDisplayStatus,
    DocumentItem,
    DocumentUploadResponse,
    DocumentVersionCreateResponse,
    DocumentVersionDetail,
    DocumentVersionSummary,
    normalize_document_title,
)
from app.services.audit_service import audit
from app.storage.file_storage import LocalFileStorage
from app.storage.validators import safe_title_from_file_name, validate_upload


class DocumentService:
    def __init__(
        self,
        repository: DocumentRepository | None = None,
        job_repository: JobRepository | None = None,
        storage: LocalFileStorage | None = None,
    ) -> None:
        self.repository = repository or DocumentRepository()
        self.job_repository = job_repository or JobRepository()
        self.storage = storage or LocalFileStorage()

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
            db.refresh(document)
            db.refresh(version)
            db.refresh(job)
        except Exception:
            db.rollback()
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
            db.refresh(version)
            db.refresh(job)
        except Exception:
            db.rollback()
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
        preview = chunk.content_text[:MAX_CHUNK_PREVIEW_LENGTH]
        return DocumentChunkItem(
            document_chunk_id=chunk.document_chunk_id,
            document_version_id=chunk.document_version_id,
            chunk_index=chunk.chunk_index,
            preview=preview,
            preview_truncated=len(chunk.content_text) > MAX_CHUNK_PREVIEW_LENGTH,
            page_from=chunk.page_from,
            page_to=chunk.page_to,
            section_title=chunk.section_title,
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
