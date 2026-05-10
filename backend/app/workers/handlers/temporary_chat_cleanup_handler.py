from __future__ import annotations

from app.workers.handlers.base import JobExecutionContext, JobHandlerResult


class TemporaryChatCleanupHandler:
    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        return JobHandlerResult.failed(
            error_code="job_handler_not_implemented",
            error_message="Temporary chat cleanup handler is not implemented in PR-09.",
        )
