from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Job
from app.workers import worker_main


def test_document_ingest_failure_updates_job_in_owner_session(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(worker_main, "SessionLocal", session_factory)
    monkeypatch.setattr(
        worker_main,
        "get_settings",
        lambda: SimpleNamespace(storage_root="unused"),
    )

    with session_factory() as db:
        db.add(
            Job(
                job_id=1,
                job_type="document_ingest",
                status="queued",
                payload_json={"document_version_id": 999},
            )
        )
        db.commit()

    assert worker_main.run_once() is True

    with session_factory() as db:
        job = db.scalar(select(Job).where(Job.job_id == 1))
        assert job is not None
        assert job.status == "failed"
        assert job.error_code == "document_version_not_found"
        assert job.finished_at is not None
