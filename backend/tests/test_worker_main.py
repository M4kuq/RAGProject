from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.job_utils import LeaseLostError, redact_error_message, redact_payload
from app.db.base import Base
from app.db.models import DocumentVersion, Job, LogicalDocument, Role, User
from app.repositories.job_repository import JobRepository
from app.workers import worker_main
from app.workers.handlers.base import JobExecutionContext, JobHandlerResult
from app.workers.job_dispatcher import JobDispatcher
from app.workers.startup_checks import WorkerStartupError, run_startup_checks
from app.workers.worker_config import (
    WorkerConfig,
    WorkerConfigError,
    build_worker_instance_id,
    parse_enabled_job_types,
)
from app.workers.worker_main import WorkerRunner


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_worker_settings_parse_and_instance_id_generation() -> None:
    assert parse_enabled_job_types("all") is None
    assert parse_enabled_job_types("document_ingest,qdrant_mirror_update") == frozenset(
        {"document_ingest", "qdrant_mirror_update"}
    )
    with pytest.raises(WorkerConfigError):
        parse_enabled_job_types("document_ingest,unknown")
    with pytest.raises(WorkerConfigError):
        parse_enabled_job_types("all,unknown")

    worker_id = build_worker_instance_id(
        hostname="host name",
        process_id=123,
        boot_uuid="abcdef1234567890",
        instance_name="ci worker",
    )
    assert worker_id == "ci-worker:host-name:pid-123:boot-abcdef1234567890"
    assert len(worker_id) <= 100


def test_payload_and_error_redaction() -> None:
    redacted = redact_payload(
        {
            "document_version_id": 10,
            "api_token": "secret-value",
            "prompt": "sensitive prompt",
            "local_path": r"C:\Users\kei01\secret.txt",
        }
    )
    assert redacted == {
        "document_version_id": 10,
        "api_token": "[REDACTED]",
        "prompt": "[REDACTED]",
        "local_path": "[REDACTED]",
    }
    assert redact_error_message(r"failed at C:\Users\kei01\secret.txt") == (
        "Job failed with a redacted error."
    )


def test_acquire_prioritizes_queued_and_reclaims_without_overwriting_started_at(
    session_factory: sessionmaker[Session],
) -> None:
    repository = JobRepository()
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    original_started_at = now - timedelta(hours=5)
    with session_factory() as db:
        db.add_all(
            [
                Job(
                    job_id=1,
                    job_type="document_ingest",
                    status="queued",
                    priority=100,
                    payload_json={},
                    created_at=now - timedelta(hours=1),
                    updated_at=now - timedelta(hours=1),
                ),
                Job(
                    job_id=2,
                    job_type="document_ingest",
                    status="running",
                    priority=100,
                    payload_json={},
                    locked_by="stale",
                    locked_at=now - timedelta(hours=2),
                    lease_expires_at=now - timedelta(minutes=1),
                    started_at=original_started_at,
                    created_at=now - timedelta(hours=2),
                    updated_at=now - timedelta(hours=2),
                ),
            ]
        )
        db.commit()

        jobs = repository.acquire_jobs(
            db,
            worker_instance_id="worker-1",
            enabled_job_types=frozenset({"document_ingest"}),
            lease_duration=timedelta(minutes=5),
            batch_size=2,
            now=now,
        )
        assert [job.job_id for job in jobs] == [1, 2]
        db.commit()

    with session_factory() as db:
        reclaimed = db.get(Job, 2)
        assert reclaimed is not None
        assert reclaimed.started_at == original_started_at.replace(tzinfo=None)
        assert reclaimed.locked_by == "worker-1"


