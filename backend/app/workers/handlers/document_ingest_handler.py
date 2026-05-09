from __future__ import annotations

from datetime import UTC, datetime

from app.db.models import DocumentVersion
from app.workers.handlers.base import JobExecutionContext, JobHandlerResult


class DocumentIngestHandler:
    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        document_version_id = context.payload.get("document_version_id")
        if not _is_positive_int(document_version_id):
            return JobHandlerResult.failed(
                error_code="validation_error",
                error_message="Job payload is invalid.",
            )
        if context.session_factory is None:
            return _not_implemented()

        db = context.session_factory()
        try:
            version = db.get(DocumentVersion, document_version_id)
            if version is None:
                return JobHandlerResult.failed(
                    error_code="resource_not_found",
                    error_message="Document version was not found.",
                )
            if version.status == "ready":
                return JobHandlerResult.succeeded(
                    {
                        "handler_status": "already_ready",
                        "document_version_id": version.document_version_id,
                    }
                )
            if version.status != "archived":
                version.status = "failed"
                version.error_code = "job_handler_not_implemented"
                version.is_active = False
                version.updated_at = datetime.now(UTC)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return _not_implemented()


def _not_implemented() -> JobHandlerResult:
    return JobHandlerResult.failed(
        error_code="job_handler_not_implemented",
        error_message="Document ingest handler is not implemented in PR-09.",
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and value > 0
