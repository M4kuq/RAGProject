from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import Job
from app.db.session import SessionLocal
from app.ingest.qdrant import HttpQdrantClient, QdrantCollectionConfig, QdrantVectorStore
from app.workers.worker_config import SUPPORTED_JOB_TYPES, WorkerConfig


class WorkerStartupError(RuntimeError):
    pass


def run_startup_checks(
    config: WorkerConfig,
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> None:
    if config.poll_interval_seconds <= 0:
        raise WorkerStartupError("WORKER_POLL_INTERVAL_MS must be positive.")
    if config.batch_size < 1:
        raise WorkerStartupError("WORKER_BATCH_SIZE must be positive.")
    if config.lease_duration.total_seconds() <= 0:
        raise WorkerStartupError("WORKER_LEASE_SECONDS must be positive.")
    if config.lease_renew_interval_seconds <= 0:
        raise WorkerStartupError("WORKER_LEASE_RENEW_INTERVAL_SECONDS must be positive.")
    if config.lease_renew_interval_seconds >= config.lease_duration.total_seconds():
        raise WorkerStartupError(
            "WORKER_LEASE_RENEW_INTERVAL_SECONDS must be shorter than WORKER_LEASE_SECONDS."
        )
    if config.shutdown_grace_seconds <= 0:
        raise WorkerStartupError("WORKER_SHUTDOWN_GRACE_SECONDS must be positive.")
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
    settings = get_settings()
    if settings.qdrant_required and _needs_qdrant(config.enabled_job_types):
        try:
            QdrantVectorStore(
                client=HttpQdrantClient(
                    url=settings.qdrant_url,
                    timeout_seconds=settings.qdrant_timeout_seconds,
                ),
                config=QdrantCollectionConfig(
                    name=settings.qdrant_collection_name,
                    vector_dimension=settings.effective_embedding_dimension,
                    distance=settings.qdrant_distance,
                ),
                create_collection=settings.qdrant_create_collection,
            ).ensure_collection()
        except Exception as exc:
            raise WorkerStartupError("Qdrant startup check failed.") from exc


def _needs_qdrant(enabled_job_types: frozenset[str] | None) -> bool:
    if enabled_job_types is None:
        return True
    return bool(
        enabled_job_types & {"document_ingest", "qdrant_mirror_update", "qdrant_consistency_sweep"}
    )
