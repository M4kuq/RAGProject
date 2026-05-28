from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|credential|token|cookie|csrf|session)\s*[:=]\s*[^,\s;]+"
    r"|bearer\s+[A-Za-z0-9._-]+"
    r"|sk-[A-Za-z0-9]+"
)
_WINDOWS_PATH_RE = re.compile(r"(?i)\b[a-z]:\\")
_POSIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:app|home|tmp|var|users?)/")
_FORBIDDEN_KEY_PARTS = (
    "api_key",
    "answer",
    "authorization",
    "chunk_text",
    "content_text",
    "context",
    "cookie",
    "credential",
    "csrf",
    "full_context",
    "message",
    "password",
    "path",
    "payload",
    "pii",
    "prompt",
    "query_preview",
    "raw_chunk",
    "raw_context",
    "raw_prompt",
    "raw_query",
    "raw_text",
    "secret",
    "session",
    "snippet",
    "storage",
    "text",
    "token",
)


def redact_experiment_artifact(value: object) -> object:
    if isinstance(value, Mapping):
        safe: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_forbidden_key(key_text):
                safe[key_text] = "[REDACTED]"
            else:
                safe[key_text] = redact_experiment_artifact(item)
        return safe
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [redact_experiment_artifact(item) for item in value]
    return value


def render_markdown_report(artifact: Mapping[str, object]) -> str:
    summary = _dict(artifact.get("summary"))
    lines = [
        "# SentenceTransformers Experiment Report",
        "",
        f"- schema_version: `{_safe(artifact.get('schema_version'))}`",
        f"- experiment: `{_safe(artifact.get('experiment_name'))}`",
        f"- dataset: `{_safe(artifact.get('dataset'))}`",
        f"- mode: `{_safe(artifact.get('mode'))}`",
        f"- status: `{_safe(summary.get('status'))}`",
        f"- total_runs: `{_safe(summary.get('total_runs'))}`",
        f"- succeeded: `{_safe(summary.get('succeeded_count'))}`",
        f"- skipped: `{_safe(summary.get('skipped_count'))}`",
        f"- blocked: `{_safe(summary.get('blocked_count'))}`",
        "",
        "## Results",
        "",
        "| embedding_model | reranker_model | status | case_count | "
        "recall_at_k | mrr | no_context_rate | p95_latency | reasons |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in _list(artifact.get("results")):
        if not isinstance(row, Mapping):
            continue
        metrics = _dict(row.get("metrics"))
        lines.append(
            "| "
            f"`{_safe(row.get('embedding_model_id'))}` | "
            f"`{_safe(row.get('reranker_model_id'))}` | "
            f"`{_safe(row.get('status'))}` | "
            f"{_safe(row.get('case_count'))} | "
            f"{_fmt(metrics.get('recall_at_k'))} | "
            f"{_fmt(metrics.get('mrr'))} | "
            f"{_fmt(metrics.get('no_context_rate'))} | "
            f"{_fmt(metrics.get('p95_latency'))} | "
            f"{', '.join(_strings(row.get('reason_codes'))) or 'none'} |"
        )
    limitations = _strings(artifact.get("known_limitations"))
    if limitations:
        lines.extend(["", "## Known Limitations", ""])
        lines.extend(f"- {item}" for item in limitations)
    lines.extend(
        [
            "",
            "## Privacy",
            "",
            "Artifacts and reports contain model ids, statuses, aggregate metrics, counts, "
            "and reason codes only. Raw prompts, full context, raw chunk text, PII, "
            "secrets, tokens, local paths, and full answers are redacted or omitted.",
            "",
        ]
    )
    return "\n".join(lines)


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _FORBIDDEN_KEY_PARTS)


def _redact_string(value: str) -> str:
    if _WINDOWS_PATH_RE.search(value) or _POSIX_PATH_RE.search(value):
        return "[REDACTED_PATH]"
    value = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    return _SECRET_VALUE_RE.sub("[REDACTED]", value)


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, str) else []


def _strings(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [_redact_string(str(item)) for item in value if isinstance(item, str)]


def _safe(value: object) -> str:
    if value is None:
        return "N/A"
    return _redact_string(str(value))


def _fmt(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "N/A"
    return f"{float(value):.4f}"
