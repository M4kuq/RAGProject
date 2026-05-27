from __future__ import annotations

import json
from typing import cast

from app.core.config import Settings
from app.rag.query_planner import QueryPlanBuilder, QueryPlanBuildResult
from app.rag.retrieval import RetrievalFilters
from app.rag.router import StrategyRouter
from app.rag.strategy import RetrievalStrategy
from app.rag.trace import build_router_strategy_decision
from app.schemas.rag_strategy import RouterDecisionTrace


def test_strategy_router_disabled_uses_configured_dense_fallback() -> None:
    decision = _route(
        "alpha policy",
        settings=Settings(
            app_env="test",
            router_enabled=False,
            router_fallback_strategy="dense",
        ),
    )

    assert decision.selected_strategy == RetrievalStrategy.DENSE
    assert decision.execution_strategy == RetrievalStrategy.DENSE
    assert decision.fallback_used is True
    assert decision.fallback_reason == "router_disabled"
    assert "router_disabled" in decision.reason_codes
    assert "fallback_strategy:dense" in decision.reason_codes


def test_strategy_router_disabled_can_mark_fallback_dense() -> None:
    decision = _route(
        "alpha policy",
        settings=Settings(
            app_env="test",
            router_enabled=False,
            router_fallback_strategy="fallback_dense",
        ),
    )

    assert decision.selected_strategy == RetrievalStrategy.FALLBACK_DENSE
    assert decision.execution_strategy == RetrievalStrategy.FALLBACK_DENSE
    assert decision.fallback_used is True
    assert decision.fallback_reason == "router_disabled"
    assert "fallback_strategy:fallback_dense" in decision.reason_codes


def test_strategy_router_keyword_heavy_and_comparison_choose_hybrid() -> None:
    keyword_decision = _route("HTTP 500 API_ERROR SQL_ERROR")
    comparison_decision = _route("Compare dense vs sparse retrieval")

    assert keyword_decision.selected_strategy == RetrievalStrategy.HYBRID
    assert keyword_decision.execution_strategy == RetrievalStrategy.HYBRID
    assert keyword_decision.fallback_used is False
    assert "keyword_heavy" in keyword_decision.reason_codes
    assert comparison_decision.selected_strategy == RetrievalStrategy.HYBRID
    assert comparison_decision.execution_strategy == RetrievalStrategy.HYBRID
    assert "comparison_intent" in comparison_decision.reason_codes


def test_strategy_router_normal_query_chooses_dense() -> None:
    decision = _route("alpha policy overview")

    assert decision.selected_strategy == RetrievalStrategy.DENSE
    assert decision.execution_strategy == RetrievalStrategy.DENSE
    assert decision.reason_codes == [
        "planner_candidate:dense",
        "execution_strategy:dense",
    ]


def test_strategy_router_version_specific_falls_back_from_unimplemented_candidate() -> None:
    decision = _route("v2 changes for alpha policy")

    assert decision.selected_strategy == RetrievalStrategy.HYBRID
    assert decision.execution_strategy == RetrievalStrategy.HYBRID
    assert RetrievalStrategy.VERSION_AWARE in decision.disabled_candidates
    assert "version_specific" in decision.reason_codes
    assert decision.fallback_used is False


def test_strategy_router_unavailable_sparse_and_hybrid_choose_dense() -> None:
    decision = _route(
        "HTTP 500 API_ERROR /api/v1/rag/search",
        settings=Settings(app_env="test", sparse_enabled=False, hybrid_enabled=False),
    )

    assert decision.selected_strategy == RetrievalStrategy.DENSE
    assert decision.execution_strategy == RetrievalStrategy.DENSE
    assert "dense_available" in decision.reason_codes


def test_strategy_router_exception_uses_configured_fallback_without_raw_query() -> None:
    settings = Settings(app_env="test", router_fallback_strategy="dense")
    router = StrategyRouter(settings)
    decision = router.route(
        query_plan=cast(QueryPlanBuildResult, object()),
        requested_strategy=RetrievalStrategy.AGENTIC_ROUTER,
        request_kind="search",
    )

    assert decision.selected_strategy == RetrievalStrategy.DENSE
    assert decision.execution_strategy == RetrievalStrategy.DENSE
    assert decision.fallback_reason == "router_error"
    assert "fallback_strategy:dense" in decision.reason_codes
    dumped = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)
    assert "raw_prompt" not in dumped
    assert "raw_chunk" not in dumped
    assert "content_text" not in dumped
    assert "alpha policy" not in dumped


def test_strategy_router_can_disable_decision_trace_persistence() -> None:
    decision = _route(
        "alpha policy overview",
        settings=Settings(app_env="test", router_store_decision_trace=False),
    )

    assert decision.store_decision_trace is False
    assert build_router_strategy_decision(decision=decision) is None
    dumped = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)
    assert "store_decision_trace" not in dumped


def _route(
    query: str,
    *,
    settings: Settings | None = None,
) -> RouterDecisionTrace:
    settings = settings or Settings(app_env="test")
    built = QueryPlanBuilder(settings).build(
        query,
        filters=RetrievalFilters(),
        requested_strategy=RetrievalStrategy.AGENTIC_ROUTER,
    )
    return StrategyRouter(settings).route(
        query_plan=built,
        requested_strategy=RetrievalStrategy.AGENTIC_ROUTER,
        request_kind="search",
    )
