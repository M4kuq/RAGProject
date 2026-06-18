from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol, cast

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import RetrievalRun
from app.rag.trace import TraceRedactor

TRACE_EXPORT_SCHEMA_VERSION: Literal["phase2.trace_export.v1"] = "phase2.trace_export.v1"

_EXPORT_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
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
    "payload_snapshot",
    "pii",
    "prompt",
    "qdrant_payload",
    "query_preview",
    "raw_chunk",
    "raw_context",
    "raw_payload",
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
_EXPORT_SAFE_KEYS = {
    "answer_confidence",
    "case_count",
    "confidence_label",
    "dataset_name",
    "elapsed_ms",
    "error_code",
    "evaluation_dataset_id",
    "evaluation_run_id",
    "fallback_rate",
    "fallback_reason",
    "fallback_used",
    "finished_at",
    "generated_at",
    "citable_path_count",
    "excluded_path_count",
    "graph_path_count",
    "graph_path_relevance",
    "groundedness",
    "groundedness_score",
    "latency_breakdown",
    "metric_name",
    "metric_summary",
    "metrics_by_strategy",
    "mrr",
    "no_context_rate",
    "origin_type",
    "p95_latency",
    "path_count",
    "query_hash",
    "reason_codes",
    "recall_at_k",
    "request_id",
    "requested_top_k",
    "retrieval_call_count",
    "retrieval_run_id",
    "retrieval_score_summary",
    "retrieval_settings",
    "selected_count",
    "started_at",
    "status",
    "strategy_type",
    "threshold_result",
    "top_k",
    "trace_type",
    "valid_path_count",
}
_WINDOWS_PATH_RE = re.compile(r"(?i)\b[a-z]:\\")
_POSIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:app|home|tmp|var|users?)/")


class TraceExportStatus(StrEnum):
    EXPORTED = "exported"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class TraceExportPayload:
    trace_type: Literal["retrieval", "evaluation", "ci_evaluation"]
    payload: dict[str, object]


@dataclass(frozen=True)
class TraceExportResult:
    status: TraceExportStatus
    provider: str
    reason_code: str | None = None

    def model_dump(self) -> dict[str, object]:
        data: dict[str, object] = {
            "schema_version": TRACE_EXPORT_SCHEMA_VERSION,
            "status": self.status.value,
            "provider": self.provider,
        }
        if self.reason_code:
            data["reason_code"] = self.reason_code
        return data


class TraceExporter(Protocol):
    provider: str

    def export_retrieval_trace(self, payload: TraceExportPayload) -> TraceExportResult: ...

    def export_evaluation_trace(self, payload: TraceExportPayload) -> TraceExportResult: ...

    def export_ci_evaluation_summary(self, payload: TraceExportPayload) -> TraceExportResult: ...


class NoOpTraceExporter:
    provider = "none"

    def __init__(self, *, reason_code: str = "disabled") -> None:
        self.reason_code = reason_code

    def export_retrieval_trace(self, payload: TraceExportPayload) -> TraceExportResult:
        del payload
        return TraceExportResult(
            status=TraceExportStatus.SKIPPED,
            provider=self.provider,
            reason_code=self.reason_code,
        )

    def export_evaluation_trace(self, payload: TraceExportPayload) -> TraceExportResult:
        del payload
        return TraceExportResult(
            status=TraceExportStatus.SKIPPED,
            provider=self.provider,
            reason_code=self.reason_code,
        )

    def export_ci_evaluation_summary(self, payload: TraceExportPayload) -> TraceExportResult:
        del payload
        return TraceExportResult(
            status=TraceExportStatus.SKIPPED,
            provider=self.provider,
            reason_code=self.reason_code,
        )


