from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings
from app.rag.agentic_planner import (
    AgenticPlannerResult,
    AgenticStrategyPlanner,
    AgenticStrategyPlanningRequest,
    planner_trace_event,
    query_analysis_payload,
)
from app.rag.query_planner import QueryPlanBuildResult
from app.rag.strategy import QueryIntent, RetrievalStrategy
from app.schemas.rag_strategy import RouterDecisionTrace

ROUTER_SCHEMA_VERSION = "phase2.router.v1"
UNIMPLEMENTED_ROUTER_STRATEGIES = {
    RetrievalStrategy.MULTI_QUERY_DENSE,
    RetrievalStrategy.MULTI_QUERY_HYBRID,
    RetrievalStrategy.METADATA_FILTERED,
    RetrievalStrategy.VERSION_AWARE,
}


@dataclass(frozen=True)
class StrategyRouter:
    settings: Settings
    planner: AgenticStrategyPlanner | None = None

    def route(
        self,
        *,
        query_plan: QueryPlanBuildResult,
        requested_strategy: RetrievalStrategy,
        request_kind: Literal["search", "ask"],
    ) -> RouterDecisionTrace:
        try:
            return self._route(
                query_plan=query_plan,
                requested_strategy=requested_strategy,
                request_kind=request_kind,
            )
        except Exception:
            return self.fallback_decision(
                requested_strategy=requested_strategy,
                fallback_reason="router_error",
                reason_codes=["router_error"],
            )

    def fallback_decision(
        self,
        *,
        requested_strategy: RetrievalStrategy,
        fallback_reason: str,
        reason_codes: list[str],
    ) -> RouterDecisionTrace:
        fallback_strategy = _configured_fallback_strategy(self.settings)
        normalized_reason_codes = [code for code in reason_codes if code != "fallback_dense"]
        return RouterDecisionTrace(
            requested_strategy=requested_strategy,
            selected_strategy=fallback_strategy,
            execution_strategy=fallback_strategy,
            decision_source="fallback",
            fallback_used=True,
            fallback_reason=fallback_reason,
            router_enabled=False,
            confidence=0.0,
            reason_codes=[
                *normalized_reason_codes,
                f"fallback_strategy:{fallback_strategy.value}",
            ],
            safety_flags=[f"{fallback_strategy.value}_only", "single_retrieval_call"],
            store_decision_trace=self.settings.router_store_decision_trace,
        )

    def _route(
        self,
        *,
        query_plan: QueryPlanBuildResult,
        requested_strategy: RetrievalStrategy,
        request_kind: Literal["search", "ask"],
    ) -> RouterDecisionTrace:
        if requested_strategy != RetrievalStrategy.AGENTIC_ROUTER:
            return RouterDecisionTrace(
                requested_strategy=requested_strategy,
                selected_strategy=requested_strategy,
                execution_strategy=requested_strategy,
                decision_source="explicit_strategy",
                fallback_used=False,
                router_enabled=False,
                confidence=1.0,
                reason_codes=[f"explicit_strategy:{requested_strategy.value}"],
                store_decision_trace=self.settings.router_store_decision_trace,
            )

        if not self.settings.router_enabled:
            return self.fallback_decision(
                requested_strategy=requested_strategy,
                fallback_reason="router_disabled",
                reason_codes=["router_disabled"],
            )
        if request_kind == "search" and not self.settings.router_allow_agentic_search:
            return self.fallback_decision(
                requested_strategy=requested_strategy,
                fallback_reason="agentic_search_disabled",
                reason_codes=["agentic_search_disabled"],
            )
        if request_kind == "ask" and not self.settings.router_allow_agentic_ask:
            return self.fallback_decision(
                requested_strategy=requested_strategy,
                fallback_reason="agentic_ask_disabled",
                reason_codes=["agentic_ask_disabled"],
            )

        available = _available_strategies(self.settings)
        analysis = query_plan.analysis
        planner = query_plan.planner
        candidates = list(planner.candidate_strategies if planner else ())
        if not candidates and analysis is not None:
            candidates = list(analysis.recommended_candidate_strategies)

        selected: RetrievalStrategy
        reason_codes: list[str]
        confidence: float
        decision_source = "rule_based"
        llm_planner_used: bool | None = None
        planner_result: AgenticPlannerResult | None = None
        planner_fallback_reason: str | None = None
        planner_event: dict[str, object] | None = None

        if self.settings.router_mode == "llm":
            if self.planner is None:
                planner_fallback_reason = "planner_unavailable"
                planner_event = planner_trace_event(
                    phase="initial",
                    result=None,
                    used=False,
                    fallback_reason=planner_fallback_reason,
                )
            else:
                planner_result = self.planner.plan(
                    AgenticStrategyPlanningRequest(
                        query=query_plan.retrieval_query,
                        phase="initial",
                        available_strategies=tuple(available),
                        candidate_strategies=tuple(candidates),
                        query_analysis=query_analysis_payload(analysis),
                        remaining_retrieval_calls=self.settings.router_max_retrieval_calls,
                        remaining_fallback_calls=self.settings.router_max_fallback_calls,
                    )
                )
                plan = planner_result.plan
                if (
                    plan is not None
                    and plan.action == "retrieve"
                    and plan.strategy is not None
                    and _planner_strategy_allowed(plan.strategy, available)
                ):
                    selected = plan.strategy
                    reason_codes = [
                        "llm_planner_selected",
                        *plan.reason_codes,
                    ]
                    confidence = plan.confidence
                    decision_source = "llm_planner"
                    llm_planner_used = True
                    planner_event = planner_trace_event(
                        phase="initial",
                        result=planner_result,
                        used=True,
                        selected_strategy=selected,
                    )
                else:
                    planner_fallback_reason = (
                        "planner_finalize_not_allowed_initial"
                        if plan is not None and plan.action == "finalize"
                        else "planner_strategy_unavailable"
                        if plan is not None
                        and plan.strategy is not None
                        and plan.strategy not in available
                        else "planner_strategy_not_allowed"
                        if plan is not None and plan.strategy is not None
                        else planner_result.fallback_reason or "planner_failed"
                    )
                    planner_event = planner_trace_event(
                        phase="initial",
                        result=planner_result,
                        used=False,
                        fallback_reason=planner_fallback_reason,
                    )

        if llm_planner_used is not True:
            selected, reason_codes, confidence = _select_strategy(
                analysis=query_plan.analysis,
                candidate_strategies=candidates,
                settings=self.settings,
                available_strategies=available,
            )
            if planner_fallback_reason:
                reason_codes = [
                    *reason_codes,
                    "llm_planner_fallback",
                    f"planner_fallback:{planner_fallback_reason}",
                ]
                llm_planner_used = False
        disabled_candidates = _disabled_candidates(candidates, available)
        execution, fallback_used, fallback_reason, resolution_reasons = _resolve_execution_strategy(
            selected,
            available_strategies=available,
            fallback_strategy=_configured_fallback_strategy(self.settings),
        )
        planner_selected_strategy = selected if llm_planner_used else None
        planner_reason_codes = (
            list(planner_result.plan.reason_codes)
            if planner_result is not None and planner_result.plan is not None
            else []
        )
        return RouterDecisionTrace(
            requested_strategy=requested_strategy,
            selected_strategy=selected,
            execution_strategy=execution,
            decision_source=decision_source,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            router_enabled=True,
            confidence=confidence,
            reason_codes=reason_codes + resolution_reasons,
            disabled_candidates=disabled_candidates,
            safety_flags=["single_retrieval_call", "no_agentic_loop", "no_external_action"],
            llm_planner_used=llm_planner_used,
            planner_provider=planner_result.provider if planner_result is not None else None,
            planner_model=planner_result.model if planner_result is not None else None,
            planner_action=(
                planner_result.plan.action
                if planner_result is not None and planner_result.plan is not None
                else None
            ),
            planner_selected_strategy=planner_selected_strategy,
            planner_reason_codes=planner_reason_codes,
            planner_fallback_reason=planner_fallback_reason,
            planner_events=[planner_event] if planner_event is not None else [],
            store_decision_trace=self.settings.router_store_decision_trace,
        )


