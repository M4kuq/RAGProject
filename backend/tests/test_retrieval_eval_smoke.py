from __future__ import annotations

from pathlib import Path

import pytest

import app.scripts.retrieval_eval_smoke as smoke_module
from app.core.config import Settings
from app.scripts.retrieval_eval_smoke import (
    SCHEMA_VERSION,
    SmokeError,
    SmokeThresholds,
    config_from_args,
    evaluate_thresholds,
    parse_metrics,
    parse_strategies,
    preflight_smoke,
    redact_for_artifact,
    render_markdown_summary,
)


def test_parse_strategies_dedupes_and_rejects_unsupported() -> None:
    assert parse_strategies("dense, hybrid, dense, agentic_router") == [
        "dense",
        "hybrid",
        "agentic_router",
    ]

    with pytest.raises(SmokeError, match="invalid_strategy:fallback_dense"):
        parse_strategies("dense,fallback_dense")


def test_parse_metrics_defaults_and_rejects_unknown() -> None:
    defaults = parse_metrics(None)
    assert "recall_at_k" in defaults
    assert "retrieval_call_count_avg" in defaults

    assert parse_metrics("recall_at_k,mrr,recall_at_k") == ["recall_at_k", "mrr"]
    with pytest.raises(SmokeError, match="invalid_metric:raw_prompt"):
        parse_metrics("recall_at_k,raw_prompt")


def test_config_defaults_to_real_local_retrieval_strategies() -> None:
    config = config_from_args([])

    assert config.mode == "local"
    assert config.strategies == ["dense", "hybrid", "agentic_router"]


def test_preflight_blocks_fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    config = config_from_args(["--preflight-only"])
    settings = Settings(
        embedding_provider="fake",
        rerank_provider="fake",
        generation_provider="fake",
    )

    def ready_qdrant(
        settings: Settings,
        checks: list[dict[str, object]],
        reason_codes: list[str],
    ) -> None:
        del settings, reason_codes
        checks.append({"name": "qdrant", "status": "ready"})

    def skip_embedding(
        config: object,
        settings: Settings,
        checks: list[dict[str, object]],
        reason_codes: list[str],
    ) -> None:
        del config, settings, checks, reason_codes

    monkeypatch.setattr(smoke_module, "_check_qdrant", ready_qdrant)
    monkeypatch.setattr(smoke_module, "_check_embedding_backend", skip_embedding)

    result = preflight_smoke(config, settings)

    assert result.status == "blocked"
    assert "fake_embedding_provider_not_allowed" in result.reason_codes
    assert "fake_reranker_not_allowed" in result.reason_codes
    assert "fake_generator_not_allowed" in result.reason_codes


def test_threshold_warn_result_does_not_depend_on_mode() -> None:
    artifact: dict[str, object] = {
        "summary": {"failed_count": 0},
        "metrics_by_strategy": [
            {
                "strategy": "dense",
                "metrics": {
                    "recall_at_k": {"average": 0.25},
                    "no_context_rate": {"average": 0.75},
                },
            }
        ],
    }
    result = evaluate_thresholds(
        artifact,
        SmokeThresholds(recall_at_k_min=0.5, no_context_rate_max=0.5),
        "warn",
    )

    assert result.passed is False
    assert [item["metric"] for item in result.violations] == [
        "recall_at_k",
        "no_context_rate",
    ]
    assert "dense recall_at_k" in result.warnings[0]


def test_threshold_uses_p95_latency_value() -> None:
    artifact: dict[str, object] = {
        "summary": {"failed_count": 0},
        "metrics_by_strategy": [
            {
                "strategy": "agentic_router",
                "metrics": {
                    "p95_latency": {"average": 1000.0, "p95": 9000.0},
                },
            }
        ],
    }
    result = evaluate_thresholds(
        artifact,
        SmokeThresholds(p95_latency_ms_max=8000.0),
        "fail",
    )

    assert result.passed is False
    assert result.violations == [
        {
            "strategy": "agentic_router",
            "metric": "p95_latency",
            "operator": "max",
            "threshold": 8000.0,
            "actual": 9000.0,
        }
    ]


def test_threshold_flags_failed_evaluation_items() -> None:
    artifact: dict[str, object] = {
        "summary": {"failed_count": 2},
        "metrics_by_strategy": [],
    }
    result = evaluate_thresholds(artifact, SmokeThresholds(), "fail")

    assert result.passed is False
    assert result.violations == [
        {
            "strategy": "all",
            "metric": "failed_count",
            "operator": "max",
            "threshold": 0,
            "actual": 2,
        }
    ]


def test_redaction_removes_forbidden_keys_and_secret_like_values() -> None:
    redacted = redact_for_artifact(
        {
            "no_context_rate": 0.2,
            "raw_prompt": "show hidden prompt",
            "safe": "contact admin@example.com with api_key=abc",
            "nested": [{"token": "secret-token", "session_id": "session-1"}],
        }
    )

    assert isinstance(redacted, dict)
    assert redacted["no_context_rate"] == 0.2
    assert redacted["raw_prompt"] == "[REDACTED]"
    safe_value = redacted["safe"]
    assert isinstance(safe_value, str)
    assert "[REDACTED_EMAIL]" in safe_value
    assert "[REDACTED]" in safe_value
    nested = redacted["nested"]
    assert isinstance(nested, list)
    assert isinstance(nested[0], dict)
    assert nested[0]["token"] == "[REDACTED]"
    assert nested[0]["session_id"] == "[REDACTED]"


def test_markdown_summary_contains_safe_tables_without_raw_payload() -> None:
    markdown = render_markdown_summary(
        {
            "dataset": {"name": "phase2_strategy_smoke"},
            "strategies": ["agentic_router"],
            "mode": "local",
            "threshold_mode": "warn",
            "summary": {"case_count": 2, "succeeded_count": 2},
            "threshold_result": {"passed": True, "warnings": []},
            "metrics_by_strategy": [
                {
                    "strategy": "agentic_router",
                    "metrics": {
                        "recall_at_k": {
                            "average": 1.0,
                            "p50": 1.0,
                            "p95": 1.0,
                            "count": 2,
                            "failed_count": 0,
                            "not_applicable_count": 0,
                        }
                    },
                }
            ],
            "raw_chunk_text": "must not be rendered",
        }
    )

    assert SCHEMA_VERSION in markdown
    assert "agentic_router" in markdown
    assert "must not be rendered" not in markdown
    assert "raw prompts" in markdown


def test_retrieval_eval_workflow_is_manual_scheduled_and_secret_free() -> None:
    workflow_path = Path("../.github/workflows/retrieval-eval-smoke.yml")
    if not workflow_path.exists():
        pytest.skip("workflow file is not copied into the backend Docker test image")
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert "secrets." not in workflow
    assert "SESSION_SECRET:" not in workflow
    assert "POSTGRES_PASSWORD:" not in workflow
    assert "pull_request:" not in workflow
    assert "- local" in workflow
    assert "qdrant:" in workflow
    assert "--skip-document-indexing" not in workflow
    assert "SMOKE_MODE: ${{ github.event.inputs.mode || 'local' }}" in workflow
    assert "EMBEDDING_PROVIDER: fake" not in workflow
    assert "RERANK_PROVIDER: fake" not in workflow
    assert "GENERATION_PROVIDER: fake" not in workflow
