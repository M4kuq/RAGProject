from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import cast

from app.db.models import Job

REDACTED = "[REDACTED]"
ACTIVE_RETRY_STATUSES = frozenset({"queued", "running"})
_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "credential",
    "cookie",
    "csrf",
    "prompt",
    "raw",
    "content",
    "text",
    "message",
    "chunk",
    "chunks",
    "query",
    "file",
    "path",
    "storage_key",
    "url",
    "dsn",
)
_SAFE_RESULT_KEYS = frozenset(
    {
        "status",
        "result_code",
        "handler_status",
        "handled",
        "document_version_id",
        "logical_document_id",
        "evaluation_run_id",
        "message_id",
        "mirror_action",
        "cleaned_count",
        "result_redacted",
    }
)
_SAFE_PAYLOAD_KEYS = frozenset(
    {
        "logical_document_id",
        "document_version_id",
        "requested_by_user_id",
        "evaluation_run_id",
        "chat_session_id",
        "chat_message_id",
        "message_id",
        "mirror_action",
        "cleanup_scope",
        "result_code",
        "status",
    }
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"[A-Za-z]:\\")
_UNIX_ABSOLUTE_PATH_RE = re.compile(
    r"(^|[\s'\"])/(app|data|etc|home|mnt|opt|root|srv|tmp|Users|var|workspace)/"
)
_API_KEY_RE = re.compile(
    r"\b((sk|pk)-[A-Za-z0-9_\-]{12,}|"
    r"(sk|pk|ghp|gho|ghu|github_pat)_[A-Za-z0-9_\-]{12,}|"
    r"(AKIA|ASIA)[A-Z0-9]{16}|"
    r"xox[baprs]-[A-Za-z0-9\-]{10,})\b"
)
_BEARER_TOKEN_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._\-]{12,}\b", re.IGNORECASE)
_KEY_VALUE_SECRET_RE = re.compile(
    r"\b(api[_-]?key|secret|token|password|credential)\s*[:=]\s*[^,\s;]+",
    re.IGNORECASE,
)
_CREDENTIAL_URL_RE = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")


class LeaseLostError(RuntimeError):
    pass


def original_source_job_id(job: Job) -> int:
    return int(job.retry_of_job_id or job.job_id)


def is_active_retry_status(status: str) -> bool:
    return status in ACTIVE_RETRY_STATUSES


def redact_payload(value: object) -> object:
    return _redact_value(value, key=None)


def sanitize_job_payload(value: dict[str, object] | None) -> dict[str, object]:
    if not value:
        return {}
    redacted = redact_payload(value)
    if not isinstance(redacted, dict):
        return {}

    safe: dict[str, object] = {}
    for raw_key, raw_value in cast(dict[str, object], redacted).items():
        key = str(raw_key)
        if raw_value == REDACTED:
            safe[key] = REDACTED
        elif _is_safe_payload_key(key, raw_value):
            safe[key] = raw_value
    return safe


def redact_error_message(message: str | None) -> str:
    if not message:
        return "Job failed."
    stripped = " ".join(message.split())
    lowered = stripped.lower()
    if (
        any(part in lowered for part in _SENSITIVE_KEY_PARTS)
        or _looks_like_absolute_path(stripped)
        or _looks_like_secret(stripped)
    ):
        return "Job failed with a redacted error."
    if len(stripped) > 500:
        return f"{stripped[:497]}..."
    return stripped


def sanitize_result_json(value: dict[str, object] | None) -> dict[str, object]:
    if not value:
        return {}
    redacted = redact_payload(value)
    if not isinstance(redacted, dict):
        return {"result_redacted": True}

    safe: dict[str, object] = {}
    for raw_key, raw_value in cast(dict[str, object], redacted).items():
        key = str(raw_key)
        if _is_safe_result_key(key, raw_value):
            safe[key] = raw_value
    if not safe:
        safe["result_redacted"] = True
    return safe


def _redact_value(value: object, *, key: str | None) -> object:
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            str_key = str(raw_key)
            redacted[str_key] = _redact_value(raw_value, key=str_key)
        return redacted
    if isinstance(value, str):
        if _looks_like_absolute_path(value) or _looks_like_secret(value):
            return REDACTED
        return value if len(value) <= 1000 else f"{value[:997]}..."
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_redact_value(item, key=None) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return REDACTED
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _looks_like_absolute_path(value: str) -> bool:
    return bool(_WINDOWS_ABSOLUTE_PATH_RE.search(value) or _UNIX_ABSOLUTE_PATH_RE.search(value))


def _looks_like_secret(value: str) -> bool:
    return bool(
        _API_KEY_RE.search(value)
        or _BEARER_TOKEN_RE.search(value)
        or _KEY_VALUE_SECRET_RE.search(value)
        or _CREDENTIAL_URL_RE.search(value)
        or _JWT_RE.search(value)
    )


def _is_safe_payload_key(key: str, value: object) -> bool:
    if key in _SAFE_PAYLOAD_KEYS:
        return _is_safe_scalar(value)
    if key.endswith("_id") and isinstance(value, int):
        return True
    if key.endswith("_ids") and isinstance(value, Sequence) and not isinstance(value, str):
        return all(isinstance(item, int) for item in value)
    if key.endswith("_count") and isinstance(value, int):
        return True
    return False


def _is_safe_result_key(key: str, value: object) -> bool:
    if key in _SAFE_RESULT_KEYS:
        return _is_safe_scalar(value)
    if key.endswith("_id") and isinstance(value, int):
        return True
    if key.endswith("_count") and isinstance(value, int):
        return True
    return False


def _is_safe_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None