class LangSmithTraceExporter:
    provider = "langsmith"

    def __init__(
        self,
        *,
        api_key: str,
        project_name: str,
        endpoint: str | None,
        timeout_seconds: float,
    ) -> None:
        self._api_key = api_key
        self._project_name = project_name
        self._endpoint = endpoint.rstrip("/") if endpoint else None
        self._timeout_seconds = timeout_seconds

    def export_retrieval_trace(self, payload: TraceExportPayload) -> TraceExportResult:
        return self._create_run(payload, run_type="retriever")

    def export_evaluation_trace(self, payload: TraceExportPayload) -> TraceExportResult:
        return self._create_run(payload, run_type="chain")

    def export_ci_evaluation_summary(self, payload: TraceExportPayload) -> TraceExportResult:
        return self._create_run(payload, run_type="chain")

    def _create_run(
        self,
        payload: TraceExportPayload,
        *,
        run_type: Literal["retriever", "chain"],
    ) -> TraceExportResult:
        safe_payload = safe_trace_export_payload(payload.payload)
        try:
            from langsmith import Client  # type: ignore[import-not-found]
        except Exception:
            return TraceExportResult(
                status=TraceExportStatus.SKIPPED,
                provider=self.provider,
                reason_code="langsmith_sdk_unavailable",
            )
        try:
            client_kwargs: dict[str, object] = {
                "api_key": self._api_key,
                "timeout_ms": int(self._timeout_seconds * 1000),
            }
            if self._endpoint:
                client_kwargs["api_url"] = self._endpoint
            client = Client(**cast(Any, client_kwargs))
            client.create_run(
                name=f"ragproject.{payload.trace_type}",
                run_type=run_type,
                project_name=self._project_name,
                inputs={
                    "schema_version": TRACE_EXPORT_SCHEMA_VERSION,
                    "trace_type": payload.trace_type,
                },
                outputs=safe_payload,
                extra={
                    "metadata": {
                        "schema_version": TRACE_EXPORT_SCHEMA_VERSION,
                        "trace_type": payload.trace_type,
                    }
                },
                tags=["ragproject", "phase2", payload.trace_type],
            )
        except Exception:
            return TraceExportResult(
                status=TraceExportStatus.FAILED,
                provider=self.provider,
                reason_code="export_failed",
            )
        return TraceExportResult(status=TraceExportStatus.EXPORTED, provider=self.provider)


class TraceExportService:
    def __init__(self, settings: Settings, exporter: TraceExporter | None = None) -> None:
        self.settings = settings
        self.exporter = exporter or create_trace_exporter(settings)

    def export_retrieval_run(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
    ) -> TraceExportResult:
        if not self.settings.trace_export_include_retrieval:
            return _skipped("retrieval_export_disabled")
        run = db.get(RetrievalRun, retrieval_run_id)
        if run is None:
            return _skipped("retrieval_run_missing")
        payload = build_retrieval_trace_export_payload(run, self.settings)
        try:
            return self.exporter.export_retrieval_trace(payload)
        except Exception:
            return TraceExportResult(
                status=TraceExportStatus.FAILED,
                provider=getattr(self.exporter, "provider", "unknown"),
                reason_code="export_failed",
            )

    def export_evaluation_summary(self, summary: object) -> TraceExportResult:
        if not self.settings.trace_export_include_evaluation:
            return _skipped("evaluation_export_disabled")
        payload = build_evaluation_trace_export_payload(summary, self.settings)
        try:
            return self.exporter.export_evaluation_trace(payload)
        except Exception:
            return TraceExportResult(
                status=TraceExportStatus.FAILED,
                provider=getattr(self.exporter, "provider", "unknown"),
                reason_code="export_failed",
            )

    def export_ci_evaluation_summary(self, artifact: Mapping[str, Any]) -> TraceExportResult:
        if not self.settings.trace_export_include_ci_summary:
            return _skipped("ci_evaluation_export_disabled")
        payload = build_ci_evaluation_trace_export_payload(artifact, self.settings)
        try:
            return self.exporter.export_ci_evaluation_summary(payload)
        except Exception:
            return TraceExportResult(
                status=TraceExportStatus.FAILED,
                provider=getattr(self.exporter, "provider", "unknown"),
                reason_code="export_failed",
            )


