from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

from app.core.config import Settings
from app.observability.trace_export import (
    LangSmithTraceExporter,
    NoOpTraceExporter,
    TraceExportPayload,
    TraceExportService,
    TraceExportStatus,
    build_retrieval_trace_export_payload,
    create_trace_exporter,
    safe_trace_export_payload,
)


def test_trace_export_settings_default_to_no_external_export() -> None:
    settings = Settings(app_env="test")

    exporter = create_trace_exporter(settings)
    result = exporter.export_ci_evaluation_summary(
        TraceExportPayload(trace_type="ci_evaluation", payload={"status": "succeeded"})
    )

    assert isinstance(exporter, NoOpTraceExporter)
    assert result.status == TraceExportStatus.SKIPPED
    assert result.reason_code == "disabled"


def test_trace_export_settings_validate_provider_and_preview_flags() -> None:
    with pytest.raises(ValueError, match="TRACE_EXPORT_PROVIDER"):
        Settings(app_env="test", trace_export_provider="remote")
    with pytest.raises(ValueError, match="TRACE_EXPORT_INCLUDE_PREVIEWS"):
        Settings(app_env="test", trace_export_preview_max_chars=80)


def test_langsmith_without_secret_uses_noop() -> None:
    settings = Settings(
        app_env="test",
        trace_export_enabled=True,
        trace_export_provider="langsmith",
        langsmith_tracing_enabled=True,
        langsmith_api_key=None,
    )

    exporter = create_trace_exporter(settings)
    result = exporter.export_retrieval_trace(
        TraceExportPayload(trace_type="retrieval", payload={"retrieval_run_id": 1})
    )

    assert isinstance(exporter, NoOpTraceExporter)
    assert result.status == TraceExportStatus.SKIPPED
    assert result.reason_code == "langsmith_not_configured"


def test_retrieval_payload_excludes_raw_text_and_secret_like_values() -> None:
    settings = Settings(app_env="test")
    run = types.SimpleNamespace(
        request_id="req_1",
        retrieval_run_id=123,
        chat_session_id=None,
        status="succeeded",
        strategy_type="agentic_router",
        query_hash="a" * 64,
        retrieval_score_summary={
            "selected_count": 2,
            "excluded_by_rdb_check_count": 1,
            "raw_query": "secret question",
            "content_text": "raw chunk text",
            "safe_count": 2,
        },
        strategy_decision_json={
            "selected_strategy": "hybrid",
            "execution_strategy": "hybrid",
            "fallback_used": False,
            "reason_codes": ["keyword_heavy"],
            "apiKey": "should-not-export",
        },
        query_plan_json={
            "query_hash": "a" * 64,
            "intent": "factual_lookup",
            "normalized_query_preview": "do not export previews by default",
        },
        latency_breakdown_json={"total_ms": 42},
        retrieval_settings_json={"strategy_type": "agentic_router"},
        confidence_label="High",
        answer_confidence=Decimal("0.9"),
        groundedness_score=Decimal("0.8"),
        error_code=None,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    payload = build_retrieval_trace_export_payload(cast(Any, run), settings).payload
    serialized = repr(payload).lower()

    assert payload["retrieval_run_id"] == 123
    assert payload["query_hash"] == "a" * 64
    assert "raw_query" not in serialized
    assert "raw chunk text" not in serialized
    assert "should-not-export" not in serialized
    assert "preview" not in serialized


def test_safe_trace_export_payload_redacts_paths_and_forbidden_keys() -> None:
    payload = safe_trace_export_payload(
        {
            "query_hash": "b" * 64,
            "storage_path": r"C:\Users\example\secret.txt",
            "nested": {
                "session_id": "session-secret",
                "safe_metric": 0.5,
                "note": r"C:\Users\example\secret.txt",
            },
            "metric_summary": {
                "graph_path_relevance": 0.75,
                "graph_path_count": 2,
                "storage_path": r"C:\Users\example\secret.txt",
            },
        },
        settings=Settings(app_env="test"),
    )

    serialized = repr(payload).lower()
    assert payload["query_hash"] == "b" * 64
    assert "storage_path" not in serialized
    assert "session-secret" not in serialized
    assert "c:\\users" not in serialized
    assert payload["nested"] == {"safe_metric": 0.5, "note": "redacted"}
    assert payload["metric_summary"] == {
        "graph_path_relevance": 0.75,
        "graph_path_count": 2,
    }


def test_trace_export_service_failure_is_non_fatal() -> None:
    class FailingExporter:
        provider = "failing"

        def export_retrieval_trace(self, payload: TraceExportPayload) -> Any:
            raise RuntimeError("boom")

        def export_evaluation_trace(self, payload: TraceExportPayload) -> Any:
            raise RuntimeError("boom")

        def export_ci_evaluation_summary(self, payload: TraceExportPayload) -> Any:
            raise RuntimeError("boom")

    service = TraceExportService(Settings(app_env="test"), exporter=FailingExporter())

    result = service.export_ci_evaluation_summary({"status": "succeeded"})

    assert result.status == TraceExportStatus.FAILED
    assert result.reason_code == "export_failed"


def test_langsmith_adapter_uses_safe_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    fake_module = types.ModuleType("langsmith")

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def create_run(self, **kwargs: object) -> None:
            calls.append(kwargs)

    fake_module.Client = FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langsmith", fake_module)

    exporter = LangSmithTraceExporter(
        api_key="secret-api-key",
        project_name="ragproject-phase2",
        endpoint=None,
        timeout_seconds=3,
    )
    result = exporter.export_retrieval_trace(
        TraceExportPayload(
            trace_type="retrieval",
            payload={
                "retrieval_run_id": 1,
                "query_hash": "c" * 64,
                "raw_query": "do not send",
                "token": "do not send",
            },
        )
    )

    assert result.status == TraceExportStatus.EXPORTED
    assert len(calls) == 1
    assert calls[0]["run_type"] == "retriever"
    assert calls[0]["project_name"] == "ragproject-phase2"
    serialized_outputs = repr(calls[0]["outputs"]).lower()
    assert "do not send" not in serialized_outputs
    assert "token" not in serialized_outputs
    assert "query_hash" in serialized_outputs
