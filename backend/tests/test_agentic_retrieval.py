from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from app.core.config import Settings
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument
from app.rag.agentic import (
    AgenticRetrievalExecutor,
    ContextSufficiencyChecker,
    RetrievalAttemptResult,
)
from app.rag.agentic_planner import (
    AgenticPlannerResult,
    AgenticStrategyPlan,
    AgenticStrategyPlanningRequest,
)
from app.rag.strategy import QueryIntent, RetrievalStrategy
from app.rag.trace import LatencyTracker
from app.repositories.retrieval_repository import CheckedRetrievalCandidate


def test_context_sufficiency_checker_accepts_enough_candidates() -> None:
    checker = ContextSufficiencyChecker(Settings(app_env="test"))
    decision = checker.check(
        [_candidate(100, 0.7), _candidate(101, 0.6, logical_document_id=11)],
        selected_count=1,
        intent=QueryIntent.FACTUAL_LOOKUP,
    )

    assert decision.sufficient is True
    assert decision.score == 1.0
    assert "sufficient_context" in decision.reason_codes
    assert "content_text" not in str(decision.to_trace())


def test_context_sufficiency_checker_rejects_empty_low_score_and_comparison_diversity() -> None:
    checker = ContextSufficiencyChecker(Settings(app_env="test"))

    empty = checker.check([], selected_count=0, intent=QueryIntent.FACTUAL_LOOKUP)
    assert empty.sufficient is False
    assert "no_candidates" in empty.reason_codes

    low_score = checker.check([_candidate(100, 0.1)], selected_count=1, intent=None)
    assert low_score.sufficient is False
    assert "low_top_score" in low_score.reason_codes

    comparison = checker.check(
        [_candidate(100, 0.8), _candidate(101, 0.7)],
        selected_count=2,
        intent=QueryIntent.COMPARISON,
    )
    assert comparison.sufficient is False
    assert "insufficient_source_diversity_for_comparison" in comparison.reason_codes

    single_selected_comparison = checker.check(
        [_candidate(100, 0.8), _candidate(101, 0.7, logical_document_id=11)],
        selected_count=1,
        intent=QueryIntent.COMPARISON,
    )
    assert single_selected_comparison.sufficient is False
    assert "too_few_selected_candidates_for_comparison" in single_selected_comparison.reason_codes


def test_agentic_executor_fallback_merges_and_dedupes_candidates() -> None:
    settings = Settings(
        app_env="test",
        router_sufficiency_top_score_threshold=0.8,
        router_max_retrieval_calls=2,
        router_max_fallback_calls=1,
    )
    executor = AgenticRetrievalExecutor(settings)
    attempts = {
        RetrievalStrategy.DENSE: RetrievalAttemptResult(
            strategy=RetrievalStrategy.DENSE,
            candidates=[_candidate(100, 0.2)],
            qdrant_candidate_count=1,
            role="initial",
        ),
        RetrievalStrategy.HYBRID: RetrievalAttemptResult(
            strategy=RetrievalStrategy.HYBRID,
            candidates=[_candidate(100, 0.3), _candidate(101, 1.0, logical_document_id=11)],
            qdrant_candidate_count=2,
            sparse_candidate_count=2,
            hybrid_candidate_count=2,
            role="fallback",
        ),
    }

    result = executor.execute(
        query="alpha policy",
        initial_strategy=RetrievalStrategy.DENSE,
        intent=QueryIntent.FACTUAL_LOOKUP,
        top_k=5,
        rerank_top_n=1,
        retrieve=lambda strategy, role: attempts[strategy],
        latency_tracker=LatencyTracker(),
    )

    assert result.retrieval_call_count == 2
    assert result.fallback_used is True
    assert result.fallback_strategies == [RetrievalStrategy.HYBRID]
    assert result.final_candidates[0].chunk.document_chunk_id == 101
    assert {candidate.chunk.document_chunk_id for candidate in result.final_candidates} == {
        100,
        101,
    }
    assert result.deduped_candidate_count == 2
    assert result.no_context is False
    trace = result.decision_trace_fields()
    assert trace["retrieval_call_count"] == 2
    assert trace["fallback_reason"] == "insufficient_context"
    assert "content_text" not in str(trace)