def test_acquire_filters_enabled_types_and_skips_ineligible_jobs(
    session_factory: sessionmaker[Session],
) -> None:
    repository = JobRepository()
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        db.add_all(
            [
                Job(job_id=1, job_type="document_ingest", status="queued", payload_json={}),
                Job(job_id=2, job_type="evaluation_run", status="queued", payload_json={}),
                Job(
                    job_id=3,
                    job_type="document_ingest",
                    status="running",
                    payload_json={},
                    locked_by="other",
                    locked_at=now,
                    lease_expires_at=now + timedelta(minutes=5),
                    started_at=now,
                ),
                Job(
                    job_id=4,
                    job_type="document_ingest",
                    status="succeeded",
                    payload_json={},
                    started_at=now,
                    finished_at=now,
                ),
            ]
        )
        db.commit()

        jobs = repository.acquire_jobs(
            db,
            worker_instance_id="worker-1",
            enabled_job_types=frozenset({"evaluation_run"}),
            lease_duration=timedelta(minutes=5),
            batch_size=10,
            now=now,
        )

    assert [job.job_id for job in jobs] == [2]


def test_lease_terminal_updates_and_retry_creation(session_factory: sessionmaker[Session]) -> None:
    repository = JobRepository()
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        db.add(Job(job_id=1, job_type="document_ingest", status="queued", payload_json={}))
        db.commit()
        acquired = repository.acquire_jobs(
            db,
            worker_instance_id="worker-1",
            enabled_job_types=None,
            lease_duration=timedelta(minutes=5),
            batch_size=1,
            now=now,
        )
        assert len(acquired) == 1
        repository.renew_lease(
            db,
            job_id=1,
            worker_instance_id="worker-1",
            lease_duration=timedelta(minutes=10),
            now=now + timedelta(minutes=1),
        )
        repository.mark_succeeded(
            db,
            job_id=1,
            worker_instance_id="worker-1",
            result_json={"ok": True},
            now=now + timedelta(minutes=2),
        )
        db.commit()

    with session_factory() as db:
        job = db.get(Job, 1)
        assert job is not None
        assert job.status == "succeeded"
        assert job.error_code is None
        assert job.lease_expires_at is None
        assert job.finished_at is not None
        assert job.result_json == {"result_redacted": True}
        assert job.locked_by == "worker-1"

        failed = Job(
            job_id=2,
            job_type="document_ingest",
            status="failed",
            payload_json={"api_token": "secret-value", "document_version_id": 9},
            error_code="job_handler_not_implemented",
            error_message="safe",
            started_at=now,
            finished_at=now,
        )
        db.add(failed)
        db.commit()
        retry = repository.create_retry_job(db, source_job=failed, requested_by_user_id=100)
        db.commit()
        assert retry.status == "queued"
        assert retry.retry_of_job_id == 2
        assert retry.payload_json == {
            "api_token": "[REDACTED]",
            "document_version_id": 9,
            "requested_by_user_id": 100,
        }
        active_retry = repository.find_active_retry(db, source_job_id=2)
        assert active_retry is not None
        assert active_retry.job_id == retry.job_id


def test_terminal_updates_raise_lease_lost_for_wrong_worker(
    session_factory: sessionmaker[Session],
) -> None:
    repository = JobRepository()
    now = datetime(2026, 5, 9, 1, 0, tzinfo=UTC)
    with session_factory() as db:
        db.add(
            Job(
                job_id=1,
                job_type="document_ingest",
                status="running",
                payload_json={},
                locked_by="worker-1",
                locked_at=now,
                lease_expires_at=now + timedelta(minutes=5),
                started_at=now,
            )
        )
        db.commit()

        with pytest.raises(LeaseLostError):
            repository.mark_failed(
                db,
                job_id=1,
                worker_instance_id="worker-2",
                error_code="safe_failure",
                error_message="safe",
            )
        db.rollback()

    with session_factory() as db:
        job = db.get(Job, 1)
        assert job is not None
        assert job.status == "running"
        assert job.error_code is None
        assert job.finished_at is None


