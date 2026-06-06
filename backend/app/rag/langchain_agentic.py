from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from langchain_core.runnables import RunnableLambda
from langchain_core.tools import BaseTool, StructuredTool

from app.core.config import Settings
from app.rag.agentic import (
    AgenticRetrievalResult,
    RetrievalAttemptResult,
    merge_dedupe_candidates,
)
from app.rag.strategy import RetrievalStrategy
from app.rag.tool_result_compression import (
    CompressedToolResult,
    ToolResultBudgetManager,
    ToolResultCandidate,
    ToolResultCompressionPolicy,
    ToolResultCompressionTrace,
    ToolResultCompressor,
    ToolResultItem,
    tool_result_item_from_candidate,
)
from app.rag.trace import LatencyTracker, TraceRedactor
from app.repositories.retrieval_repository import CheckedRetrievalCandidate

LANGCHAIN_AGENTIC_SCHEMA_VERSION = "phase2.langchain_agentic.v1"
LANGCHAIN_SEARCH_TOOL_NAMES = {"dense_search", "sparse_search", "hybrid_search"}
LANGCHAIN_ALLOWED_TOOL_NAMES = {
    *LANGCHAIN_SEARCH_TOOL_NAMES,
    "finalize_answer",
}


@dataclass(frozen=True)
class LangChainToolCall:
    tool_name: str
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LangChainToolResult:
    tool_call_id: str
    tool_name: str
    status: str
    item_count: int = 0
    items: list[ToolResultItem] = field(default_factory=list)
    error_code: str | None = None

    def to_trace(self) -> dict[str, object]:
        return TraceRedactor.safe_dict(
            {
                "tool_call_id": self.tool_call_id,
                "tool_name": self.tool_name,
                "status": self.status,
                "item_count": self.item_count,
                "error_code": self.error_code,
            }
        )


@dataclass(frozen=True)
class LangChainPlanningState:
    user_query: str
    max_query_chars: int
    remaining_tool_calls: int
    remaining_search_calls: int
    available_tools: Sequence[str]
    tool_results: Sequence[LangChainToolResult]


@dataclass(frozen=True)
class LangChainAgenticExecutionResult:
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
                "langchain_agentic_schema_version": LANGCHAIN_AGENTIC_SCHEMA_VERSION,
                "orchestrator_provider": "langchain",
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
                "langchain_agentic_schema_version": LANGCHAIN_AGENTIC_SCHEMA_VERSION,
                "orchestrator_provider": "langchain",
                "tool_call_count": self.tool_call_count,
                "search_call_count": self.search_call_count,
                "tools_used": self.tools_used,
                "budget_exhausted": self.budget_exhausted,
                "timeout_exceeded": self.timeout_exceeded,
                "repeated_query_detected": self.repeated_query_detected,
                "finalize_called": self.finalize_called,
                "best_effort_finalize_used": self.best_effort_finalize_used,
                "no_context": self.no_context,
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