def test_agentic_executor_respects_retrieval_budget() -> None:
    settings = Settings(
        app_env="test",
        router_max_retrieval_calls=1,
        router_max_fallback_calls=0,
        router_sufficiency_top_score_threshold=0.9,
    )
    executor = AgenticRetrievalExecutor(settings)

    result = executor.execute(
        query="alpha policy",
        initial_strategy=RetrievalStrategy.DENSE,
        intent=QueryIntent.FACTUAL_LOOKUP,
        top_k=5,
        rerank_top_n=1,
        retrieve=lambda strategy, role: RetrievalAttemptResult(
            strategy=strategy,
            candidates=[_candidate(100, 0.1)],
            qdrant_candidate_count=1,
            role=role,
        ),
        latency_tracker=LatencyTracker(),
    )

    assert result.retrieval_call_count == 1
    assert result.fallback_used is False
    assert result.budget_exhausted is True
    assert result.no_context is True


def test_agentic_executor_skips_dense_equivalent_fallback_after_dense_attempt() -> None:
    settings = Settings(
        app_env="test",
        router_sufficiency_top_score_threshold=0.9,
        router_max_retrieval_calls=2,
        router_max_fallback_calls=1,
        router_enable_fallback_hybrid=False,
        router_fallback_strategy="fallback_dense",
    )
    executor = AgenticRetrievalExecutor(settings)
    calls: list[RetrievalStrategy] = []

    def retrieve(strategy: RetrievalStrategy, role: str) -> RetrievalAttemptResult:
        calls.append(strategy)
        return RetrievalAttemptResult(
            strategy=strategy,
            candidates=[_candidate(100, 0.3)],
            qdrant_candidate_count=1,
            role=role,
        )

    result = executor.execute(
        query="alpha policy",
        initial_strategy=RetrievalStrategy.DENSE,
        intent=QueryIntent.FACTUAL_LOOKUP,
        top_k=5,
        rerank_top_n=1,
        retrieve=retrieve,
        latency_tracker=LatencyTracker(),
    )

    assert calls == [RetrievalStrategy.DENSE]
    assert result.retrieval_call_count == 1
    assert result.fallback_used is False
    assert result.budget_exhausted is True
    assert result.no_context is True


def test_agentic_executor_uses_llm_planner_for_fallback_strategy() -> None:
    settings = Settings(
        app_env="test",
        router_mode="llm",
        router_sufficiency_top_score_threshold=0.8,
        router_max_retrieval_calls=2,
        router_max_fallback_calls=1,
    )
    planner = _FakePlanner(
        AgenticPlannerResult(
            plan=AgenticStrategyPlan(
                action="retrieve",
                strategy=RetrievalStrategy.HYBRID,
                confidence=0.77,
                reason_codes=("planner_keyword_heavy",),
                provider="lmstudio",
                model="qwen3.5-4b",
            ),
            provider="lmstudio",
            model="qwen3.5-4b",
        )
    )
    executor = AgenticRetrievalExecutor(settings, planner=planner)
    attempts = {
        RetrievalStrategy.DENSE: RetrievalAttemptResult(
            strategy=RetrievalStrategy.DENSE,
            candidates=[_candidate(100, 0.2)],
            qdrant_candidate_count=1,
            role="initial",
        ),
        RetrievalStrategy.HYBRID: RetrievalAttemptResult(
            strategy=RetrievalStrategy.HYBRID,
            candidates=[_candidate(101, 0.9, logical_document_id=11)],
            qdrant_candidate_count=1,
            sparse_candidate_count=1,
            hybrid_candidate_count=1,
            role="fallback",
        ),
    }

    result = executor.execute(
        query="HTTP 500 API_ERROR",
        initial_strategy=RetrievalStrategy.DENSE,
        intent=QueryIntent.TROUBLESHOOTING,
        top_k=5,
        rerank_top_n=1,
        retrieve=lambda strategy, role: attempts[strategy],
        latency_tracker=LatencyTracker(),
    )

    assert planner.requests[0].phase == "fallback"
    assert planner.requests[0].available_strategies == (RetrievalStrategy.HYBRID,)
    assert result.fallback_strategies == [RetrievalStrategy.HYBRID]
    trace = result.decision_trace_fields()
    assert trace["llm_planner_used"] is True
    assert trace["planner_provider"] == "lmstudio"
    assert trace["planner_model"] == "qwen3.5-4b"
    assert trace["planner_selected_strategy"] == "hybrid"
    assert trace["planner_reason_codes"] == ["planner_keyword_heavy"]
    assert "content_text" not in str(trace)