def test_lease_lost_errors_do_not_mark_failed(session_factory: sessionmaker[Session]) -> None:
    config = _worker_config()
    with session_factory() as db:
        db.add(Job(job_id=1, job_type="document_ingest", status="queued", payload_json={}))
        db.commit()

    class LeaseLostDispatcher:
        def dispatch(self, context: JobExecutionContext) -> JobHandlerResult:
            raise LeaseLostError("lost")

    runner = WorkerRunner(
        config=config,
        session_factory=session_factory,
        dispatcher=cast(JobDispatcher, LeaseLostDispatcher()),
    )
    assert runner.run_once() == 1

    with session_factory() as db:
        job = db.scalar(select(Job).where(Job.job_id == 1))
        assert job is not None
        assert job.status == "running"
        assert job.error_code is None


def test_document_ingest_stub_failure_updates_version_in_terminal_transaction(
    session_factory: sessionmaker[Session],
) -> None:
    config = _worker_config()
    with session_factory() as db:
        role = Role(role_name="admin", description="Admin")
        db.add(role)
        db.flush()
        user = User(
            role_id=role.role_id,
            email="admin@example.com",
            display_name="Admin",
            status="active",
        )
        db.add(user)
        db.flush()
        document = LogicalDocument(owner_user_id=user.user_id, title="Guide")
        db.add(document)
        db.flush()
        version = DocumentVersion(
            document_version_id=1,
            logical_document_id=document.logical_document_id,
            version_no=1,
            content_hash="0" * 64,
            status="processing",
            file_name="guide.txt",
            mime_type="text/plain",
            file_size_bytes=10,
            created_by=user.user_id,
        )
        job = Job(
            job_id=1,
            job_type="document_ingest",
            status="queued",
            target_type="document_version",
            target_id=1,
            payload_json={"document_version_id": 1},
        )
        db.add_all([version, job])
        db.commit()

    runner = WorkerRunner(config=config, session_factory=session_factory)
    assert runner.run_once() == 1

    with session_factory() as db:
        stored_version = db.get(DocumentVersion, 1)
        stored_job = db.get(Job, 1)
        assert stored_version is not None
        assert stored_job is not None
        assert stored_version.status == "failed"
        assert stored_version.error_code == "job_handler_not_implemented"
        assert stored_job.status == "failed"
        assert stored_job.error_code == "job_handler_not_implemented"


def test_document_ingest_stub_treats_already_ready_version_as_success(
    session_factory: sessionmaker[Session],
) -> None:
    config = _worker_config()
    with session_factory() as db:
        role = Role(role_name="admin", description="Admin")
        db.add(role)
        db.flush()
        user = User(
            role_id=role.role_id,
            email="admin-ready@example.com",
            display_name="Admin",
            status="active",
        )
        db.add(user)
        db.flush()
        document = LogicalDocument(owner_user_id=user.user_id, title="Ready Guide")
        db.add(document)
        db.flush()
        version = DocumentVersion(
            document_version_id=1,
            logical_document_id=document.logical_document_id,
            version_no=1,
            content_hash="1" * 64,
            status="ready",
            file_name="ready.txt",
            mime_type="text/plain",
            file_size_bytes=10,
            created_by=user.user_id,
        )
        job = Job(
            job_id=1,
            job_type="document_ingest",
            status="queued",
            target_type="document_version",
            target_id=1,
            payload_json={"document_version_id": 1},
        )
        db.add_all([version, job])
        db.commit()

    runner = WorkerRunner(config=config, session_factory=session_factory)
    assert runner.run_once() == 1

    with session_factory() as db:
        stored_version = db.get(DocumentVersion, 1)
        stored_job = db.get(Job, 1)
        assert stored_version is not None
        assert stored_job is not None
        assert stored_version.status == "ready"
        assert stored_version.error_code is None
        assert stored_job.status == "succeeded"
        assert stored_job.error_code is None
        assert stored_job.result_json == {
            "handler_status": "already_ready",
            "document_version_id": 1,
        }