def create_trace_exporter(settings: Settings) -> TraceExporter:
    provider = settings.trace_export_provider.lower()
    if not settings.trace_export_enabled or provider == "none":
        return NoOpTraceExporter(reason_code="disabled")
    if provider == "langsmith":
        api_key = (settings.langsmith_api_key or "").strip()
        if not settings.langsmith_tracing_enabled or not api_key:
            return NoOpTraceExporter(reason_code="langsmith_not_configured")
        return LangSmithTraceExporter(
            api_key=api_key,
            project_name=settings.langsmith_project,
            endpoint=settings.langsmith_endpoint or None,
            timeout_seconds=settings.trace_export_timeout_seconds,
        )
    return NoOpTraceExporter(reason_code="invalid_provider")


def build_retrieval_trace_export_payload(
    run: RetrievalRun,
    settings: Settings,
) -> TraceExportPayload:
    score_summary = _safe_mapping(run.retrieval_score_summary, settings)
    decision = _safe_mapping(run.strategy_decision_json, settings)
    query_plan = _safe_mapping(run.query_plan_json, settings)
    payload = {
        "schema_version": TRACE_EXPORT_SCHEMA_VERSION,
        "trace_type": "retrieval",
        "request_id": TraceRedactor.safe_string(run.request_id, max_length=100)
        if run.request_id
        else None,
        "retrieval_run_id": run.retrieval_run_id,
        "origin_type": "chat" if run.chat_session_id is not None else "standalone",
        "status": run.status,
        "strategy_type": run.strategy_type,
        "selected_strategy": decision.get("selected_strategy"),
        "execution_strategy": decision.get("execution_strategy"),
        "fallback_used": _bool_or_none(decision.get("fallback_used")),
        "fallback_reason": decision.get("fallback_reason"),
        "retrieval_call_count": _number_or_none(
            decision.get("retrieval_call_count") or score_summary.get("retrieval_call_count")
        ),
        "query_hash": run.query_hash,
        "intent": query_plan.get("intent"),
        "reason_codes": _string_list(
            decision.get("reason_codes") or query_plan.get("reason_codes")
        ),
        "retrieval_score_summary": score_summary,
        "latency_breakdown": _safe_mapping(run.latency_breakdown_json, settings),
        "retrieval_settings": _safe_mapping(run.retrieval_settings_json, settings),
        "selected_count": _number_or_none(score_summary.get("selected_count")),
        "excluded_by_rdb_check_count": _number_or_none(
            score_summary.get("excluded_by_rdb_check_count")
        ),
        "confidence_label": run.confidence_label,
        "answer_confidence": _float_or_none(run.answer_confidence),
        "groundedness_score": _float_or_none(run.groundedness_score),
        "error_code": run.error_code,
        "started_at": _iso_datetime(run.started_at),
        "finished_at": _iso_datetime(run.finished_at),
    }
    return TraceExportPayload(
        trace_type="retrieval",
        payload=safe_trace_export_payload(payload, settings=settings),
    )


def build_evaluation_trace_export_payload(
    summary: object,
    settings: Settings,
) -> TraceExportPayload:
    data = _model_dump(summary)
    payload = {
        "schema_version": TRACE_EXPORT_SCHEMA_VERSION,
        "trace_type": "evaluation",
        "evaluation_run_id": data.get("evaluation_run_id"),
        "evaluation_dataset_id": data.get("evaluation_dataset_id"),
        "dataset_name": data.get("dataset_name"),
        "status": data.get("status"),
        "strategies": data.get("strategies"),
        "metric_names": data.get("metric_names"),
        "case_count": data.get("case_count"),
        "succeeded_count": data.get("succeeded_count"),
        "failed_count": data.get("failed_count"),
        "metric_summary": data.get("metric_summary"),
        "strategy_comparison": data.get("strategy_comparison"),
        "strategy_metrics_summary": data.get("strategy_metrics_summary_json"),
        "error_code": data.get("error_code"),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
    }
    return TraceExportPayload(
        trace_type="evaluation",
        payload=safe_trace_export_payload(payload, settings=settings),
    )


