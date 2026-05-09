from __future__ import annotations

from app.workers.handlers.base import JobExecutionContext, JobHandlerResult


class QdrantMirrorUpdateHandler:
    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        if not context.target_type or context.target_id is None:
            return JobHandlerResult.failed(
                error_code="validation_error",
                error_message="Job payload is invalid.",
            )
        return JobHandlerResult.failed(
            error_code="job_handler_not_implemented",
            error_message="Qdrant mirror update handler is not implemented in PR-09.",
        )
