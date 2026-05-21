from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|authorization|cookie|credential|csrf|password|private[_-]?key|"
    r"secret|session|token)",
    re.IGNORECASE,
)
RAW_CONTEXT_KEY_NAMES = {
    "chunkcontent",
    "chunktext",
    "contenttext",
    "context",
    "contextchunks",
    "contextmessages",
    "contexttext",
    "fullcontext",
    "fullprompt",
    "jobpayload",
    "payloadjson",
    "prompt",
    "prompttext",
    "qdrantpayload",
    "raw",
    "rawchunk",
    "rawchunktext",
    "rawcontext",
    "rawprompt",
    "retrievedcontext",
    "sourcetext",
    "storagekey",
    "storagepath",
}
SAFE_METRIC_DETAIL_NAMES = {
    "answerpresent",
    "caseid",
    "caselabel",
    "casename",
    "citationcoverage",
    "confidence",
    "confidencelabel",
    "confidencescore",
    "errorcode",
    "expectedanswerpresent",
    "faithfulnessscore",
    "groundednessscore",
    "hasconfidence",
    "latencyms",
    "requiredcitation",
    "source",
    "sourcelabel",
    "status",
}
SECRET_VALUE_RE = re.compile(
    r"(?i)\b[A-Za-z0-9_-]*"
    r"(api[_-]?key|authorization|cookie|credential|csrf|password|private[_-]?key|"
    r"secret|session|token)"
    r"[A-Za-z0-9_-]*\s*[:=]\s*(?:(?:basic|bearer)\s+[^\s,;]+|[^\s,;]+)"
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
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\"'\r\n]+")
UNIX_STORAGE_PATH_RE = re.compile(r"/(?:app/)?(?:storage|data|tmp)/[^\"'\r\n]+")

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
        saw_sensitive = False
        saw_raw = False
        for key, item in value.items():
            key_text = str(key)
            if SENSITIVE_KEY_RE.search(key_text):
                saw_sensitive = True
            elif _is_raw_context_key(key_text):
                saw_raw = True
            else:
                redacted[key_text] = redact_data(item, max_string_chars=max_string_chars)
        if saw_sensitive:
            redacted["redacted_sensitive"] = True
        if saw_raw:
            redacted["omitted_raw_field"] = True
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
        if SENSITIVE_KEY_RE.search(key_text) or _is_raw_context_key(key_text):
            continue
        if not _is_safe_metric_detail_key(key_text):
            allowed["omitted_unsafe_detail"] = True
            continue
        allowed[key_text] = redact_data(item, max_string_chars=max_string_chars)
    return allowed


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_raw_context_key(value: str) -> bool:
    normalized = _normalize_key(value)
    return normalized in RAW_CONTEXT_KEY_NAMES


def _is_safe_metric_detail_key(value: str) -> bool:
    normalized = _normalize_key(value)
    return (
        normalized in SAFE_METRIC_DETAIL_NAMES
        or normalized.endswith("count")
        or normalized.endswith("score")
        or normalized.endswith("rate")
        or normalized.endswith("ratio")
        or normalized.endswith("ms")
    )
