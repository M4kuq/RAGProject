from __future__ import annotations

from app.rag.llm_orchestrator import (
    LLMToolCallingRetrievalOrchestrator,
    LLMToolResult,
    _llm_tool_result_from_compressed,
    _normalized_query,
)
from app.rag.sparse import normalize_sparse_query
from app.rag.tool_result_compression import CompressedToolResult


def _orchestrator(clock_values: list[float]) -> LLMToolCallingRetrievalOrchestrator:
    from app.core.config import Settings

    settings = Settings(app_env="test", embedding_provider="fake", embedding_fake_dimension=4)
    calls = {"i": 0}

    def clock() -> float:
        index = min(calls["i"], len(clock_values) - 1)
        calls["i"] += 1
        return clock_values[index]

    return LLMToolCallingRetrievalOrchestrator(settings, planner=None, clock=clock)


def test_remaining_seconds_positive_then_expired() -> None:
    orchestrator = _orchestrator([10.0, 19.0, 25.0])
    deadline = 20.0
    # clock() == 10.0 -> 10 seconds left
    assert orchestrator._remaining_seconds(deadline) == 10.0
    # clock() == 19.0 -> 1 second left
    assert orchestrator._remaining_seconds(deadline) == 1.0
    # clock() == 25.0 -> past the deadline
    assert orchestrator._remaining_seconds(deadline) <= 0


def test_dropped_item_count_is_visible_to_planner() -> None:
    compressed = CompressedToolResult(
        tool_call_id="tc_1",
        tool_name="dense_search",
        status="succeeded",
        original_item_count=5,
        output_item_count=2,
        dropped_item_count=3,
    )
    result = _llm_tool_result_from_compressed(compressed)

    assert result.dropped_item_count == 3
    assert result.truncated is True

    planner_payload = result.to_planner_payload()
    assert planner_payload["dropped_item_count"] == 3
    assert planner_payload["truncated"] is True

    trace_payload = result.to_trace()
    assert trace_payload["dropped_item_count"] == 3


def test_tool_result_without_drops_is_not_truncated() -> None:
    result = LLMToolResult(
        tool_call_id="tc_1",
        tool_name="dense_search",
        status="succeeded",
        item_count=2,
    )
    assert result.truncated is False
    assert result.to_planner_payload()["dropped_item_count"] == 0


def test_query_normalizations_are_intentionally_different() -> None:
    query = "Reset RESET the   widget?"
    # Orchestrator dedup normalization preserves the whole string (lowercased,
    # whitespace-collapsed) including punctuation and repeated words.
    assert _normalized_query(query) == "reset reset the widget?"
    # FTS tokenization splits on [A-Za-z0-9_]+, strips underscores, dedupes terms,
    # and drops trailing punctuation.
    sparse = normalize_sparse_query(query, max_terms=10)
    assert sparse.terms == ("reset", "the", "widget")
