from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, StateGraph

from app.core.config import Settings
from app.rag.agentic import (
    AgenticRetrievalResult,
    RetrievalAttemptResult,
    merge_dedupe_candidates,
)
from app.rag.langchain_agentic import (
    LangChainToolResult,
    _attempt_with_candidates,
    _available_tool_names,
    _bounded_int,
    _deduped_reason_codes,
    _langchain_tool_result_from_compressed,
    _legacy_tool_result_items,
    _looks_keyword_heavy,
    _normalized_query,
    _search_tools,
    _sum_optional,
    _tool_query,
    _tool_result_candidates,
    _tool_result_compression_policy,
)
from app.rag.strategy import RetrievalStrategy
from app.rag.tool_result_compression import (
    ToolResultBudgetManager,
    ToolResultCompressionTrace,
    ToolResultCompressor,
)
from app.rag.trace import LatencyTracker, TraceRedactor

LANGGRAPH_AGENTIC_SCHEMA_VERSION = "phase2.langgraph_agentic.v1"
LANGGRAPH_SEARCH_TOOL_NAMES = {"dense_search", "sparse_search", "hybrid_search"}
LANGGRAPH_ALLOWED_TOOL_NAMES = {
    *LANGGRAPH_SEARCH_TOOL_NAMES,
    "finalize_answer",
}


@dataclass(frozen=True)
class LangGraphToolCall:
    tool_name: str
    arguments: dict[str, object] = field(default_factory=dict)


class LangGraphAgenticState(TypedDict):
    user_query: str
    max_query_chars: int
    max_tool_calls: int
    max_search_calls: int
    started_at: float
    timeout_seconds: float
    available_tools: Sequence[str]
    tool_results: list[LangChainToolResult]
    attempts_by_tool_call_id: dict[str, RetrievalAttemptResult]
    selected_tool_call_ids: list[str]
    seen_searches: set[tuple[str, str]]
    tool_call_count: int
    search_call_count: int
    timeout_exceeded: bool
    repeated_query_detected: bool
    finalize_called: bool
    stop_requested: bool
    reason_codes: list[str]
    next_call: NotRequired[LangGraphToolCall | None]


@dataclass(frozen=True)
class LangGraphAgenticExecutionResult:
    retrieval_result: AgenticRetrievalResult
    tool_results: list[LangChainToolResult]
    tool_call_count: int
    search_call_count: int
    tools_used: list[str]
    budget_exhausted: bool
    timeout_exceeded: bool
    repeated_query_detected: bool
    finalize_called: bool
    best_effort_finalize_used: bool
    no_context: bool
    reason_codes: list[str]
    graph_node_count: int
    graph_transition_count: int
    tool_result_compression_trace: ToolResultCompressionTrace | None = None

    def decision_trace_fields(self) -> dict[str, object]:
        retrieval_result = self.retrieval_result
        fallback_strategy = (
            retrieval_result.fallback_strategies[-1].value
            if retrieval_result.fallback_strategies
            else None
        )
        return TraceRedactor.safe_dict(
            {
                "langgraph_agentic_schema_version": LANGGRAPH_AGENTIC_SCHEMA_VERSION,
                "orchestrator_provider": "langgraph",
                "tool_call_count": self.tool_call_count,
                "search_call_count": self.search_call_count,
                "tools_used": self.tools_used,
                "retrieval_call_count": retrieval_result.retrieval_call_count,
                "fallback_used": retrieval_result.fallback_used,
                "fallback_strategy": fallback_strategy,
                "fallback_reason": retrieval_result.fallback_reason,
                "budget_exhausted": self.budget_exhausted,
                "timeout_exceeded": self.timeout_exceeded,
                "repeated_query_detected": self.repeated_query_detected,
                "finalize_called": self.finalize_called,
                "best_effort_finalize_used": self.best_effort_finalize_used,
                "no_context": self.no_context,
                "sufficiency_score": None,
                "sufficiency_reason_codes": [],
                "tool_result_compression_enabled": (
                    self.tool_result_compression_trace.enabled
                    if self.tool_result_compression_trace is not None
                    else None
                ),
                "graph_node_count": self.graph_node_count,
                "graph_transition_count": self.graph_transition_count,
                "merged_candidate_count": retrieval_result.merged_candidate_count,
                "deduped_candidate_count": retrieval_result.deduped_candidate_count,
                "final_selected_count": retrieval_result.final_selected_count,
                "qdrant_candidate_count": retrieval_result.qdrant_candidate_count,
                "sparse_candidate_count": retrieval_result.sparse_candidate_count,
                "hybrid_candidate_count": retrieval_result.hybrid_candidate_count,
                "excluded_by_rdb_check_count": retrieval_result.excluded_by_rdb_check_count,
                "reason_codes": self.reason_codes,
                "tool_results": [result.to_trace() for result in self.tool_results],
            }
        )

    def summary_fields(self) -> dict[str, object]:
        return TraceRedactor.safe_dict(
            {
                "langgraph_agentic_schema_version": LANGGRAPH_AGENTIC_SCHEMA_VERSION,
                "orchestrator_provider": "langgraph",
                "tool_call_count": self.tool_call_count,
                "search_call_count": self.search_call_count,
                "tools_used": self.tools_used,
                "budget_exhausted": self.budget_exhausted,
                "timeout_exceeded": self.timeout_exceeded,
                "repeated_query_detected": self.repeated_query_detected,
                "finalize_called": self.finalize_called,
                "best_effort_finalize_used": self.best_effort_finalize_used,
                "no_context": self.no_context,
                "graph_node_count": self.graph_node_count,
                "graph_transition_count": self.graph_transition_count,
                "tool_result_compression": (
                    self.tool_result_compression_trace.summary.model_dump(
                        mode="json",
                        exclude_none=True,
                    )
                    if self.tool_result_compression_trace is not None
                    else None
                ),
            }
        )


