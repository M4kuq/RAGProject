from __future__ import annotations

from http import HTTPStatus
from typing import Any

ERROR_MESSAGES: dict[str, str] = {
    "authentication_failed": "Authentication failed.",
    "auth_required": "Authentication required.",
    "permission_denied": "Permission denied.",
    "csrf_missing": "CSRF token is required.",
    "csrf_invalid": "CSRF token is invalid.",
    "rate_limit_exceeded": "Rate limit exceeded.",
    "resource_not_found": "Resource not found.",
    "validation_error": "Invalid request.",
    "business_validation_error": "Request cannot be processed.",
    "conflict": "Resource conflict.",
    "archived_session_readonly": "Archived session is read-only.",
    "document_archived": "Document is archived.",
    "document_version_not_approvable": "Document version is not approvable.",
    "active_version_conflict": "Active version conflict.",
    "job_not_ready": "Job is not ready for this operation.",
    "job_active_retry_exists": "An active retry already exists for this job.",
    "unknown_job_type": "Unknown job type.",
    "lease_lost": "Job lease was lost.",
    "worker_startup_failed": "Worker startup check failed.",
    "job_handler_not_implemented": "Job handler is not implemented.",
    "document_version_not_found": "Document version was not found.",
    "document_version_not_ingestable": "Document version cannot be ingested.",
    "storage_file_missing": "Stored file was not found.",
    "unsupported_file_type": "Unsupported file type.",
    "mime_type_mismatch": "File type does not match MIME type.",
    "text_extraction_failed": "Text extraction failed.",
    "empty_extracted_text": "Extracted text is empty.",
    "chunking_failed": "Chunking failed.",
    "no_chunks_created": "No chunks were created.",
    "document_chunk_insert_failed": "Document chunk insert failed.",
    "ingest_cleanup_failed": "Ingest cleanup failed.",
    "payload_too_large": "Payload too large.",
    "unsupported_media_type": "Unsupported media type.",
    "unsafe_file_rejected": "Unsafe file rejected.",
    "temporary_session_expired": "Temporary session has expired.",
    "temporary_session_not_archivable": "Temporary session cannot be archived.",
    "no_context_found": "No context found.",
    "internal_error": "Internal server error.",
    "internal_server_error": "Internal server error.",
}

DETAIL_CODE_ALIASES: dict[str, str] = {
    "invalid_credentials": "authentication_failed",
    "unauthenticated": "auth_required",
    "forbidden": "permission_denied",
    "csrf_required": "csrf_missing",
    "not_found": "resource_not_found",
}

STATUS_DEFAULT_CODES: dict[int, str] = {
    401: "auth_required",
    403: "permission_denied",
    404: "resource_not_found",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
}


class AppError(Exception):
    def __init__(
        self,
        code: str,
        status_code: int,
        message: str | None = None,
        details: object | None = None,
    ) -> None:
        super().__init__(message or ERROR_MESSAGES.get(code) or _status_message(status_code))
        self.code = code
        self.status_code = status_code
        self.message = message or ERROR_MESSAGES.get(code) or _status_message(status_code)
        self.details = details or {}


class AuthenticationRequired(AppError):
    def __init__(self) -> None:
        super().__init__("auth_required", 401)


class AuthenticationFailed(AppError):
    def __init__(self) -> None:
        super().__init__("authentication_failed", 401)


class PermissionDenied(AppError):
    def __init__(self) -> None:
        super().__init__("permission_denied", 403)


class ResourceNotFound(AppError):
    def __init__(self) -> None:
        super().__init__("resource_not_found", 404)


class ValidationFailed(AppError):
    def __init__(self, details: object | None = None) -> None:
        super().__init__("validation_error", 422, details=details)


class ConflictError(AppError):
    def __init__(self, code: str = "conflict", details: object | None = None) -> None:
        super().__init__(code, 409, details=details)


class ArchivedSessionReadonly(ConflictError):
    def __init__(self) -> None:
        super().__init__("archived_session_readonly")


class DocumentArchived(ConflictError):
    def __init__(self) -> None:
        super().__init__("document_archived")


class DocumentVersionNotApprovable(ConflictError):
    def __init__(self) -> None:
        super().__init__("document_version_not_approvable")


class ActiveVersionConflict(ConflictError):
    def __init__(self) -> None:
        super().__init__("active_version_conflict")


class JobNotReady(ConflictError):
    def __init__(self) -> None:
        super().__init__("job_not_ready")


class JobActiveRetryExists(ConflictError):
    def __init__(self, details: object | None = None) -> None:
        super().__init__("job_active_retry_exists", details=details)


class TemporarySessionExpired(ConflictError):
    def __init__(self) -> None:
        super().__init__("temporary_session_expired")


class TemporarySessionNotArchivable(ConflictError):
    def __init__(self) -> None:
        super().__init__("temporary_session_not_archivable")


class CsrfMissing(AppError):
    def __init__(self) -> None:
        super().__init__("csrf_missing", 403)


class CsrfInvalid(AppError):
    def __init__(self) -> None:
        super().__init__("csrf_invalid", 403)


class RateLimitExceeded(AppError):
    def __init__(self) -> None:
        super().__init__("rate_limit_exceeded", 429)


class PayloadTooLarge(AppError):
    def __init__(self) -> None:
        super().__init__("payload_too_large", 413)


class UnsupportedMediaType(AppError):
    def __init__(self) -> None:
        super().__init__("unsupported_media_type", 415)


class UnsafeFileRejected(AppError):
    def __init__(self) -> None:
        super().__init__("unsafe_file_rejected", 415)


def normalize_error(status_code: int, detail: Any) -> tuple[str, str, object]:
    if isinstance(detail, dict):
        raw_code = str(detail.get("code") or STATUS_DEFAULT_CODES.get(status_code, "error"))
        code = DETAIL_CODE_ALIASES.get(raw_code, raw_code)
        message = str(
            detail.get("message") or ERROR_MESSAGES.get(code) or _status_message(status_code)
        )
        details = detail.get("details", {})
        return code, message, details

    if isinstance(detail, str):
        code = DETAIL_CODE_ALIASES.get(detail, detail)
        if code == detail and detail not in ERROR_MESSAGES:
            code = STATUS_DEFAULT_CODES.get(status_code, detail)
        return code, ERROR_MESSAGES.get(code, _status_message(status_code)), {}

    code = STATUS_DEFAULT_CODES.get(status_code, "error")
    return code, ERROR_MESSAGES.get(code, _status_message(status_code)), {}


def _status_message(status_code: int) -> str:
    try:
        return f"{HTTPStatus(status_code).phrase}."
    except ValueError:
        return "Request failed."
