from __future__ import annotations

from typing import cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.responses import pagination_meta
from app.core.errors import JobActiveRetryExists, JobNotReady, ResourceNotFound, ValidationFailed
from app.core.job_utils import redact_error_message, redact_payload
from app.db.models import Job, User
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
            raise JobActiveRetryExists({"active_retry_job_id": active_retry.job_id})
        try:
            retry_job = self.repository.create_retry_job(
                db,
                source_job=job,
                requested_by_user_id=user.user_id,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise JobActiveRetryExists() from exc
        db.refresh(retry_job)
        return JobRetryResponse(
            source_job_id=source_job_id,
            job=self._to_detail(db, retry_job),
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
            error_message=redact_error_message(job.error_message),
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
            result_json=_redacted_payload_dict(job.result_json or {}) if job.result_json else None,
            source_job_id=source_job_id,
            active_retry_job_id=active_retry.job_id if active_retry is not None else None,
        )


def _redacted_payload_dict(value: object) -> dict[str, object]:
    redacted = redact_payload(value or {})
    if isinstance(redacted, dict):
        return {str(key): item for key, item in redacted.items()}
    return {}
