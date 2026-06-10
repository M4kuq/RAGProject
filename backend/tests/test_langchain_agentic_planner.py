from __future__ import annotations

from app.rag.langchain_agentic import (
    LangChainPlanningState,
    LangChainToolCall,
    LangChainToolResult,
    _plan_next_calls,
)


def test_langchain_agentic_planner_skips_failed_empty_search_keys() -> None:
    query = "alpha beta gamma delta epsilon zeta retrieval evidence"
    call = _plan_next_calls(
        LangChainPlanningState(
            user_query=query,
            max_query_chars=200,
            remaining_tool_calls=3,
            remaining_search_calls=3,
            available_tools=("dense_search", "sparse_search", "hybrid_search", "finalize_answer"),
            tool_results=[
                LangChainToolResult(
                    tool_call_id="lc_1",
                    tool_name="hybrid_search",
                    status="failed",
                    item_count=0,
                    error_code="oversized_tool_output",
                    normalized_query=query,
                )
            ],
        )
    )

    assert call == [LangChainToolCall(tool_name="sparse_search", arguments={"query": query})]
