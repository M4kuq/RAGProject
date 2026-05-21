from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.evaluation.fixtures import EvaluationFixtureError
from app.services.evaluation_service import EvaluationService
from app.workers.handlers.base import JobExecutionContext, JobHandlerResult


class EvaluationRunHandler:
    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        service_factory: Callable[[], EvaluationService] = EvaluationService,
    ) -> None:
        self.session_factory = session_factory
        self.service_factory = service_factory

    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        evaluation_run_id = _evaluation_run_id(context)
        if context.target_type != "evaluation_run" or evaluation_run_id is None:
            return JobHandlerResult.failed(
                error_code="validation_error",
                error_message="Job payload is invalid.",
            )
        with self.session_factory() as db:
            try:
                result = self.service_factory().run_job(
                    db,
                    evaluation_run_id=evaluation_run_id,
                    request_id=f"job:{context.job_id}",
                )
            except EvaluationFixtureError as exc:
                return JobHandlerResult.failed(
                    error_code=str(exc),
                    error_message="Evaluation run failed.",
                )
            except OperationalError:
                return JobHandlerResult.failed(
                    error_code="job_handler_not_implemented",
                    error_message="Evaluation run storage is not initialized.",
                )
            except Exception:
                return JobHandlerResult.failed(
                    error_code="internal_error",
                    error_message="Evaluation run failed.",
                )
        return JobHandlerResult.succeeded(result)


def _evaluation_run_id(context: JobExecutionContext) -> int | None:
    if context.target_id is not None:
        if isinstance(context.target_id, bool) or context.target_id < 1:
            return None
        return int(context.target_id)
    raw = context.payload.get("evaluation_run_id")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None
    return raw
