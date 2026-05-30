from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.responses import pagination_meta
from app.core.errors import JobActiveRetryExists, JobNotReady, ResourceNotFound, ValidationFailed
from app.core.job_utils import redact_error_message, sanitize_job_payload, sanitize_result_json
from app.db.models import DocumentVersion, Job, LogicalDocument, User
from app.repositories.job_repository import JobRepository
from app.schemas.common import PaginationMeta, PaginationParams
from app.schemas.jobs import JobDetail, JobItem, JobPayloadView, JobRetryResponse, JobStatus

VALID_JOB_STATUSES = {"queued", "running", "succeeded", "failed", "canceled"}


class JobService:
    def __init__(self, repository: JobRepository | None = None) -> None:
        self.repository = repository or JobRepository()

    def list_jobs(
        self,
        db: Session,
        *,
        status: str | None,
        job_type: str | None,
        target_type: str | None,
        target_id: int | None,
        pagination: PaginationParams,
    ) -> tuple[list[JobItem], PaginationMeta]:
        if status is not None and status not in VALID_JOB_STATUSES:
            raise ValidationFailed({"status": "invalid"})
        rows, total = self.repository.list_jobs(
            db,
            status=status,
            job_type=job_type,
            target_type=target_type,
            target_id=target_id,
            offset=pagination.offset,
            limit=pagination.page_size,
        )
        return [self._to_item(row) for row in rows], pagination_meta(pagination, total)

    def get_job_detail(self, db: Session, *, job_id: int) -> JobDetail:
        job = self.repository.get_job(db, job_id)
        if job is None:
            raise ResourceNotFound()
        return self._to_detail(db, job)

    def retry_job(self, db: Session, *, job_id: int, user: User) -> JobRetryResponse:
        job = self.repository.get_job(db, job_id, for_update=True)
        if job is None:
            raise ResourceNotFound()
        if job.status != "failed":
            raise JobNotReady()
        source_job_id = self.repository.get_source_job_id(job)
        active_retry = self.repository.find_active_retry(db, source_job_id=source_job_id)
        if active_retry is not None:
            raise JobActiveRetryExists(
                {"source_job_id": source_job_id, "active_retry_job_id": active_retry.job_id}
            )
        try:
            self._prepare_document_ingest_retry(db, job=job)
            retry_job = self.repository.create_retry_job(
                db,
                source_job=job,
                requested_by_user_id=user.user_id,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            active_retry = self.repository.find_active_retry(db, source_job_id=source_job_id)
            details = {"source_job_id": source_job_id}
            if active_retry is not None:
                details["active_retry_job_id"] = active_retry.job_id
            raise JobActiveRetryExists(details) from exc
        db.refresh(retry_job)
        return JobRetryResponse(
            job_id=retry_job.job_id,
            source_job_id=source_job_id,
            retry_count=retry_job.retry_count,
        )

    def _to_item(self, job: Job) -> JobItem:
        return JobItem(
            job_id=job.job_id,
            job_type=job.job_type,
            status=cast(JobStatus, job.status),
            priority=job.priority,
            target_type=job.target_type,
            target_id=job.target_id,
            retry_of_job_id=job.retry_of_job_id,
            retry_count=job.retry_count,
            created_by=job.created_by,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
            error_code=job.error_code,
            error_message=_safe_error_message(job),
            payload_view=JobPayloadView(payload=_redacted_payload_dict(job.payload_json)),
        )

    def _to_detail(self, db: Session, job: Job) -> JobDetail:
        source_job_id = self.repository.get_source_job_id(job)
        active_retry = self.repository.find_active_retry(db, source_job_id=source_job_id)
        item = self._to_item(job)
        return JobDetail(
            **item.model_dump(),
            locked_at=job.locked_at,
            lease_expires_at=job.lease_expires_at,
            result_json=sanitize_result_json(job.result_json or {}) if job.result_json else None,
            source_job_id=job.retry_of_job_id,
            active_retry_job_id=active_retry.job_id if active_retry is not None else None,
        )

    def _prepare_document_ingest_retry(self, db: Session, *, job: Job) -> None:
        if job.job_type != "document_ingest" or job.target_type != "document_version":
            return
        if job.target_id is None:
            raise ResourceNotFound()

        version = db.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_version_id == job.target_id)
            .with_for_update()
        )
        if version is None:
            raise ResourceNotFound()
        if version.status != "failed":
            return

        now = datetime.now(UTC)
        version.status = "processing"
        version.error_code = None
        version.is_active = False
        version.updated_at = now
        document = db.scalar(
            select(LogicalDocument)
            .where(LogicalDocument.logical_document_id == version.logical_document_id)
            .with_for_update()
        )
        if document is not None:
            document.updated_at = now


def _redacted_payload_dict(value: object) -> dict[str, object]:
    return sanitize_job_payload(cast(dict[str, object], value or {}))


def _safe_error_message(job: Job) -> str | None:
    if job.error_message:
        return redact_error_message(job.error_message)
    if job.status == "failed" or job.error_code:
        return redact_error_message(job.error_message)
    return None
