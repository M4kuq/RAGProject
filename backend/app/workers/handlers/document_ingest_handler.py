from __future__ import annotations

from app.workers.handlers.base import JobExecutionContext, JobHandlerResult


class DocumentIngestHandler:
    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        document_version_id = context.payload.get("document_version_id")
        if not _is_positive_int(document_version_id):
            return JobHandlerResult.failed(
                error_code="validation_error",
                error_message="Job payload is invalid.",
            )
        if context.target_type != "document_version" or context.target_id != document_version_id:
            return JobHandlerResult.failed(
                error_code="validation_error",
                error_message="Job payload is invalid.",
            )
        return _not_implemented()


def _not_implemented() -> JobHandlerResult:
    return JobHandlerResult.failed(
        error_code="job_handler_not_implemented",
        error_message="Document ingest handler is not implemented in PR-09.",
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and value > 0
