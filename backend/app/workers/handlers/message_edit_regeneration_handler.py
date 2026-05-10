from __future__ import annotations

from app.workers.handlers.base import JobExecutionContext, JobHandlerResult


class MessageEditRegenerationHandler:
    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        if context.target_type != "chat_message" or context.target_id is None:
            return JobHandlerResult.failed(
                error_code="validation_error",
                error_message="Job payload is invalid.",
            )
        return JobHandlerResult.failed(
            error_code="job_handler_not_implemented",
            error_message="Message edit regeneration handler is not implemented in PR-09.",
        )
