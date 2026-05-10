from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Job
from app.db.session import SessionLocal
from app.workers.worker_config import SUPPORTED_JOB_TYPES, WorkerConfig


class WorkerStartupError(RuntimeError):
    pass


def run_startup_checks(
    config: WorkerConfig,
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    if config.batch_size < 1:
        raise WorkerStartupError("WORKER_BATCH_SIZE must be positive.")
    if config.lease_duration.total_seconds() <= 0:
        raise WorkerStartupError("WORKER_LEASE_SECONDS must be positive.")
    if config.lease_renew_interval_seconds >= config.lease_duration.total_seconds():
        raise WorkerStartupError(
            "WORKER_LEASE_RENEW_INTERVAL_SECONDS must be shorter than WORKER_LEASE_SECONDS."
        )
    if config.enabled_job_types is not None:
        unknown = config.enabled_job_types - SUPPORTED_JOB_TYPES
        if unknown:
            raise WorkerStartupError(f"Unknown worker job_type: {', '.join(sorted(unknown))}")
    try:
        db = session_factory()
        try:
            db.execute(select(1))
            db.execute(select(Job.job_id).limit(1))
        finally:
            db.close()
    except Exception as exc:
        raise WorkerStartupError("Worker startup check failed.") from exc