def _available_strategies(settings: Settings) -> set[RetrievalStrategy]:
    available = {RetrievalStrategy.DENSE, RetrievalStrategy.FALLBACK_DENSE}
    if settings.sparse_enabled:
        available.add(RetrievalStrategy.SPARSE)
    if settings.hybrid_enabled and (settings.hybrid_sparse_weight <= 0 or settings.sparse_enabled):
        available.add(RetrievalStrategy.HYBRID)
    return available


def _configured_fallback_strategy(settings: Settings) -> RetrievalStrategy:
    if settings.router_fallback_strategy == RetrievalStrategy.DENSE.value:
        return RetrievalStrategy.DENSE
    return RetrievalStrategy.FALLBACK_DENSE


def _select_strategy(
    *,
    analysis: object,
    candidate_strategies: Iterable[RetrievalStrategy],
    settings: Settings,
    available_strategies: set[RetrievalStrategy],
) -> tuple[RetrievalStrategy, list[str], float]:
    intent = getattr(analysis, "intent", QueryIntent.UNKNOWN)
    ambiguity_score = float(getattr(analysis, "ambiguity_score", 0.0) or 0.0)
    keyword_score = float(getattr(analysis, "keyword_heavy_score", 0.0) or 0.0)
    version_specific = bool(getattr(analysis, "version_specific_flag", False))
    candidate_list = list(candidate_strategies)

    if version_specific:
        return _choose_first_available(
            [RetrievalStrategy.VERSION_AWARE, RetrievalStrategy.HYBRID, RetrievalStrategy.DENSE],
            available_strategies=available_strategies,
            reason_code="version_specific",
            confidence=0.74,
        )
    if keyword_score >= settings.router_keyword_heavy_threshold:
        return _choose_first_available(
            [RetrievalStrategy.HYBRID, RetrievalStrategy.SPARSE, RetrievalStrategy.DENSE],
            available_strategies=available_strategies,
            reason_code="keyword_heavy",
            confidence=min(0.9, 0.55 + keyword_score / 2),
        )
    if intent == QueryIntent.COMPARISON:
        return _choose_first_available(
            [RetrievalStrategy.HYBRID, RetrievalStrategy.DENSE],
            available_strategies=available_strategies,
            reason_code="comparison_intent",
            confidence=0.72,
        )
    if ambiguity_score >= settings.router_ambiguity_threshold:
        return _choose_first_available(
            [RetrievalStrategy.HYBRID, _configured_fallback_strategy(settings)],
            available_strategies=available_strategies,
            reason_code="ambiguous_query",
            confidence=0.58,
        )
    for candidate in candidate_list:
        if candidate in UNIMPLEMENTED_ROUTER_STRATEGIES:
            continue
        if candidate == RetrievalStrategy.FALLBACK_DENSE:
            fallback_strategy = _configured_fallback_strategy(settings)
            if fallback_strategy in available_strategies:
                return (
                    fallback_strategy,
                    [
                        f"planner_candidate:{candidate.value}",
                        f"fallback_strategy:{fallback_strategy.value}",
                    ],
                    0.64,
                )
            continue
        if candidate in available_strategies:
            return candidate, [f"planner_candidate:{candidate.value}"], 0.64
    return RetrievalStrategy.DENSE, ["default_dense"], 0.6


