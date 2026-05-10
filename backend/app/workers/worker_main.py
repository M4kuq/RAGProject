from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import cast

from sqlalchemy.orm import Session

from app.core.job_utils import LeaseLostError
from app.db.models import Job
from app.db.session import SessionLocal
from app.repositories.job_repository import JobRepository
from app.workers.handlers.base import JobExecutionContext, JobHandlerResult
from app.workers.job_dispatcher import JobDispatcher
from app.workers.startup_checks import WorkerStartupError, run_startup_checks
from app.workers.worker_config import WorkerConfig, WorkerConfigError, load_worker_config

logger = logging.getLogger(__name__)


class WorkerRunner:
    def __init__(
        self,
        *,
        config: WorkerConfig,
        session_factory: Callable[[], Session] = SessionLocal,
        repository: JobRepository | None = None,
        dispatcher: JobDispatcher | None = None,
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.repository = repository or JobRepository()
        self.dispatcher = dispatcher or JobDispatcher()

    def run_once(self) -> int:
        contexts = self._acquire_contexts()
        for context in contexts:
            self._run_context(context)
        return len(contexts)

    def run_loop(
        self,
        *,
        max_iterations: int | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        iterations = 0
        should_stop = stop_requested or (lambda: False)
        while not should_stop():
            processed = self.run_once()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                return
            if processed == 0:
                time.sleep(self.config.poll_interval_seconds)

    def _acquire_contexts(self) -> list[JobExecutionContext]:
        db = self.session_factory()
        try:
            jobs = self.repository.acquire_jobs(
                db,
                worker_instance_id=self.config.worker_instance_id,
                enabled_job_types=self.config.enabled_job_types,
                lease_duration=self.config.lease_duration,
                batch_size=self.config.batch_size,
            )
            contexts = [_context_from_job(job, self.config.worker_instance_id) for job in jobs]
            db.commit()
            return contexts
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _run_context(self, context: JobExecutionContext) -> None:
        logger.info(
            "worker processing job",
            extra={
                "job_id": context.job_id,
                "job_type": context.job_type,
                "worker_instance_id": context.worker_instance_id,
            },
        )
        try:
            with _LeaseHeartbeat(
                config=self.config,
                session_factory=self.session_factory,
                repository=self.repository,
                job_id=context.job_id,
            ) as heartbeat:
                result = self.dispatcher.dispatch(context)
            if heartbeat.lease_lost:
                logger.warning(
                    "worker skipped terminal update because lease heartbeat was lost",
                    extra={
                        "job_id": context.job_id,
                        "worker_instance_id": context.worker_instance_id,
                    },
                )
                return
        except LeaseLostError:
            logger.warning(
                "worker lost job lease during dispatch",
                extra={"job_id": context.job_id, "worker_instance_id": context.worker_instance_id},
            )
            return
        except Exception:
            result = JobHandlerResult.failed(
                error_code="internal_error",
                error_message="Job handler failed.",
            )
        self._store_terminal_result(context, result)

    def _store_terminal_result(
        self, context: JobExecutionContext, result: JobHandlerResult
    ) -> None:
        db = self.session_factory()
        try:
            self.repository.renew_lease(
                db,
                job_id=context.job_id,
                worker_instance_id=self.config.worker_instance_id,
                lease_duration=self.config.lease_duration,
            )
            if result.status == "succeeded":
                self.repository.mark_succeeded(
                    db,
                    job_id=context.job_id,
                    worker_instance_id=self.config.worker_instance_id,
                    result_json=result.result_json,
                )
            else:
                self.repository.mark_failed(
                    db,
                    job_id=context.job_id,
                    worker_instance_id=self.config.worker_instance_id,
                    error_code=result.error_code or "internal_error",
                    error_message=result.error_message or "Job failed.",
                )
            db.commit()
        except LeaseLostError:
            db.rollback()
            logger.warning(
                "worker skipped terminal update because lease was lost",
                extra={"job_id": context.job_id, "worker_instance_id": context.worker_instance_id},
            )
        except Exception:
            db.rollback()
            logger.error(
                "worker failed to store terminal job state",
                extra={"job_id": context.job_id, "worker_instance_id": context.worker_instance_id},
            )
            raise
        finally:
            db.close()


def run_once(
    *,
    config: WorkerConfig | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    repository: JobRepository | None = None,
    dispatcher: JobDispatcher | None = None,
) -> int:
    runner = WorkerRunner(
        config=config or load_worker_config(),
        session_factory=session_factory,
        repository=repository,
        dispatcher=dispatcher,
    )
    return runner.run_once()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop = _StopSignal()
    signal.signal(signal.SIGTERM, stop.handle)
    signal.signal(signal.SIGINT, stop.handle)
    try:
        config = load_worker_config()
        run_startup_checks(config)
    except (WorkerConfigError, WorkerStartupError) as exc:
        logger.error("worker startup failed", extra={"error_code": "worker_startup_failed"})
        raise SystemExit(1) from exc

    logger.info(
        "worker started",
        extra={"worker_instance_id": config.worker_instance_id},
    )
    WorkerRunner(config=config).run_loop(stop_requested=stop.requested)


class _StopSignal:
    def __init__(self) -> None:
        self._requested = False

    def handle(self, signum: int, frame: FrameType | None) -> None:
        self._requested = True

    def requested(self) -> bool:
        return self._requested


def _context_from_job(
    job: Job,
    worker_instance_id: str,
) -> JobExecutionContext:
    payload = job.payload_json or {}
    return JobExecutionContext(
        job_id=int(job.job_id),
        job_type=str(job.job_type),
        target_type=cast(str | None, job.target_type),
        target_id=cast(int | None, job.target_id),
        payload=cast(dict[str, object], payload),
        worker_instance_id=worker_instance_id,
    )


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        config: WorkerConfig,
        session_factory: Callable[[], Session],
        repository: JobRepository,
        job_id: int,
    ) -> None:
        self.config = config
        self.session_factory = session_factory
        self.repository = repository
        self.job_id = job_id
        self.lease_lost = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"job-lease-{job_id}", daemon=True)

    def __enter__(self) -> _LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.wait(self.config.lease_renew_interval_seconds):
            db = self.session_factory()
            try:
                self.repository.renew_lease(
                    db,
                    job_id=self.job_id,
                    worker_instance_id=self.config.worker_instance_id,
                    lease_duration=self.config.lease_duration,
                )
                db.commit()
            except LeaseLostError:
                db.rollback()
                self.lease_lost = True
                self._stop.set()
            except Exception:
                db.rollback()
                self.lease_lost = True
                self._stop.set()
                logger.error(
                    "worker lease heartbeat failed",
                    extra={
                        "job_id": self.job_id,
                        "worker_instance_id": self.config.worker_instance_id,
                    },
                )
            finally:
                db.close()


if __name__ == "__main__":
    main()
