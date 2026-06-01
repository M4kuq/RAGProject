from __future__ import annotations

import json

import pytest

from app.rag.tool_result_compression import (
    OrchestratorContextGuard,
    ToolResultBudgetManager,
    ToolResultCandidate,
    ToolResultCompressionPolicy,
    ToolResultCompressor,
    sanitize_tool_result_compression_json,
)


def _candidate(
    chunk_id: int,
    text: str,
    *,
    tool_call_id: str = "tc_1",
    tool_name: str = "dense_search",
    rank: int = 1,
    score: float = 0.9,
    source: str = "source-a",
) -> ToolResultCandidate:
    return ToolResultCandidate(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        document_chunk_id=chunk_id,
        text=text,
        source_label=f"{source}.md",
        section_title="Section",
        page_from=1,
        page_to=1,
        rank=rank,
        retrieval_score=score,
        citation_candidate=True,
        source_group_key=source,
    )


def test_tool_result_policy_validation() -> None:
    with pytest.raises(ValueError):
        ToolResultCompressionPolicy(max_items_per_tool=3, max_total_items_per_turn=2)
    with pytest.raises(ValueError):
        ToolResultCompressionPolicy(
            max_tokens_per_tool=20,
            max_total_tool_result_tokens=10,
        )


def test_tool_result_compression_enforces_item_and_turn_limits() -> None:
    policy = ToolResultCompressionPolicy(
        max_items_per_tool=2,
        max_total_items_per_turn=3,
        max_tokens_per_tool=100,
        max_total_tool_result_tokens=100,
    )
    manager = ToolResultBudgetManager(policy)
    compressor = ToolResultCompressor()

    first = compressor.compress(
        [
            _candidate(1, "a" * 40, rank=1),
            _candidate(2, "b" * 40, rank=2),
            _candidate(3, "c" * 40, rank=3),
        ],
        policy=policy,
        budget_manager=manager,
        tool_call_id="tc_1",
        tool_name="dense_search",
    )
    second = compressor.compress(
        [
            _candidate(4, "d" * 40, tool_call_id="tc_2", rank=1),
            _candidate(5, "e" * 40, tool_call_id="tc_2", rank=2),
        ],
        policy=policy,
        budget_manager=manager,
        tool_call_id="tc_2",
        tool_name="hybrid_search",
    )

    assert first.output_item_count == 2
    assert first.drop_reasons == {"max_items_limit": 1}
    assert second.output_item_count == 1
    assert second.drop_reasons == {"max_total_items_limit": 1}
    assert manager.trace().summary.output_item_count == 3


def test_tool_result_compression_bounds_snippets_and_token_budgets() -> None:
    policy = ToolResultCompressionPolicy(
        max_items_per_tool=3,
        max_total_items_per_turn=3,
        max_snippet_chars=20,
        max_tokens_per_tool=10,
        max_total_tool_result_tokens=10,
    )
    manager = ToolResultBudgetManager(policy)
    result = ToolResultCompressor().compress(
        [
            _candidate(1, "a" * 100, rank=1),
            _candidate(2, "b" * 100, rank=2),
            _candidate(3, "c" * 100, rank=3),
        ],
        policy=policy,
        budget_manager=manager,
        tool_call_id="tc_1",
        tool_name="dense_search",
    )

    assert result.status == "succeeded"
    assert [len(item.snippet) for item in result.items] == [20, 20]
    assert result.drop_reasons == {"max_tokens_limit": 1}
    assert result.compression_methods == {"max_chars_per_snippet": 2}


def test_tool_result_compression_dedupes_same_chunk_and_exact_text() -> None:
    policy = ToolResultCompressionPolicy()
    manager = ToolResultBudgetManager(policy)
    result = ToolResultCompressor().compress(
        [
            _candidate(1, "duplicate text", rank=1),
            _candidate(1, "different same chunk", rank=2),
            _candidate(2, "duplicate text", rank=3),
        ],
        policy=policy,
        budget_manager=manager,
        tool_call_id="tc_1",
        tool_name="dense_search",
    )

    assert result.output_item_count == 1
    assert result.drop_reasons == {
        "same_chunk_deduped": 1,
        "exact_duplicate_removed": 1,
    }


def test_tool_result_compression_detects_repeated_result() -> None:
    policy = ToolResultCompressionPolicy()
    manager = ToolResultBudgetManager(policy)
    compressor = ToolResultCompressor()
    compressor.compress(
        [_candidate(1, "same result", rank=1)],
        policy=policy,
        budget_manager=manager,
        tool_call_id="tc_1",
        tool_name="dense_search",
    )
    repeated = compressor.compress(
        [_candidate(1, "same result", tool_call_id="tc_2", rank=1)],
        policy=policy,
        budget_manager=manager,
        tool_call_id="tc_2",
        tool_name="hybrid_search",
    )

    assert repeated.output_item_count == 0
    assert repeated.repeated_result is True
    assert repeated.drop_reasons == {"repeated_result": 1}
    assert manager.trace().summary.repeated_result_count == 1