class LangChainAgenticRetrievalOrchestrator:
    def __init__(
        self,
        settings: Settings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.planning_chain = RunnableLambda(_plan_next_calls)

    def execute(
        self,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        retrieve: Callable[[RetrievalStrategy, str, str], RetrievalAttemptResult],
        latency_tracker: LatencyTracker,
    ) -> LangChainAgenticExecutionResult:
        started_at = self.clock()
        max_tool_calls = _bounded_int(self.settings.langchain_agentic_max_tool_calls, 1, 10)
        max_search_calls = min(
            _bounded_int(self.settings.langchain_agentic_max_search_calls, 1, 10),
            max_tool_calls,
        )
        timeout_seconds = max(1.0, float(self.settings.langchain_agentic_timeout_seconds))
        max_query_chars = _bounded_int(self.settings.langchain_agentic_max_query_chars, 1, 1000)
        legacy_result_item_limit = _bounded_int(
            self.settings.langchain_agentic_max_tool_result_items,
            1,
            20,
        )
        legacy_snippet_chars = _bounded_int(
            self.settings.langchain_agentic_max_snippet_chars,
            20,
            1000,
        )
        compression_policy = _tool_result_compression_policy(self.settings)
        compressor = ToolResultCompressor()
        budget_manager = ToolResultBudgetManager(compression_policy)
        available_tools = _available_tool_names(self.settings)
        tools_by_name = _search_tools(
            available_tools=available_tools,
            retrieve=retrieve,
        )

        tool_results: list[LangChainToolResult] = []
        attempts_by_tool_call_id: dict[str, RetrievalAttemptResult] = {}
        selected_tool_call_ids: list[str] = []
        seen_searches: set[tuple[str, str]] = set()
        tool_call_count = 0
        search_call_count = 0
        repeated_query_detected = False
        finalize_called = False
        timeout_exceeded = False
        reason_codes: list[str] = [
            "langchain_agentic_started",
            "langchain_runnable_planner",
            "langchain_structured_tools",
        ]

        def append_error_result(
            *,
            tool_call_id: str,
            tool_name: str,
            error_code: str,
        ) -> None:
            compressed = compressor.error_result(
                policy=compression_policy,
                budget_manager=budget_manager,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                error_code=error_code,
            )
            tool_results.append(_langchain_tool_result_from_compressed(compressed))

        with latency_tracker.span("langchain_agentic_ms"):
            while tool_call_count < max_tool_calls:
                elapsed_seconds = self.clock() - started_at
                remaining_timeout = timeout_seconds - elapsed_seconds
                if remaining_timeout <= 0:
                    timeout_exceeded = True
                    reason_codes.append("timeout_exceeded")
                    break
                with latency_tracker.span("langchain_planning_ms"):
                    planned_calls = self.planning_chain.invoke(
                        LangChainPlanningState(
                            user_query=query[:max_query_chars],
                            max_query_chars=max_query_chars,
                            remaining_tool_calls=max_tool_calls - tool_call_count,
                            remaining_search_calls=max_search_calls - search_call_count,
                            available_tools=available_tools,
                            tool_results=tool_results,
                        )
                    )
                if self.clock() - started_at > timeout_seconds:
                    timeout_exceeded = True
                    reason_codes.append("timeout_exceeded")
                    break
                if not planned_calls:
                    planned_calls = [
                        LangChainToolCall(
                            tool_name="finalize_answer",
                            arguments={
                                "selected_tool_call_ids": list(attempts_by_tool_call_id),
                                "answer_intent": "final_answer",
                            },
                        )
                    ]
                    reason_codes.append("langchain_planner_no_call_finalize")

                for planned_call in planned_calls:
                    if tool_call_count >= max_tool_calls:
                        reason_codes.append("max_tool_calls_exhausted")
                        break
                    tool_call_count += 1
                    tool_call_id = f"lc_{tool_call_count}"
                    tool_name = planned_call.tool_name
                    if tool_name not in LANGCHAIN_ALLOWED_TOOL_NAMES:
                        append_error_result(
                            tool_call_id=tool_call_id,
                            tool_name="unknown",
                            error_code="tool_not_allowed",
                        )
                        reason_codes.append("tool_not_allowed")
                        continue
                    if tool_name == "finalize_answer":
                        finalize_called = True
                        selected_ids = _selected_tool_call_ids(planned_call.arguments)
                        selected_tool_call_ids = (
                            selected_ids
                            if selected_ids is not None
                            else list(attempts_by_tool_call_id)
                        )
                        if not selected_tool_call_ids:
                            reason_codes.append("finalize_answer_empty_selection")
                        reason_codes.append("finalize_answer_called")
                        break

                    if search_call_count >= max_search_calls:
                        append_error_result(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            error_code="max_search_calls_exhausted",
                        )
                        reason_codes.append("max_search_calls_exhausted")
                        continue
                    tool = tools_by_name.get(tool_name)
                    if tool is None:
                        append_error_result(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            error_code="strategy_not_enabled",
                        )
                        reason_codes.append("strategy_not_enabled")
                        continue
                    tool_query = _tool_query(planned_call.arguments, fallback=query)
                    search_key = (tool_name, _normalized_query(tool_query))
                    if search_key in seen_searches:
                        repeated_query_detected = True
                        reason_codes.append("repeated_query_detected")
                        break
                    seen_searches.add(search_key)
                    with latency_tracker.span("langchain_tool_execution_ms"):
                        attempt = tool.invoke({"query": tool_query[:max_query_chars]})
                    if not isinstance(attempt, RetrievalAttemptResult):
                        append_error_result(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            error_code="tool_result_invalid",
                        )
                        reason_codes.append("tool_result_invalid")
                        continue
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
                        attempts_by_tool_call_id[tool_call_id] = _attempt_with_candidates(
                            attempt,
                            compressed.items,
                        )
                        tool_results.append(_langchain_tool_result_from_compressed(compressed))
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
                        attempts_by_tool_call_id[tool_call_id] = attempt
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
                            )
                        )
                        reason_codes.append("tool_result_compression_skipped")
                    reason_codes.append(f"{tool_name}_called")
                if finalize_called or repeated_query_detected:
                    break

        budget_exhausted = tool_call_count >= max_tool_calls and not finalize_called
        if search_call_count >= max_search_calls and not finalize_called:
            budget_exhausted = True
        selected_attempts = [
            attempts_by_tool_call_id[tool_call_id]
            for tool_call_id in selected_tool_call_ids
            if tool_call_id in attempts_by_tool_call_id
        ]
        best_effort_finalize_used = False
        best_effort_finalize_reason = None
        if repeated_query_detected:
            best_effort_finalize_reason = "best_effort_finalize_after_repeated_query"
        elif budget_exhausted or timeout_exceeded:
            best_effort_finalize_reason = "best_effort_finalize_after_budget_or_timeout"
        if (
            not finalize_called
            and not selected_attempts
            and attempts_by_tool_call_id
            and best_effort_finalize_reason is not None
        ):
            selected_tool_call_ids = list(attempts_by_tool_call_id)
            selected_attempts = [
                attempts_by_tool_call_id[tool_call_id]
                for tool_call_id in selected_tool_call_ids
                if tool_call_id in attempts_by_tool_call_id
            ]
            best_effort_finalize_used = True
            reason_codes.append(best_effort_finalize_reason)
        final_candidates = (
            merge_dedupe_candidates(selected_attempts, limit=top_k) if selected_attempts else []
        )
        no_context = (not finalize_called and not best_effort_finalize_used) or not final_candidates
        if no_context:
            reason_codes.append("no_context")
        retrieval_result = AgenticRetrievalResult(
            final_candidates=final_candidates,
            retrieval_call_count=search_call_count,
            initial_strategy=(
                selected_attempts[0].strategy
                if selected_attempts
                else RetrievalStrategy.FALLBACK_DENSE
            ),
            fallback_strategies=[attempt.strategy for attempt in selected_attempts[1:]],
            fallback_used=len(selected_attempts) > 1,
            fallback_reason="langchain_additional_search" if len(selected_attempts) > 1 else None,
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
        return LangChainAgenticExecutionResult(
            retrieval_result=retrieval_result,
            tool_results=tool_results,
            tool_call_count=tool_call_count,
            search_call_count=search_call_count,
            tools_used=[result.tool_name for result in tool_results],
            budget_exhausted=budget_exhausted,
            timeout_exceeded=timeout_exceeded,
            repeated_query_detected=repeated_query_detected,
            finalize_called=finalize_called,
            best_effort_finalize_used=best_effort_finalize_used,
            no_context=no_context,
            reason_codes=_deduped_reason_codes(reason_codes),
            tool_result_compression_trace=budget_manager.trace(),
        )


def _plan_next_calls(state: LangChainPlanningState) -> list[LangChainToolCall]:
    successful_search_results = [
        result
        for result in state.tool_results
        if result.tool_name in LANGCHAIN_SEARCH_TOOL_NAMES
        and result.status == "succeeded"
        and result.item_count > 0
    ]
    if successful_search_results:
        return [
            LangChainToolCall(
                tool_name="finalize_answer",
                arguments={
                    "selected_tool_call_ids": [
                        result.tool_call_id for result in successful_search_results
                    ],
                    "answer_intent": "final_answer",
                },
            )
        ]
    if state.remaining_search_calls <= 0:
        return []
    if "hybrid_search" in state.available_tools:
        return [
            LangChainToolCall(
                tool_name="hybrid_search",
                arguments={"query": state.user_query[: state.max_query_chars]},
            )
        ]
    if "sparse_search" in state.available_tools and _looks_keyword_heavy(state.user_query):
        return [
            LangChainToolCall(
                tool_name="sparse_search",
                arguments={"query": state.user_query[: state.max_query_chars]},
            )
        ]
    return [
        LangChainToolCall(
            tool_name="dense_search",
            arguments={"query": state.user_query[: state.max_query_chars]},
        )
    ]


def _search_tools(
    *,
    available_tools: Sequence[str],
    retrieve: Callable[[RetrievalStrategy, str, str], RetrievalAttemptResult],
) -> dict[str, BaseTool]:
    tools: dict[str, BaseTool] = {}
    for tool_name in available_tools:
        if tool_name not in LANGCHAIN_SEARCH_TOOL_NAMES:
            continue
        strategy = _tool_strategy(tool_name)

        tools[tool_name] = StructuredTool.from_function(
            func=_search_tool_function(strategy=strategy, retrieve=retrieve),
            name=tool_name,
            description=f"Run read-only {strategy.value} retrieval for RAG evidence.",
        )
    return tools


def _search_tool_function(
    *,
    strategy: RetrievalStrategy,
    retrieve: Callable[[RetrievalStrategy, str, str], RetrievalAttemptResult],
) -> Callable[[str], RetrievalAttemptResult]:
    def search(query: str) -> RetrievalAttemptResult:
        """Run a bounded read-only retrieval strategy."""
        return retrieve(strategy, "langchain_tool", query)

    return search


def _tool_strategy(tool_name: str) -> RetrievalStrategy:
    if tool_name == "sparse_search":
        return RetrievalStrategy.SPARSE
    if tool_name == "hybrid_search":
        return RetrievalStrategy.HYBRID
    return RetrievalStrategy.DENSE


def _available_tool_names(settings: Settings) -> tuple[str, ...]:
    tools = ["dense_search", "finalize_answer"]
    if settings.sparse_enabled:
        tools.append("sparse_search")
    if settings.hybrid_enabled and (
        settings.sparse_enabled or float(settings.hybrid_sparse_weight) <= 0
    ):
        tools.append("hybrid_search")
    return tuple(tools)


def _tool_query(arguments: dict[str, object], *, fallback: str) -> str:
    value = arguments.get("query")
    if isinstance(value, str) and value.strip():
        return _bounded_executable_query(value, max_chars=1000)
    return fallback


def _selected_tool_call_ids(arguments: dict[str, object]) -> list[str] | None:
    if "selected_tool_call_ids" not in arguments:
        return None
    value = arguments.get("selected_tool_call_ids")
    if not isinstance(value, list):
        return None
    ids: list[str] = []
    for item in value[:20]:
        if isinstance(item, str) and item.startswith("lc_"):
            ids.append(item[:40])
    return ids


def _bounded_executable_query(value: str, *, max_chars: int) -> str:
    normalized = " ".join(value.replace("\x00", " ").split())
    return normalized[:max_chars]


def _looks_keyword_heavy(query: str) -> bool:
    words = [word for word in query.replace("_", " ").split() if word]
    unique_words = set(words)
    return len(unique_words) >= 6 or any(len(word) >= 16 for word in unique_words)


def _tool_result_candidates(
    candidates: Sequence[CheckedRetrievalCandidate],
    *,
    tool_call_id: str,
    tool_name: str,
) -> list[ToolResultCandidate]:
    return [
        ToolResultCandidate(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            document_chunk_id=candidate.chunk.document_chunk_id,
            text=candidate.chunk.content_text,
            source_label=_candidate_source_label(candidate),
            section_title=candidate.chunk.section_title,
            page_from=candidate.chunk.page_from,
            page_to=candidate.chunk.page_to,
            rank=candidate.rank_order,
            retrieval_score=round(float(candidate.retrieval_score), 6),
            fusion_score=_payload_float(candidate, "fused_score")
            or _payload_float(candidate, "fusion_score"),
            citation_candidate=True,
            source_group_key=f"logical_document:{candidate.logical_document.logical_document_id}",
        )
        for candidate in candidates
    ]


def _legacy_tool_result_items(
    candidates: Sequence[CheckedRetrievalCandidate],
    *,
    tool_call_id: str,
    tool_name: str,
    max_item_count: int,
    max_snippet_chars: int,
) -> list[ToolResultItem]:
    items: list[ToolResultItem] = []
    for candidate in _tool_result_candidates(
        candidates[:max_item_count],
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    ):
        item = tool_result_item_from_candidate(candidate, max_snippet_chars=max_snippet_chars)
        if item is not None:
            items.append(item)
    return items


def _attempt_with_candidates(
    attempt: RetrievalAttemptResult,
    items: Sequence[ToolResultItem],
) -> RetrievalAttemptResult:
    candidate_by_chunk_id = {
        candidate.chunk.document_chunk_id: candidate for candidate in attempt.candidates
    }
    filtered = [
        candidate_by_chunk_id[item.document_chunk_id]
        for item in items
        if item.document_chunk_id in candidate_by_chunk_id
    ]
    return RetrievalAttemptResult(
        strategy=attempt.strategy,
        candidates=filtered,
        qdrant_candidate_count=attempt.qdrant_candidate_count,
        sparse_candidate_count=attempt.sparse_candidate_count,
        hybrid_candidate_count=attempt.hybrid_candidate_count,
        excluded_by_rdb_check_count=attempt.excluded_by_rdb_check_count,
        role=attempt.role,
    )


def _langchain_tool_result_from_compressed(result: CompressedToolResult) -> LangChainToolResult:
    return LangChainToolResult(
        tool_call_id=result.tool_call_id,
        tool_name=result.tool_name,
        status=result.status,
        item_count=result.output_item_count,
        items=result.items,
        error_code=result.error_code,
    )


def _tool_result_compression_policy(settings: Settings) -> ToolResultCompressionPolicy:
    return ToolResultCompressionPolicy(
        enabled=settings.tool_result_compression_enabled,
        max_items_per_tool=settings.tool_result_compression_max_items_per_tool,
        max_total_items_per_turn=settings.tool_result_compression_max_total_items_per_turn,
        max_snippet_chars=settings.tool_result_compression_max_snippet_chars,
        max_tokens_per_tool=settings.tool_result_compression_max_tokens_per_tool,
        max_total_tool_result_tokens=settings.tool_result_compression_max_total_tool_result_tokens,
        drop_low_score_first=settings.tool_result_compression_drop_low_score_first,
        group_by_source=settings.tool_result_compression_group_by_source,
        reject_oversized_output=settings.tool_result_compression_reject_oversized_output,
        store_debug_trace=settings.tool_result_compression_store_debug_trace,
    )


def _candidate_source_label(candidate: CheckedRetrievalCandidate) -> str:
    raw_label = candidate.document_version.file_name or candidate.logical_document.title
    safe = TraceRedactor.safe_string(raw_label, max_length=180)
    if not safe or safe == "redacted":
        safe = f"document:{candidate.logical_document.logical_document_id}"
    section_title = TraceRedactor.safe_string(candidate.chunk.section_title or "", max_length=80)
    if section_title and section_title != "redacted":
        safe = f"{safe} / {section_title}"
    return safe[:255]


def _payload_float(candidate: CheckedRetrievalCandidate, key: str) -> float | None:
    value = candidate.payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 6)


def _normalized_query(query: str) -> str:
    return " ".join(query.lower().split())[:500]


def _bounded_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _sum_optional(values: Iterable[int | None]) -> int | None:
    safe_values = [value for value in values if value is not None]
    if not safe_values:
        return None
    return sum(safe_values)


def _deduped_reason_codes(reason_codes: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for code in reason_codes:
        safe = TraceRedactor.safe_string(code, max_length=100)
        if not safe or safe in seen:
            continue
        deduped.append(safe)
        seen.add(safe)
    return deduped[:50]