def _choose_first_available(
    strategies: list[RetrievalStrategy],
    *,
    available_strategies: set[RetrievalStrategy],
    reason_code: str,
    confidence: float,
) -> tuple[RetrievalStrategy, list[str], float]:
    for strategy in strategies:
        if strategy in available_strategies:
            return strategy, [reason_code, f"{strategy.value}_available"], confidence
    return RetrievalStrategy.FALLBACK_DENSE, [reason_code, "no_candidate_available"], 0.0


def _resolve_execution_strategy(
    selected_strategy: RetrievalStrategy,
    *,
    available_strategies: set[RetrievalStrategy],
    fallback_strategy: RetrievalStrategy,
) -> tuple[RetrievalStrategy, bool, str | None, list[str]]:
    if selected_strategy in UNIMPLEMENTED_ROUTER_STRATEGIES:
        if RetrievalStrategy.HYBRID in available_strategies:
            return (
                RetrievalStrategy.HYBRID,
                True,
                "candidate_not_implemented",
                ["candidate_not_implemented", "hybrid_fallback"],
            )
        return (
            fallback_strategy,
            True,
            "candidate_not_implemented",
            ["candidate_not_implemented", f"fallback_strategy:{fallback_strategy.value}"],
        )
    if selected_strategy == RetrievalStrategy.FALLBACK_DENSE:
        return (
            selected_strategy,
            True,
            "fallback_dense_selected",
            ["fallback_dense_selected", "fallback_strategy:fallback_dense"],
        )
    if selected_strategy in available_strategies:
        return selected_strategy, False, None, [f"execution_strategy:{selected_strategy.value}"]
    return (
        fallback_strategy,
        True,
        "selected_strategy_unavailable",
        ["selected_strategy_unavailable", f"fallback_strategy:{fallback_strategy.value}"],
    )


def _planner_strategy_allowed(
    strategy: RetrievalStrategy,
    available_strategies: set[RetrievalStrategy],
) -> bool:
    return strategy not in UNIMPLEMENTED_ROUTER_STRATEGIES and strategy in available_strategies


def _disabled_candidates(
    candidate_strategies: Iterable[RetrievalStrategy],
    available_strategies: set[RetrievalStrategy],
) -> list[RetrievalStrategy]:
    disabled: list[RetrievalStrategy] = []
    seen: set[RetrievalStrategy] = set()
    for strategy in candidate_strategies:
        if strategy in seen or strategy in available_strategies:
            continue
        seen.add(strategy)
        disabled.append(strategy)
    return disabled
