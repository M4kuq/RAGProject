from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|authorization|cookie|credential|csrf|password|private[_-]?key|"
    r"secret|session|token)",
    re.IGNORECASE,
)
RAW_CONTEXT_KEY_RE = re.compile(
    r"^(content_text|context_text|full_context|full_prompt|job_payload|payload_json|"
    r"prompt|qdrant_payload|raw|raw_chunk_text|raw_context|raw_prompt|storage_key|"
    r"storage_path)$",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|token|csrf|session)\b\s*[:=]\s*\S+"
)
API_KEY_RE = re.compile(
    r"\b((sk|pk)-[A-Za-z0-9_\-]{12,}|"
    r"(sk|pk|ghp|gho|ghu|github_pat)_[A-Za-z0-9_\-]{12,}|"
    r"(AKIA|ASIA)[A-Z0-9]{16}|"
    r"xox[baprs]-[A-Za-z0-9\-]{10,})\b"
)
BEARER_TOKEN_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._\-]{12,}\b", re.IGNORECASE)
CREDENTIAL_URL_RE = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+")
UNIX_STORAGE_PATH_RE = re.compile(r"/(?:app/)?(?:storage|data|tmp)/[^\s\"']+")

REDACTED = "[REDACTED]"
OMITTED = "[OMITTED]"


def truncate_text(value: str, *, max_chars: int) -> str:
    cleaned = " ".join(value.replace("\x00", " ").split())
    cleaned = SECRET_VALUE_RE.sub(REDACTED, cleaned)
    cleaned = API_KEY_RE.sub(REDACTED, cleaned)
    cleaned = BEARER_TOKEN_RE.sub(REDACTED, cleaned)
    cleaned = CREDENTIAL_URL_RE.sub("://[REDACTED]@", cleaned)
    cleaned = JWT_RE.sub(REDACTED, cleaned)
    cleaned = WINDOWS_PATH_RE.sub(REDACTED, cleaned)
    cleaned = UNIX_STORAGE_PATH_RE.sub(REDACTED, cleaned)
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 3]}..."


def safe_source_label(value: str | None, *, max_chars: int = 255) -> str | None:
    if value is None:
        return None
    normalized = value.replace("\\", "/")
    label = PurePosixPath(normalized).name or PureWindowsPath(value).name
    safe = truncate_text(label, max_chars=max_chars)
    return safe or None


def redact_data(value: Any, *, max_string_chars: int) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SENSITIVE_KEY_RE.search(key_text):
                redacted["redacted_sensitive"] = True
            elif RAW_CONTEXT_KEY_RE.search(key_text):
                redacted["omitted_raw_field"] = True
            else:
                redacted[key_text] = redact_data(item, max_string_chars=max_string_chars)
        return redacted
    if isinstance(value, list):
        return [redact_data(item, max_string_chars=max_string_chars) for item in value]
    if isinstance(value, tuple):
        return [redact_data(item, max_string_chars=max_string_chars) for item in value]
    if isinstance(value, str):
        return truncate_text(value, max_chars=max_string_chars)
    return value


def safe_metric_details(
    value: dict[str, object] | None,
    *,
    max_string_chars: int,
) -> dict[str, object]:
    if not value:
        return {}
    allowed: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if SENSITIVE_KEY_RE.search(key_text) or RAW_CONTEXT_KEY_RE.search(key_text):
            continue
        allowed[key_text] = redact_data(item, max_string_chars=max_string_chars)
    return allowed