def test_agentic_executor_rejects_unavailable_llm_strategy_and_uses_rule_fallback() -> None:
    settings = Settings(
        app_env="test",
        router_mode="llm",
        router_sufficiency_top_score_threshold=0.8,
        router_max_retrieval_calls=2,
        router_max_fallback_calls=1,
    )
    planner = _FakePlanner(
        AgenticPlannerResult(
            plan=AgenticStrategyPlan(
                action="retrieve",
                strategy=RetrievalStrategy.SPARSE,
                confidence=0.9,
                reason_codes=("planner_sparse",),
                provider="lmstudio",
                model="qwen3.5-4b",
            ),
            provider="lmstudio",
            model="qwen3.5-4b",
        )
    )
    executor = AgenticRetrievalExecutor(settings, planner=planner)
    calls: list[RetrievalStrategy] = []

    def retrieve(strategy: RetrievalStrategy, role: str) -> RetrievalAttemptResult:
        calls.append(strategy)
        score = 0.1 if strategy == RetrievalStrategy.DENSE else 0.9
        return RetrievalAttemptResult(
            strategy=strategy,
            candidates=[_candidate(100 if strategy == RetrievalStrategy.DENSE else 101, score)],
            qdrant_candidate_count=1,
            role=role,
        )

    result = executor.execute(
        query="alpha policy",
        initial_strategy=RetrievalStrategy.DENSE,
        intent=QueryIntent.FACTUAL_LOOKUP,
        top_k=5,
        rerank_top_n=1,
        retrieve=retrieve,
        latency_tracker=LatencyTracker(),
    )

    assert calls == [RetrievalStrategy.DENSE, RetrievalStrategy.HYBRID]
    assert result.fallback_strategies == [RetrievalStrategy.HYBRID]
    trace = result.decision_trace_fields()
    assert trace["llm_planner_used"] is False
    assert trace["planner_fallback_reason"] == "planner_strategy_unavailable"


def test_langchain_agentic_uses_llm_planner_for_initial_tool() -> None:
    from app.rag.langchain_agentic import LangChainAgenticRetrievalOrchestrator

    planner = _SequencedFakePlanner(
        _planner_result(
            action="retrieve",
            strategy=RetrievalStrategy.SPARSE,
            reason_codes=("planner_sparse",),
        ),
        _planner_result(action="finalize", strategy=None, reason_codes=("planner_finalize",)),
    )
    orchestrator = LangChainAgenticRetrievalOrchestrator(
        Settings(app_env="test", router_mode="llm"),
        planner=planner,
    )
    calls: list[RetrievalStrategy] = []

    def retrieve(
        strategy: RetrievalStrategy,
        role: str,
        tool_query: str,
    ) -> RetrievalAttemptResult:
        calls.append(strategy)
        return RetrievalAttemptResult(
            strategy=strategy,
            candidates=[_candidate(201, 0.9)],
            sparse_candidate_count=1 if strategy == RetrievalStrategy.SPARSE else None,
            role=role,
        )

    result = orchestrator.execute(
        query="alpha beta exact keyword",
        top_k=5,
        rerank_top_n=1,
        retrieve=retrieve,
        latency_tracker=LatencyTracker(),
    )

    assert calls == [RetrievalStrategy.SPARSE]
    assert planner.requests[0].phase == "initial"
    assert RetrievalStrategy.SPARSE in planner.requests[0].available_strategies
    query_analysis = planner.requests[0].query_analysis
    assert query_analysis is not None
    assert query_analysis["orchestrator_provider"] == "langchain"
    assert result.retrieval_result.initial_strategy == RetrievalStrategy.SPARSE
    trace = result.decision_trace_fields()
    assert trace["llm_planner_used"] is True
    assert trace["planner_model"] == "qwen3.5-4b"
    planner_events = trace["planner_events"]
    assert isinstance(planner_events, list)
    first_event = planner_events[0]
    assert isinstance(first_event, dict)
    assert first_event["planner_selected_strategy"] == "sparse"
    assert "content_text" not in str(trace)