def test_tool_result_compression_rejects_oversized_output_safely() -> None:
    policy = ToolResultCompressionPolicy(
        max_items_per_tool=1,
        max_total_items_per_turn=1,
        max_snippet_chars=100,
        max_tokens_per_tool=1,
        max_total_tool_result_tokens=1,
        reject_oversized_output=True,
    )
    manager = ToolResultBudgetManager(policy)
    result = ToolResultCompressor().compress(
        [_candidate(1, "oversized" * 20)],
        policy=policy,
        budget_manager=manager,
        tool_call_id="tc_1",
        tool_name="dense_search",
    )

    assert result.status == "failed"
    assert result.error_code == "oversized_tool_output"
    assert result.oversized_rejected is True
    assert result.items == []
    assert result.drop_reasons["oversized_rejected"] == 1


def test_tool_result_trace_has_no_snippet_or_raw_text_fields() -> None:
    policy = ToolResultCompressionPolicy(max_snippet_chars=40)
    manager = ToolResultBudgetManager(policy)
    ToolResultCompressor().compress(
        [
            _candidate(
                1,
                "raw chunk text must not be persisted token=abcd1234",
                source=r"C:\Users\kei01\private",
            )
        ],
        policy=policy,
        budget_manager=manager,
        tool_call_id="tc_1",
        tool_name="dense_search",
    )
    dumped = json.dumps(manager.trace().model_dump(mode="json"), sort_keys=True)

    assert '"snippet":' not in dumped
    assert "raw chunk text" not in dumped
    assert "token=abcd1234" not in dumped
    assert r"C:\Users" not in dumped
    assert "snippet_hash" in dumped


def test_tool_result_trace_sanitizer_allowlists_safe_shape() -> None:
    sanitized = sanitize_tool_result_compression_json(
        {
            "schema_version": "phase2.tool_result_compression.v1",
            "enabled": True,
            "budget": {
                "max_items_per_tool": 8,
                "max_total_items_per_turn": 20,
                "max_snippet_chars": 500,
                "max_tokens_per_tool": 1200,
                "max_total_tool_result_tokens": 3000,
                "token_estimator": "heuristic",
                "drop_low_score_first": True,
                "group_by_source": True,
                "reject_oversized_output": True,
            },
            "summary": {
                "tool_call_count": 1,
                "search_tool_call_count": 1,
                "original_item_count": 1,
                "output_item_count": 1,
                "dropped_item_count": 0,
                "estimated_tokens_before": 10,
                "estimated_tokens_after": 5,
                "compression_ratio": 0.5,
                "budget_exhausted": False,
                "repeated_result_count": 0,
                "oversized_rejected_count": 0,
            },
            "drop_reasons": {"raw_prompt": 1, "max_items_limit": 0},
            "by_tool": [],
            "item_refs": [
                {
                    "tool_call_id": "tc_1",
                    "tool_name": "dense_search",
                    "document_chunk_id": 10,
                    "source_label": r"C:\Users\kei01\private.md",
                    "citation_candidate": True,
                    "snippet_hash": "a" * 64,
                    "original_char_count": 40,
                    "snippet_char_count": 20,
                    "estimated_tokens": 5,
                    "source_group_key": "source-a",
                    "compression_method": "max_chars_per_snippet",
                    "snippet": "must not survive",
                }
            ],
            "raw_prompt": "must not survive",
        }
    )

    assert sanitized is not None
    dumped = json.dumps(sanitized, sort_keys=True)
    assert "max_items_per_tool" in dumped
    assert "raw_prompt" not in dumped
    assert '"snippet":' not in dumped
    assert "must not survive" not in dumped
    assert r"C:\Users" not in dumped
    assert "redacted" in dumped


def test_tool_result_log_payload_redaction() -> None:
    payload = OrchestratorContextGuard().safe_log_payload(
        {
            "request_id": "req-1",
            "retrieval_run_id": 1,
            "tool_name": "dense_search",
            "tool_call_id": "tc_1",
            "snippet": "raw text",
            "raw_prompt": "prompt",
            "drop_reason_counts": {"max_items_limit": 1},
        }
    )

    dumped = json.dumps(payload, sort_keys=True)
    assert "req-1" in dumped
    assert "max_items_limit" in dumped
    assert "raw text" not in dumped
    assert "prompt" not in dumped
