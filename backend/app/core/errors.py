from __future__ import annotations

from http import HTTPStatus
from typing import Any

ERROR_MESSAGES: dict[str, str] = {
    "authentication_failed": "Authentication failed.",
    "auth_required": "Authentication required.",
    "permission_denied": "Permission denied.",
    "csrf_missing": "CSRF token is required.",
    "csrf_invalid": "CSRF token is invalid.",
    "resource_not_found": "Resource not found.",
    "validation_error": "Invalid request.",
    "business_validation_error": "Request cannot be processed.",
    "conflict": "Resource conflict.",
    "no_context_found": "No context found.",
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
    422: "validation_error",
}


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