class LangGraphAgenticRetrievalOrchestrator:
    def __init__(
        self,
        settings: Settings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.clock = clock

    def execute(
        self,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        retrieve: Callable[[RetrievalStrategy, str, str], RetrievalAttemptResult],
        latency_tracker: LatencyTracker,
    ) -> LangGraphAgenticExecutionResult:
        started_at = self.clock()
        max_tool_calls = _bounded_int(self.settings.langgraph_agentic_max_tool_calls, 1, 10)
        max_search_calls = min(
            _bounded_int(self.settings.langgraph_agentic_max_search_calls, 1, 10),
            max_tool_calls,
        )
        timeout_seconds = max(1.0, float(self.settings.langgraph_agentic_timeout_seconds))
        max_query_chars = _bounded_int(self.settings.langgraph_agentic_max_query_chars, 1, 1000)
        legacy_result_item_limit = _bounded_int(
            self.settings.langgraph_agentic_max_tool_result_items,
            1,
            20,
        )
        legacy_snippet_chars = _bounded_int(
            self.settings.langgraph_agentic_max_snippet_chars,
            20,
            1000,
        )
        compression_policy = _tool_result_compression_policy(self.settings)
        compressor = ToolResultCompressor()
        budget_manager = ToolResultBudgetManager(compression_policy)
        available_tools = _available_tool_names(self.settings)
        tools_by_name = _search_tools(available_tools=available_tools, retrieve=retrieve)
        graph_node_count = 0
        graph_transition_count = 0

        def plan_next_tool(state: LangGraphAgenticState) -> dict[str, object]:
            nonlocal graph_node_count
            graph_node_count += 1
            reason_codes = list(state["reason_codes"])
            if self.clock() - state["started_at"] > state["timeout_seconds"]:
                reason_codes.append("timeout_exceeded")
                return {
                    "timeout_exceeded": True,
                    "stop_requested": True,
                    "reason_codes": reason_codes,
                    "next_call": None,
                }
            with latency_tracker.span("langgraph_planning_ms"):
                planned_call = _plan_next_call(state)
            if planned_call is None:
                planned_call = LangGraphToolCall(
                    tool_name="finalize_answer",
                    arguments={
                        "selected_tool_call_ids": list(state["attempts_by_tool_call_id"]),
                        "answer_intent": "final_answer",
                    },
                )
                reason_codes.append("langgraph_planner_no_call_finalize")
            return {"next_call": planned_call, "reason_codes": reason_codes}

        def execute_tool(state: LangGraphAgenticState) -> dict[str, object]:
            nonlocal graph_node_count
            graph_node_count += 1
            next_call = state.get("next_call")
            if next_call is None:
                return {"stop_requested": True}
            reason_codes = list(state["reason_codes"])
            tool_results = list(state["tool_results"])
            attempts = dict(state["attempts_by_tool_call_id"])
            seen_searches = set(state["seen_searches"])
            tool_call_count = int(state["tool_call_count"]) + 1
            search_call_count = int(state["search_call_count"])
            tool_call_id = f"lg_{tool_call_count}"
            tool_name = next_call.tool_name
            selected_tool_call_ids = list(state["selected_tool_call_ids"])
            repeated_query_detected = bool(state["repeated_query_detected"])
            finalize_called = bool(state["finalize_called"])
            stop_requested = False

            if tool_call_count > state["max_tool_calls"]:
                reason_codes.append("max_tool_calls_exhausted")
                return {
                    "tool_call_count": state["tool_call_count"],
                    "stop_requested": True,
                    "reason_codes": reason_codes,
                }
            if tool_name not in LANGGRAPH_ALLOWED_TOOL_NAMES:
                compressed = compressor.error_result(
                    policy=compression_policy,
                    budget_manager=budget_manager,
                    tool_call_id=tool_call_id,
                    tool_name="unknown",
                    error_code="tool_not_allowed",
                )
                tool_results.append(_langchain_tool_result_from_compressed(compressed))
                reason_codes.append("tool_not_allowed")
                return {
                    "tool_call_count": tool_call_count,
                    "tool_results": tool_results,
                    "reason_codes": reason_codes,
                }
            if tool_name == "finalize_answer":
                finalize_called = True
                selected_ids = _selected_tool_call_ids(next_call.arguments)
                selected_tool_call_ids = (
                    selected_ids if selected_ids is not None else list(attempts)
                )
                if not selected_tool_call_ids:
                    reason_codes.append("finalize_answer_empty_selection")
                reason_codes.append("finalize_answer_called")
                stop_requested = True
                return {
                    "tool_call_count": tool_call_count,
                    "selected_tool_call_ids": selected_tool_call_ids,
                    "finalize_called": finalize_called,
                    "stop_requested": stop_requested,
                    "reason_codes": reason_codes,
                }
            if search_call_count >= state["max_search_calls"]:
                compressed = compressor.error_result(
                    policy=compression_policy,
                    budget_manager=budget_manager,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    error_code="max_search_calls_exhausted",
                )
                tool_results.append(_langchain_tool_result_from_compressed(compressed))
                reason_codes.append("max_search_calls_exhausted")
                return {
                    "tool_call_count": tool_call_count,
                    "tool_results": tool_results,
                    "reason_codes": reason_codes,
                }
            tool = tools_by_name.get(tool_name)
            if tool is None:
                compressed = compressor.error_result(
                    policy=compression_policy,
                    budget_manager=budget_manager,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    error_code="strategy_not_enabled",
                )
                tool_results.append(_langchain_tool_result_from_compressed(compressed))
                reason_codes.append("strategy_not_enabled")
                return {
                    "tool_call_count": tool_call_count,
                    "tool_results": tool_results,
                    "reason_codes": reason_codes,
                }

            tool_query = _tool_query(next_call.arguments, fallback=state["user_query"])
            search_key = (tool_name, _normalized_query(tool_query))
            if search_key in seen_searches:
                repeated_query_detected = True
                reason_codes.append("repeated_query_detected")
                return {
                    "tool_call_count": tool_call_count,
                    "repeated_query_detected": repeated_query_detected,
                    "stop_requested": True,
                    "reason_codes": reason_codes,
                }
            seen_searches.add(search_key)
            with latency_tracker.span("langgraph_tool_execution_ms"):
                attempt = tool.invoke({"query": tool_query[: state["max_query_chars"]]})
            if not isinstance(attempt, RetrievalAttemptResult):
                compressed = compressor.error_result(
                    policy=compression_policy,
                    budget_manager=budget_manager,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    error_code="tool_result_invalid",
                )
                tool_results.append(_langchain_tool_result_from_compressed(compressed))
                reason_codes.append("tool_result_invalid")
                return {
                    "tool_call_count": tool_call_count,
                    "tool_results": tool_results,
                    "seen_searches": seen_searches,
                    "reason_codes": reason_codes,
                }

            search_call_count += 1
            if compression_policy.enabled:
                compressed = compressor.compress(
                    _tool_result_candidates(
                        attempt.candidates,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                    ),
                    policy=compression_policy,
                    budget_manager=budget_manager,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                )
                attempts[tool_call_id] = _attempt_with_candidates(attempt, compressed.items)
                tool_results.append(
                    _langchain_tool_result_from_compressed(
                        compressed,
                        normalized_query=search_key[1],
                    )
                )
                if compressed.repeated_result:
                    reason_codes.append("repeated_tool_result_detected")
                if compressed.oversized_rejected:
                    reason_codes.append("oversized_tool_output_rejected")
                if compressed.budget_exhausted:
                    reason_codes.append("tool_result_budget_exhausted")
                reason_codes.append("tool_result_compression_applied")
                if compressed.status == "failed":
                    reason_codes.append(compressed.error_code or "tool_result_failed")
            else:
                attempts[tool_call_id] = attempt
                tool_results.append(
                    LangChainToolResult(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        status="succeeded",
                        item_count=len(attempt.candidates),
                        items=_legacy_tool_result_items(
                            attempt.candidates,
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            max_item_count=legacy_result_item_limit,
                            max_snippet_chars=legacy_snippet_chars,
                        ),
                        normalized_query=search_key[1],
                    )
                )
                reason_codes.append("tool_result_compression_skipped")
            reason_codes.append(f"{tool_name}_called")
            return {
                "tool_call_count": tool_call_count,
                "search_call_count": search_call_count,
                "tool_results": tool_results,
                "attempts_by_tool_call_id": attempts,
                "seen_searches": seen_searches,
                "reason_codes": reason_codes,
            }

        def should_continue(state: LangGraphAgenticState) -> str:
            nonlocal graph_transition_count
            graph_transition_count += 1
            if state["stop_requested"] or state["tool_call_count"] >= state["max_tool_calls"]:
                return "end"
            return "continue"

        builder = StateGraph(LangGraphAgenticState)
        builder.add_node("plan", plan_next_tool)
        builder.add_node("execute", execute_tool)
        builder.set_entry_point("plan")
        builder.add_edge("plan", "execute")
        builder.add_conditional_edges("execute", should_continue, {"continue": "plan", "end": END})
        graph = builder.compile()

        initial_state: LangGraphAgenticState = {
            "user_query": query[:max_query_chars],
            "max_query_chars": max_query_chars,
            "max_tool_calls": max_tool_calls,
            "max_search_calls": max_search_calls,
            "started_at": started_at,
            "timeout_seconds": timeout_seconds,
            "available_tools": available_tools,
            "tool_results": [],
            "attempts_by_tool_call_id": {},
            "selected_tool_call_ids": [],
            "seen_searches": set(),
            "tool_call_count": 0,
            "search_call_count": 0,
            "timeout_exceeded": False,
            "repeated_query_detected": False,
            "finalize_called": False,
            "stop_requested": False,
            "reason_codes": [
                "langgraph_agentic_started",
                "langgraph_state_graph",
                "langgraph_plan_execute_nodes",
                "langchain_structured_tools",
            ],
        }
        with latency_tracker.span("langgraph_agentic_ms"):
            final_state = cast(LangGraphAgenticState, graph.invoke(initial_state))

        selected_attempts = [
            final_state["attempts_by_tool_call_id"][tool_call_id]
            for tool_call_id in final_state["selected_tool_call_ids"]
            if tool_call_id in final_state["attempts_by_tool_call_id"]
        ]
        budget_exhausted = (
            final_state["tool_call_count"] >= max_tool_calls and not final_state["finalize_called"]
        )
        if (
            final_state["search_call_count"] >= max_search_calls
            and not final_state["finalize_called"]
        ):
            budget_exhausted = True
        best_effort_finalize_used = False
        reason_codes = list(final_state["reason_codes"])
        best_effort_finalize_reason = None
        if final_state["repeated_query_detected"]:
            best_effort_finalize_reason = "best_effort_finalize_after_repeated_query"
        elif budget_exhausted or final_state["timeout_exceeded"]:
            best_effort_finalize_reason = "best_effort_finalize_after_budget_or_timeout"
        if (
            not final_state["finalize_called"]
            and not selected_attempts
            and final_state["attempts_by_tool_call_id"]
            and best_effort_finalize_reason is not None
        ):
            selected_attempts = list(final_state["attempts_by_tool_call_id"].values())
            best_effort_finalize_used = True
            reason_codes.append(best_effort_finalize_reason)
        final_candidates = (
            merge_dedupe_candidates(selected_attempts, limit=top_k) if selected_attempts else []
        )
        no_context = (
            not final_state["finalize_called"] and not best_effort_finalize_used
        ) or not final_candidates
        if no_context:
            reason_codes.append("no_context")
        # Fallback metadata must reflect the EXECUTED search sequence, not just the
        # finalize-selected attempts: a first search that returned zero candidates
        # is excluded from selected_attempts, but the answer still depended on an
        # alternate retrieval path, and fallback-rate metrics must count it.
        executed_attempts = list(final_state["attempts_by_tool_call_id"].values())
        retrieval_result = AgenticRetrievalResult(
            final_candidates=final_candidates,
            retrieval_call_count=final_state["search_call_count"],
            initial_strategy=(
                executed_attempts[0].strategy
                if executed_attempts
                else RetrievalStrategy.FALLBACK_DENSE
            ),
            fallback_strategies=[attempt.strategy for attempt in executed_attempts[1:]],
            fallback_used=len(executed_attempts) > 1,
            fallback_reason=("langgraph_additional_search" if len(executed_attempts) > 1 else None),
            sufficiency_decisions=[],
            merged_candidate_count=sum(len(attempt.candidates) for attempt in selected_attempts),
            deduped_candidate_count=len(final_candidates),
            final_selected_count=0 if no_context else min(rerank_top_n, len(final_candidates)),
            no_context=no_context,
            budget_exhausted=budget_exhausted,
            qdrant_candidate_count=sum(
                attempt.qdrant_candidate_count for attempt in selected_attempts
            ),
            sparse_candidate_count=_sum_optional(
                attempt.sparse_candidate_count for attempt in selected_attempts
            ),
            hybrid_candidate_count=_sum_optional(
                attempt.hybrid_candidate_count for attempt in selected_attempts
            ),
            excluded_by_rdb_check_count=sum(
                attempt.excluded_by_rdb_check_count for attempt in selected_attempts
            ),
        )
        return LangGraphAgenticExecutionResult(
            retrieval_result=retrieval_result,
            tool_results=final_state["tool_results"],
            tool_call_count=final_state["tool_call_count"],
            search_call_count=final_state["search_call_count"],
            tools_used=[result.tool_name for result in final_state["tool_results"]],
            budget_exhausted=budget_exhausted,
            timeout_exceeded=final_state["timeout_exceeded"],
            repeated_query_detected=final_state["repeated_query_detected"],
            finalize_called=final_state["finalize_called"],
            best_effort_finalize_used=best_effort_finalize_used,
            no_context=no_context,
            reason_codes=_deduped_reason_codes(reason_codes),
            graph_node_count=graph_node_count,
            graph_transition_count=graph_transition_count,
            tool_result_compression_trace=budget_manager.trace(),
        )


def _plan_next_call(state: LangGraphAgenticState) -> LangGraphToolCall | None:
    query = state["user_query"][: state["max_query_chars"]]
    successful_search_results = [
        result
        for result in state["tool_results"]
        if result.tool_name in LANGGRAPH_SEARCH_TOOL_NAMES
        and result.status == "succeeded"
        and result.item_count > 0
    ]
    if successful_search_results:
        return LangGraphToolCall(
            tool_name="finalize_answer",
            arguments={
                "selected_tool_call_ids": [
                    result.tool_call_id for result in successful_search_results
                ],
                "answer_intent": "final_answer",
            },
        )
    if state["search_call_count"] >= state["max_search_calls"]:
        return None
    empty_search_keys = _empty_search_keys(state["tool_results"])
    normalized_query = _normalized_query(query)
    for tool_name in _search_tool_priority(state):
        if (tool_name, normalized_query) in empty_search_keys:
            continue
        return LangGraphToolCall(tool_name=tool_name, arguments={"query": query})
    return None


def _empty_search_keys(
    tool_results: Sequence[LangChainToolResult],
) -> set[tuple[str, str]]:
    return {
        (result.tool_name, result.normalized_query)
        for result in tool_results
        if result.tool_name in LANGGRAPH_SEARCH_TOOL_NAMES
        and result.item_count == 0
        and result.normalized_query is not None
    }


def _search_tool_priority(state: LangGraphAgenticState) -> list[str]:
    available_tools = set(state["available_tools"])
    tools: list[str] = []
    if "hybrid_search" in available_tools:
        tools.append("hybrid_search")
    if "sparse_search" in available_tools and _looks_keyword_heavy(state["user_query"]):
        tools.append("sparse_search")
    if "dense_search" in available_tools:
        tools.append("dense_search")
    if "sparse_search" in available_tools and "sparse_search" not in tools:
        tools.append("sparse_search")
    return tools


def _selected_tool_call_ids(arguments: dict[str, object]) -> list[str] | None:
    if "selected_tool_call_ids" not in arguments:
        return None
    value = arguments.get("selected_tool_call_ids")
    if not isinstance(value, list):
        return None
    ids: list[str] = []
    for item in value[:20]:
        if isinstance(item, str) and item.startswith("lg_"):
            ids.append(item[:40])
    return ids