def test_default_dispatcher_returns_stub_results() -> None:
    dispatcher = JobDispatcher()
    base_context = JobExecutionContext(
        job_id=1,
        job_type="document_ingest",
        target_type="document_version",
        target_id=1,
        payload={"document_version_id": 1},
        worker_instance_id="worker-1",
    )

    result = dispatcher.dispatch(base_context)
    assert result.status == "failed"
    assert result.error_code == "job_handler_not_implemented"

    unknown = dispatcher.dispatch(
        JobExecutionContext(
            job_id=2,
            job_type="unknown",
            target_type=None,
            target_id=None,
            payload={},
            worker_instance_id="worker-1",
        )
    )
    assert unknown.status == "failed"
    assert unknown.error_code == "unknown_job_type"


def test_run_loop_stops_at_max_iterations(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyRunner(WorkerRunner):
        calls = 0

        def run_once(self) -> int:
            self.calls += 1
            return 0

    sleeps: list[float] = []
    monkeypatch.setattr(worker_main.time, "sleep", sleeps.append)
    runner = EmptyRunner(config=_worker_config())

    runner.run_loop(max_iterations=1)

    assert runner.calls == 1
    assert sleeps == []


def test_startup_checks_reject_invalid_lease_interval() -> None:
    with pytest.raises(WorkerStartupError):
        run_startup_checks(
            WorkerConfig(
                poll_interval_seconds=0,
                batch_size=1,
                lease_duration=timedelta(seconds=10),
                lease_renew_interval_seconds=10,
                shutdown_grace_seconds=30,
                enabled_job_types=None,
                worker_instance_id="worker-1",
            )
        )


def test_startup_checks_reject_unknown_enabled_type() -> None:
    with pytest.raises(WorkerStartupError):
        run_startup_checks(
            WorkerConfig(
                poll_interval_seconds=0,
                batch_size=1,
                lease_duration=timedelta(seconds=10),
                lease_renew_interval_seconds=1,
                shutdown_grace_seconds=30,
                enabled_job_types=frozenset({"unknown"}),
                worker_instance_id="worker-1",
            )
        )


def test_worker_single_iteration_marks_success_failure_and_unknown(
    session_factory: sessionmaker[Session],
) -> None:
    config = _worker_config(batch_size=3)
    with session_factory() as db:
        db.add_all(
            [
                Job(job_id=1, job_type="success", status="queued", payload_json={}),
                Job(job_id=2, job_type="failure", status="queued", payload_json={}),
                Job(job_id=3, job_type="unknown", status="queued", payload_json={}),
            ]
        )
        db.commit()

    class StaticDispatcher:
        def dispatch(self, context: JobExecutionContext) -> JobHandlerResult:
            if context.job_type == "success":
                return JobHandlerResult.succeeded(
                    {"handled": True, "prompt": "hidden", "content": "raw"}
                )
            if context.job_type == "failure":
                return JobHandlerResult.failed(error_code="safe_failure", error_message="safe")
            return JobHandlerResult.failed(
                error_code="unknown_job_type", error_message="Unknown job type."
            )

    runner = WorkerRunner(
        config=config,
        session_factory=session_factory,
        dispatcher=cast(JobDispatcher, StaticDispatcher()),
    )
    assert runner.run_once() == 3

    with session_factory() as db:
        jobs = {job.job_id: job for job in db.scalars(select(Job)).all()}
        assert jobs[1].status == "succeeded"
        assert jobs[1].result_json == {"handled": True}
        assert jobs[2].status == "failed"
        assert jobs[2].error_code == "safe_failure"
        assert jobs[3].status == "failed"
        assert jobs[3].error_code == "unknown_job_type"


def _worker_config(batch_size: int = 1) -> WorkerConfig:
    return WorkerConfig(
        poll_interval_seconds=0,
        batch_size=batch_size,
        lease_duration=timedelta(minutes=5),
        lease_renew_interval_seconds=60,
        shutdown_grace_seconds=30,
        enabled_job_types=None,
        worker_instance_id="worker-1",
    )
