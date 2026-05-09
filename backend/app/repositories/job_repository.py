from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Job


class JobRepository:
    def create_job(
        self,
        db: Session,
        *,
        job_type: str,
        target_type: str,
        target_id: int,
        payload_json: dict[str, object],
        created_by: int | None,
        priority: int = 100,
    ) -> Job:
        job = Job(
            job_type=job_type,
            status="queued",
            priority=priority,
            target_type=target_type,
            target_id=target_id,
            payload_json=payload_json,
            created_by=created_by,
        )
        db.add(job)
        db.flush()
        return job
