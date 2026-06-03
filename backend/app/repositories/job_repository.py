from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.job_utils import (
    LeaseLostError,
    original_source_job_id,
    redact_error_message,
    sanitize_job_payload,
    sanitize_result_json,
)
from app.db.models import Job
from app.workers.worker_config import SUPPORTED_JOB_TYPES


class JobRepository:
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
        job = Job(
            job_type=job_type,
            status="queued",
            priority=priority,
            target_type=target_type,
            target_id=target_id,
            payload_json=sanitize_job_payload(payload_json),
            created_by=created_by,
        )
        db.add(job)
        db.flush()
        return job

    def get_job(self, db: Session, job_id: int, *, for_update: bool = False) -> Job | None:
        stmt = select(Job).where(Job.job_id == job_id)
        if for_update:
            stmt = stmt.with_for_update()
        return db.scalar(stmt)

    def list_jobs(
        self,
        db: Session,
        *,
        status: str | None = None,
        job_type: str | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Job], int]:
        conditions = _filter_conditions(
            status=status,
            job_type=job_type,
            target_type=target_type,
            target_id=target_id,
        )
        total_stmt = select(func.count()).select_from(Job)
        rows_stmt = (
            select(Job)
            .order_by(Job.created_at.desc(), Job.job_id.desc())
            .offset(offset)
            .limit(limit)
        )
        if conditions:
            total_stmt = total_stmt.where(*conditions)
            rows_stmt = rows_stmt.where(*conditions)
        total = int(db.scalar(total_stmt) or 0)
        return list(db.scalars(rows_stmt).all()), total

    def acquire_jobs(
        self,
        db: Session,
        *,
        worker_instance_id: str,
        enabled_job_types: frozenset[str] | None,
        lease_duration: timedelta,
        batch_size: int,
        supported_job_types: frozenset[str] | None = SUPPORTED_JOB_TYPES,
        now: datetime | None = None,
    ) -> list[Job]:
        acquired_at = now or datetime.now(UTC)
        lease_expires_at = acquired_at + lease_duration
        conditions = [
            or_(
                Job.status == "queued",
                and_(
                    Job.status == "running",
                    Job.lease_expires_at.is_not(None),
                    Job.lease_expires_at < acquired_at,
                ),
            )
        ]
        if enabled_job_types is not None:
            conditions.append(Job.job_type.in_(enabled_job_types))
        elif supported_job_types is not None:
            conditions.append(Job.job_type.in_(supported_job_types))

        stmt = (
            select(Job)
            .where(*conditions)
            .order_by(
                case((Job.status == "queued", 0), else_=1),
                Job.priority.asc(),
                Job.created_at.asc(),
                Job.job_id.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        jobs = list(db.scalars(stmt).all())
        for job in jobs:
            job.status = "running"
            if job.started_at is None:
                job.started_at = acquired_at
            job.locked_by = worker_instance_id
            job.locked_at = acquired_at
            job.lease_expires_at = lease_expires_at
            job.updated_at = acquired_at
        db.flush()
        return jobs

    def renew_lease(
        self,
        db: Session,
        *,
        job_id: int,
        worker_instance_id: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> None:
        renewed_at = now or datetime.now(UTC)
        result = db.execute(
            update(Job)
            .where(
                Job.job_id == job_id,
                Job.status == "running",
                Job.locked_by == worker_instance_id,
            )
            .values(
                locked_at=renewed_at,
                lease_expires_at=renewed_at + lease_duration,
                updated_at=renewed_at,
            )
        )
        if _rowcount(result) == 0:
            raise LeaseLostError(f"Lease lost for job_id={job_id}")

    def assert_ownership(self, db: Session, *, job_id: int, worker_instance_id: str) -> None:
        exists = db.scalar(
            select(Job.job_id).where(
                Job.job_id == job_id,
                Job.status == "running",
                Job.locked_by == worker_instance_id,
            )
        )
        if exists is None:
            raise LeaseLostError(f"Lease lost for job_id={job_id}")

    def mark_succeeded(
        self,
        db: Session,
        *,
        job_id: int,
        worker_instance_id: str,
        result_json: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> None:
        finished_at = now or datetime.now(UTC)
        result = db.execute(
            update(Job)
            .where(
                Job.job_id == job_id,
                Job.status == "running",
                Job.locked_by == worker_instance_id,
            )
            .values(
                status="succeeded",
                finished_at=finished_at,
                result_json=sanitize_result_json(result_json),
                error_code=None,
                error_message=None,
                lease_expires_at=None,
                updated_at=finished_at,
            )
        )
        if _rowcount(result) == 0:
            raise LeaseLostError(f"Lease lost for job_id={job_id}")

    def mark_failed(
        self,
        db: Session,
        *,
        job_id: int,
        worker_instance_id: str,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> None:
        finished_at = now or datetime.now(UTC)
        result = db.execute(
            update(Job)
            .where(
                Job.job_id == job_id,
                Job.status == "running",
                Job.locked_by == worker_instance_id,
            )
            .values(
                status="failed",
                finished_at=finished_at,
                result_json=None,
                error_code=error_code,
                error_message=redact_error_message(error_message),
                lease_expires_at=None,
                updated_at=finished_at,
            )
        )
        if _rowcount(result) == 0:
            raise LeaseLostError(f"Lease lost for job_id={job_id}")

    def mark_canceled(
        self,
        db: Session,
        *,
        job_id: int,
        now: datetime | None = None,
    ) -> None:
        finished_at = now or datetime.now(UTC)
        db.execute(
            update(Job)
            .where(Job.job_id == job_id, Job.status.in_(("queued", "running")))
            .values(
                status="canceled",
                finished_at=finished_at,
                lease_expires_at=None,
                updated_at=finished_at,
            )
        )

    def get_source_job_id(self, job: Job) -> int:
        return original_source_job_id(job)

    def find_active_retry(self, db: Session, *, source_job_id: int) -> Job | None:
        return db.scalar(
            select(Job)
            .where(
                Job.retry_of_job_id == source_job_id,
                Job.status.in_(("queued", "running")),
            )
            .order_by(Job.created_at.asc(), Job.job_id.asc())
        )

    def create_retry_job(
        self,
        db: Session,
        *,
        source_job: Job,
        requested_by_user_id: int | None,
    ) -> Job:
        original_source_job_id = self.get_source_job_id(source_job)
        payload = sanitize_job_payload(_as_payload_dict(source_job.payload_json or {}))
        if requested_by_user_id is not None:
            payload["requested_by_user_id"] = requested_by_user_id
        retry_job = Job(
            job_type=source_job.job_type,
            status="queued",
            priority=source_job.priority,
            target_type=source_job.target_type,
            target_id=source_job.target_id,
            payload_json=payload,
            retry_of_job_id=original_source_job_id,
            retry_count=self.next_retry_count(db, source_job_id=original_source_job_id),
            created_by=requested_by_user_id,
        )
        db.add(retry_job)
        db.flush()
        return retry_job

    def next_retry_count(self, db: Session, *, source_job_id: int) -> int:
        max_retry_count = db.scalar(
            select(func.max(Job.retry_count)).where(Job.retry_of_job_id == source_job_id)
        )
        return int(max_retry_count or 0) + 1


def _filter_conditions(
    *,
    status: str | None,
    job_type: str | None,
    target_type: str | None,
    target_id: int | None,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if status is not None:
        conditions.append(Job.status == status)
    if job_type is not None:
        conditions.append(Job.job_type == job_type)
    if target_type is not None:
        conditions.append(Job.target_type == target_type)
    if target_id is not None:
        conditions.append(Job.target_id == target_id)
    return conditions


def _as_payload_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)
