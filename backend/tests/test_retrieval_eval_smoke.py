from __future__ import annotations

from pathlib import Path

import pytest

from app.scripts.retrieval_eval_smoke import (
    SCHEMA_VERSION,
    SmokeError,
    SmokeThresholds,
    evaluate_thresholds,
    parse_metrics,
    parse_strategies,
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


def test_threshold_warn_result_does_not_depend_on_mode() -> None:
    artifact = {
        "metrics_by_strategy": [
            {
                "strategy": "dense",
                "metrics": {
                    "recall_at_k": {"average": 0.25},
                    "no_context_rate": {"average": 0.75},
                },
            }
        ]
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
            "strategies": ["dense", "agentic_router"],
            "mode": "fake",
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
    assert "BAAI/" not in workflow