def build_ci_evaluation_trace_export_payload(
    artifact: Mapping[str, Any],
    settings: Settings,
) -> TraceExportPayload:
    payload = {
        "schema_version": TRACE_EXPORT_SCHEMA_VERSION,
        "trace_type": "ci_evaluation",
        "generated_at": artifact.get("generated_at"),
        "dataset": artifact.get("dataset"),
        "strategies": artifact.get("strategies"),
        "mode": artifact.get("mode"),
        "threshold_mode": artifact.get("threshold_mode"),
        "trigger_type": artifact.get("trigger_type"),
        "case_limit": artifact.get("case_limit"),
        "elapsed_ms": artifact.get("elapsed_ms"),
        "evaluation_run_id": artifact.get("evaluation_run_id"),
        "summary": artifact.get("summary"),
        "metrics_by_strategy": artifact.get("metrics_by_strategy"),
        "failure_summary": artifact.get("failure_summary"),
        "agentic_summary": artifact.get("agentic_summary"),
        "threshold_result": artifact.get("threshold_result"),
        "known_limitations": artifact.get("known_limitations"),
    }
    return TraceExportPayload(
        trace_type="ci_evaluation",
        payload=safe_trace_export_payload(payload, settings=settings),
    )


def safe_trace_export_payload(
    value: Any,
    *,
    settings: Settings | None = None,
) -> dict[str, object]:
    redacted = _redact_for_export(value, settings=settings)
    if not isinstance(redacted, dict):
        return {}
    return redacted


def _redact_for_export(value: Any, *, settings: Settings | None) -> object:
    if isinstance(value, Mapping):
        safe: dict[str, object] = {}
        for key, nested in value.items():
            key_text = str(key)
            if _is_sensitive_export_key(key_text, settings=settings):
                continue
            safe[key_text] = _redact_for_export(nested, settings=settings)
        return safe
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_redact_for_export(item, settings=settings) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, str):
        return _safe_export_string(value, settings=settings)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return TraceRedactor.safe_string(str(value), max_length=_string_max(settings))


def _is_sensitive_export_key(key: str, *, settings: Settings | None) -> bool:
    key_text = key.lower()
    if key_text in _EXPORT_SAFE_KEYS or key_text.endswith("_hash"):
        return False
    if key_text.endswith("_preview") and not _include_previews(settings):
        return True
    return any(part in key_text for part in _EXPORT_SENSITIVE_KEY_PARTS)


def _safe_export_string(value: str, *, settings: Settings | None) -> str:
    safe = TraceRedactor.safe_string(value, max_length=_string_max(settings))
    if _WINDOWS_PATH_RE.search(safe) or _POSIX_PATH_RE.search(safe):
        return "redacted"
    return safe


def _safe_mapping(value: object, settings: Settings) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return safe_trace_export_payload(cast(Mapping[str, Any], value), settings=settings)


def _model_dump(value: object) -> dict[str, object]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(mode="json")
        return result if isinstance(result, dict) else {}
    if isinstance(value, Mapping):
        return safe_trace_export_payload(cast(Mapping[str, Any], value))
    return {}


def _skipped(reason_code: str) -> TraceExportResult:
    return TraceExportResult(
        status=TraceExportStatus.SKIPPED,
        provider="none",
        reason_code=reason_code,
    )


def _include_previews(settings: Settings | None) -> bool:
    return bool(settings and settings.trace_export_include_previews)


def _string_max(settings: Settings | None) -> int:
    if settings is None:
        return 255
    if settings.trace_export_include_previews and settings.trace_export_preview_max_chars > 0:
        return max(1, min(settings.trace_export_preview_max_chars, 240))
    return 255


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [TraceRedactor.safe_string(str(item), max_length=100) for item in value]


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _number_or_none(value: object) -> float | int | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _iso_datetime(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None