def test_langchain_agentic_rejects_llm_finalize_without_useful_results() -> None:
    from app.rag.langchain_agentic import LangChainAgenticRetrievalOrchestrator

    planner = _SequencedFakePlanner(
        _planner_result(
            action="retrieve",
            strategy=RetrievalStrategy.HYBRID,
            reason_codes=("planner_hybrid",),
        ),
        _planner_result(action="finalize", strategy=None, reason_codes=("planner_finalize",)),
        _planner_result(action="finalize", strategy=None, reason_codes=("planner_finalize",)),
    )
    orchestrator = LangChainAgenticRetrievalOrchestrator(
        Settings(app_env="test", router_mode="llm"),
        planner=planner,
    )
    calls: list[RetrievalStrategy] = []

    def retrieve(
        strategy: RetrievalStrategy,
        role: str,
        tool_query: str,
    ) -> RetrievalAttemptResult:
        calls.append(strategy)
        return RetrievalAttemptResult(
            strategy=strategy,
            candidates=[] if strategy == RetrievalStrategy.HYBRID else [_candidate(203, 0.9)],
            sparse_candidate_count=1 if strategy == RetrievalStrategy.SPARSE else None,
            hybrid_candidate_count=0 if strategy == RetrievalStrategy.HYBRID else None,
            role=role,
        )

    result = orchestrator.execute(
        query="alpha beta gamma delta epsilon zeta keyword",
        top_k=5,
        rerank_top_n=1,
        retrieve=retrieve,
        latency_tracker=LatencyTracker(),
    )

    assert calls == [RetrievalStrategy.HYBRID, RetrievalStrategy.SPARSE]
    assert result.retrieval_result.fallback_used is False
    assert result.retrieval_result.initial_strategy == RetrievalStrategy.SPARSE
    assert result.retrieval_result.final_candidates
    trace = result.decision_trace_fields()
    planner_events = trace["planner_events"]
    assert isinstance(planner_events, list)
    rejected_event = planner_events[1]
    assert isinstance(rejected_event, dict)
    assert rejected_event["planner_fallback_reason"] == "planner_finalize_without_results"
    assert "content_text" not in str(trace)


def test_langgraph_agentic_uses_llm_planner_for_initial_tool() -> None:
    from app.rag.langgraph_agentic import LangGraphAgenticRetrievalOrchestrator

    planner = _SequencedFakePlanner(
        _planner_result(
            action="retrieve",
            strategy=RetrievalStrategy.SPARSE,
            reason_codes=("planner_sparse",),
        ),
        _planner_result(action="finalize", strategy=None, reason_codes=("planner_finalize",)),
    )
    orchestrator = LangGraphAgenticRetrievalOrchestrator(
        Settings(app_env="test", router_mode="llm"),
        planner=planner,
    )
    calls: list[RetrievalStrategy] = []

    def retrieve(
        strategy: RetrievalStrategy,
        role: str,
        tool_query: str,
    ) -> RetrievalAttemptResult:
        calls.append(strategy)
        return RetrievalAttemptResult(
            strategy=strategy,
            candidates=[_candidate(202, 0.9)],
            sparse_candidate_count=1 if strategy == RetrievalStrategy.SPARSE else None,
            role=role,
        )

    result = orchestrator.execute(
        query="alpha beta exact keyword",
        top_k=5,
        rerank_top_n=1,
        retrieve=retrieve,
        latency_tracker=LatencyTracker(),
    )

    assert calls == [RetrievalStrategy.SPARSE]
    assert planner.requests[0].phase == "initial"
    assert RetrievalStrategy.SPARSE in planner.requests[0].available_strategies
    query_analysis = planner.requests[0].query_analysis
    assert query_analysis is not None
    assert query_analysis["orchestrator_provider"] == "langgraph"
    assert result.retrieval_result.initial_strategy == RetrievalStrategy.SPARSE
    trace = result.decision_trace_fields()
    assert trace["llm_planner_used"] is True
    assert trace["planner_model"] == "qwen3.5-4b"
    planner_events = trace["planner_events"]
    assert isinstance(planner_events, list)
    first_event = planner_events[0]
    assert isinstance(first_event, dict)
    assert first_event["planner_selected_strategy"] == "sparse"
    assert "content_text" not in str(trace)


