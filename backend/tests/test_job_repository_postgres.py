from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.db.models import Job
from app.db.session import SessionLocal
from app.repositories.job_repository import JobRepository


@pytest.mark.skipif(
    not get_settings().database_url.startswith("postgresql"),
    reason="PostgreSQL-specific SKIP LOCKED behavior is not available.",
)
def test_acquire_jobs_postgres_skip_locked_prevents_double_acquire() -> None:
    repository = JobRepository()
    job_type = f"test_skip_locked_{uuid4().hex[:12]}"
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with SessionLocal() as db:
        db.add_all(
            [
                Job(
                    job_type=job_type,
                    status="queued",
                    priority=100,
                    payload_json={},
                    created_at=now,
                    updated_at=now,
                ),
                Job(
                    job_type=job_type,
                    status="queued",
                    priority=100,
                    payload_json={},
                    created_at=now + timedelta(seconds=1),
                    updated_at=now + timedelta(seconds=1),
                ),
            ]
        )
        db.commit()

    session_one = SessionLocal()
    session_two = SessionLocal()
    try:
        first = repository.acquire_jobs(
            session_one,
            worker_instance_id="worker-one",
            enabled_job_types=frozenset({job_type}),
            lease_duration=timedelta(minutes=5),
            batch_size=1,
            now=now,
        )
        session_two.execute(text("SET LOCAL lock_timeout = '500ms'"))
        second = repository.acquire_jobs(
            session_two,
            worker_instance_id="worker-two",
            enabled_job_types=frozenset({job_type}),
            lease_duration=timedelta(minutes=5),
            batch_size=1,
            now=now,
        )

        assert len(first) == 1
        assert len(second) == 1
        assert first[0].job_id != second[0].job_id
    finally:
        session_two.rollback()
        session_one.rollback()
        session_two.close()
        session_one.close()