def _candidate(
    document_chunk_id: int,
    retrieval_score: float,
    *,
    logical_document_id: int = 10,
) -> CheckedRetrievalCandidate:
    now = datetime.now(UTC)
    logical_document = LogicalDocument(
        logical_document_id=logical_document_id,
        owner_user_id=1,
        title=f"Document {logical_document_id}",
        status="active",
    )
    document_version = DocumentVersion(
        document_version_id=logical_document_id,
        logical_document_id=logical_document_id,
        version_no=1,
        content_hash=f"{document_chunk_id:064x}",
        status="ready",
        is_active=True,
        file_name=f"{logical_document_id}.txt",
        mime_type="text/plain",
        file_size_bytes=10,
        created_by=1,
        created_at=now,
        updated_at=now,
    )
    chunk = DocumentChunk(
        document_chunk_id=document_chunk_id,
        document_version_id=document_version.document_version_id,
        chunk_index=0,
        chunk_hash=f"{document_chunk_id:064x}",
        content_text="raw text used only by reranker, not trace",
        token_count=10,
        char_count=10,
        modality="text",
    )
    return CheckedRetrievalCandidate(
        chunk=chunk,
        document_version=document_version,
        logical_document=logical_document,
        retrieval_score=retrieval_score,
        rank_order=1,
        payload={},
    )


class _FakePlanner:
    def __init__(self, result: AgenticPlannerResult) -> None:
        self.result = result
        self.requests: list[AgenticStrategyPlanningRequest] = []

    def plan(self, request: AgenticStrategyPlanningRequest) -> AgenticPlannerResult:
        self.requests.append(request)
        return self.result


class _SequencedFakePlanner:
    def __init__(self, *results: AgenticPlannerResult) -> None:
        self.results = list(results)
        self.requests: list[AgenticStrategyPlanningRequest] = []

    def plan(self, request: AgenticStrategyPlanningRequest) -> AgenticPlannerResult:
        self.requests.append(request)
        if len(self.requests) <= len(self.results):
            return self.results[len(self.requests) - 1]
        return self.results[-1]


def _planner_result(
    *,
    action: Literal["retrieve", "finalize"],
    strategy: RetrievalStrategy | None,
    reason_codes: tuple[str, ...],
) -> AgenticPlannerResult:
    return AgenticPlannerResult(
        plan=AgenticStrategyPlan(
            action=action,
            strategy=strategy,
            confidence=0.8,
            reason_codes=reason_codes,
            provider="lmstudio",
            model="qwen3.5-4b",
        ),
        provider="lmstudio",
        model="qwen3.5-4b",
    )


def test_langgraph_empty_first_search_counts_as_fallback() -> None:
    from app.rag.langgraph_agentic import LangGraphAgenticRetrievalOrchestrator

    settings = Settings(app_env="test")
    orchestrator = LangGraphAgenticRetrievalOrchestrator(settings)
    attempts = {
        RetrievalStrategy.HYBRID: RetrievalAttemptResult(
            strategy=RetrievalStrategy.HYBRID,
            candidates=[],
            qdrant_candidate_count=0,
            role="initial",
        ),
        RetrievalStrategy.DENSE: RetrievalAttemptResult(
            strategy=RetrievalStrategy.DENSE,
            candidates=[_candidate(100, 0.9)],
            qdrant_candidate_count=1,
            role="fallback",
        ),
    }

    result = orchestrator.execute(
        query="how does ingestion work",
        top_k=5,
        rerank_top_n=1,
        retrieve=lambda strategy, query, tool_name: attempts[strategy],
        latency_tracker=LatencyTracker(),
    )

    retrieval_result = result.retrieval_result
    # The empty first search is a real executed retrieval attempt: the answer
    # depended on an alternate path, so fallback metadata must reflect it.
    assert retrieval_result.retrieval_call_count == 2
    assert retrieval_result.initial_strategy == RetrievalStrategy.HYBRID
    assert retrieval_result.fallback_used is True
    assert retrieval_result.fallback_strategies == [RetrievalStrategy.DENSE]
    assert retrieval_result.fallback_reason == "langgraph_additional_search"
    assert [c.chunk.document_chunk_id for c in retrieval_result.final_candidates] == [100]
