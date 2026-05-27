from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.rag.strategy import QueryIntent, RetrievalStrategy
from app.rag.trace import LatencyTracker, TraceRedactor
from app.repositories.retrieval_repository import CheckedRetrievalCandidate

AGENTIC_LOOP_SCHEMA_VERSION = "phase2.agentic_loop.v1"
MIN_RETRIEVAL_CALLS = 1
MAX_RETRIEVAL_CALLS = 3


@dataclass(frozen=True)
class RetrievalAttemptResult:
    strategy: RetrievalStrategy
    candidates: list[CheckedRetrievalCandidate]
    qdrant_candidate_count: int = 0
    sparse_candidate_count: int | None = None
    hybrid_candidate_count: int | None = None
    excluded_by_rdb_check_count: int = 0
    role: str = "initial"

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


@dataclass(frozen=True)
class ContextSufficiencyDecision:
    sufficient: bool
    score: float
    reason_codes: list[str]
    candidate_count: int
    selected_count: int
    top_score: float | None
    min_required_candidates: int
    min_required_selected: int
    fallback_recommended: bool
    source_diversity: int

    def to_trace(self) -> dict[str, object]:
        return TraceRedactor.safe_dict(
            {
                "schema_version": AGENTIC_LOOP_SCHEMA_VERSION,
                "sufficient": self.sufficient,
                "score": round(self.score, 6),
                "reason_codes": list(self.reason_codes),
                "candidate_count": self.candidate_count,
                "selected_count": self.selected_count,
                "top_score": (
                    round(float(self.top_score), 6) if self.top_score is not None else None
                ),
                "min_required_candidates": self.min_required_candidates,
                "min_required_selected": self.min_required_selected,
                "fallback_recommended": self.fallback_recommended,
                "source_diversity": self.source_diversity,
            }
        )


@dataclass(frozen=True)
class AgenticRetrievalResult:
    final_candidates: list[CheckedRetrievalCandidate]
    retrieval_call_count: int
    initial_strategy: RetrievalStrategy
    fallback_strategies: list[RetrievalStrategy] = field(default_factory=list)
    fallback_used: bool = False
    fallback_reason: str | None = None
    sufficiency_decisions: list[ContextSufficiencyDecision] = field(default_factory=list)
    merged_candidate_count: int = 0
    deduped_candidate_count: int = 0
    final_selected_count: int = 0
    no_context: bool = False
    budget_exhausted: bool = False
    qdrant_candidate_count: int = 0
    sparse_candidate_count: int | None = None
    hybrid_candidate_count: int | None = None
    excluded_by_rdb_check_count: int = 0

    @property
    def final_decision(self) -> ContextSufficiencyDecision | None:
        return self.sufficiency_decisions[-1] if self.sufficiency_decisions else None

    def decision_trace_fields(self) -> dict[str, object]:
        final_decision = self.final_decision
        return TraceRedactor.safe_dict(
            {
                "agentic_schema_version": AGENTIC_LOOP_SCHEMA_VERSION,
                "agentic_fallback_used": self.fallback_used,
                "fallback_used": self.fallback_used,
                "fallback_strategy": (
                    self.fallback_strategies[-1].value if self.fallback_strategies else None
                ),
                "fallback_strategies": [strategy.value for strategy in self.fallback_strategies],
                "fallback_reason": self.fallback_reason,
                "retrieval_call_count": self.retrieval_call_count,
                "budget_exhausted": self.budget_exhausted,
                "sufficiency_score": (
                    round(final_decision.score, 6) if final_decision is not None else None
                ),
                "sufficiency_reason_codes": (
                    list(final_decision.reason_codes) if final_decision is not None else []
                ),
                "sufficiency_decisions": [
                    decision.to_trace() for decision in self.sufficiency_decisions
                ],
                "initial_candidate_count": (
                    self.sufficiency_decisions[0].candidate_count
                    if self.sufficiency_decisions
                    else 0
                ),
                "merged_candidate_count": self.merged_candidate_count,
                "deduped_candidate_count": self.deduped_candidate_count,
                "final_selected_count": self.final_selected_count,
                "no_context": self.no_context,
            }
        )

    def summary_fields(self) -> dict[str, object]:
        final_decision = self.final_decision
        return TraceRedactor.safe_dict(
            {
                "agentic_schema_version": AGENTIC_LOOP_SCHEMA_VERSION,
                "retrieval_call_count": self.retrieval_call_count,
                "fallback_used": self.fallback_used,
                "fallback_strategy": (
                    self.fallback_strategies[-1].value if self.fallback_strategies else None
                ),
                "fallback_reason": self.fallback_reason,
                "budget_exhausted": self.budget_exhausted,
                "sufficiency_score": (
                    round(final_decision.score, 6) if final_decision is not None else None
                ),
                "sufficiency_reason_codes": (
                    list(final_decision.reason_codes) if final_decision is not None else []
                ),
                "merged_candidate_count": self.merged_candidate_count,
                "deduped_candidate_count": self.deduped_candidate_count,
                "final_selected_count": self.final_selected_count,
                "no_context": self.no_context,
            }
        )


class ContextSufficiencyChecker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def check(
        self,
        candidates: list[CheckedRetrievalCandidate],
        *,
        selected_count: int,
        intent: QueryIntent | None,
    ) -> ContextSufficiencyDecision:
        candidate_count = len(candidates)
        min_candidates = self.settings.router_sufficiency_min_candidates
        min_selected = self.settings.router_sufficiency_min_selected
        top_score = _top_score(candidates)
        selected_candidates = candidates[:selected_count]
        source_diversity = _source_diversity(selected_candidates)
        reason_codes: list[str] = []
        sufficient = True

        if candidate_count == 0:
            sufficient = False
            reason_codes.append("no_candidates")
        if candidate_count < min_candidates:
            sufficient = False
            reason_codes.append("too_few_candidates")
        if selected_count < min_selected:
            sufficient = False
            reason_codes.append("too_few_selected_candidates")
        if top_score is None:
            sufficient = False
            reason_codes.append("missing_top_score")
        elif top_score < self.settings.router_sufficiency_top_score_threshold:
            sufficient = False
            reason_codes.append("low_top_score")
        if intent == QueryIntent.COMPARISON and selected_count < 2:
            sufficient = False
            reason_codes.append("too_few_selected_candidates_for_comparison")
        if intent == QueryIntent.COMPARISON and source_diversity < 2:
            sufficient = False
            reason_codes.append("insufficient_source_diversity_for_comparison")

        if sufficient:
            reason_codes.append("sufficient_context")

        score = _sufficiency_score(
            candidate_count=candidate_count,
            selected_count=selected_count,
            top_score=top_score,
            min_candidates=min_candidates,
            min_selected=min_selected,
            threshold=self.settings.router_sufficiency_top_score_threshold,
            source_diversity=source_diversity,
            comparison_intent=intent == QueryIntent.COMPARISON,
        )
        return ContextSufficiencyDecision(
            sufficient=sufficient,
            score=score,
            reason_codes=reason_codes,
            candidate_count=candidate_count,
            selected_count=selected_count,
            top_score=top_score,
            min_required_candidates=min_candidates,
            min_required_selected=min_selected,
            fallback_recommended=not sufficient,
            source_diversity=source_diversity,
        )


class AgenticRetrievalExecutor:
    def __init__(
        self,
        settings: Settings,
        checker: ContextSufficiencyChecker | None = None,
    ) -> None:
        self.settings = settings
        self.checker = checker or ContextSufficiencyChecker(settings)

    def execute(
        self,
        *,
        initial_strategy: RetrievalStrategy,
        intent: QueryIntent | None,
        top_k: int,
        rerank_top_n: int,
        retrieve: Callable[[RetrievalStrategy, str], RetrievalAttemptResult],
        latency_tracker: LatencyTracker,
    ) -> AgenticRetrievalResult:
        max_calls = _bounded_max_calls(self.settings.router_max_retrieval_calls)
        max_fallback_calls = min(
            self.settings.router_max_fallback_calls,
            max(0, max_calls - 1),
        )

        with latency_tracker.span("initial_retrieval_ms"):
            initial_attempt = retrieve(initial_strategy, "initial")
        attempts = [initial_attempt]

        with latency_tracker.span("sufficiency_check_ms"):
            initial_decision = self.checker.check(
                initial_attempt.candidates,
                selected_count=min(rerank_top_n, len(initial_attempt.candidates)),
                intent=intent,
            )
        decisions = [initial_decision]

        if initial_decision.sufficient or max_calls <= 1 or max_fallback_calls <= 0:
            budget_exhausted = not initial_decision.sufficient and (
                max_calls <= 1 or max_fallback_calls <= 0
            )
            return self._result_from_attempts(
                attempts,
                decisions=decisions,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                fallback_reason=None,
                budget_exhausted=budget_exhausted,
            )

        fallback_reason = "insufficient_context"
        fallback_calls = 0
        for fallback_strategy in self._fallback_strategies(initial_strategy):
            if fallback_calls >= max_fallback_calls or len(attempts) >= max_calls:
                break
            if fallback_strategy == initial_strategy:
                continue
            with latency_tracker.span("fallback_retrieval_ms"):
                fallback_attempt = retrieve(fallback_strategy, "fallback")
            attempts.append(fallback_attempt)
            fallback_calls += 1
            with latency_tracker.span("merge_dedupe_ms"):
                merged_candidates = merge_dedupe_candidates(attempts, limit=top_k)
            with latency_tracker.span("sufficiency_check_ms"):
                decision = self.checker.check(
                    merged_candidates,
                    selected_count=min(rerank_top_n, len(merged_candidates)),
                    intent=intent,
                )
            decisions.append(decision)
            if decision.sufficient:
                return self._result_from_attempts(
                    attempts,
                    decisions=decisions,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    fallback_reason=fallback_reason,
                    budget_exhausted=False,
                )

        return self._result_from_attempts(
            attempts,
            decisions=decisions,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            fallback_reason=fallback_reason,
            budget_exhausted=True,
        )

    def _fallback_strategies(self, initial_strategy: RetrievalStrategy) -> list[RetrievalStrategy]:
        fallback_dense = _configured_fallback_strategy(self.settings)
        strategies: list[RetrievalStrategy]
        if initial_strategy in {RetrievalStrategy.DENSE, RetrievalStrategy.FALLBACK_DENSE}:
            strategies = [RetrievalStrategy.HYBRID, fallback_dense]
        elif initial_strategy == RetrievalStrategy.SPARSE:
            strategies = [RetrievalStrategy.HYBRID, fallback_dense]
        else:
            strategies = [fallback_dense]
        return [
            strategy
            for strategy in strategies
            if _strategy_available(strategy, self.settings)
            and _execution_family(strategy) != _execution_family(initial_strategy)
        ]

    def _result_from_attempts(
        self,
        attempts: list[RetrievalAttemptResult],
        *,
        decisions: list[ContextSufficiencyDecision],
        top_k: int,
        rerank_top_n: int,
        fallback_reason: str | None,
        budget_exhausted: bool,
    ) -> AgenticRetrievalResult:
        final_candidates = merge_dedupe_candidates(attempts, limit=top_k)
        fallback_attempts = attempts[1:]
        final_decision = decisions[-1] if decisions else None
        no_context = (
            bool(final_decision and not final_decision.sufficient)
            and self.settings.router_no_context_after_budget_exhausted
        )
        return AgenticRetrievalResult(
            final_candidates=final_candidates,
            retrieval_call_count=len(attempts),
            initial_strategy=attempts[0].strategy,
            fallback_strategies=[attempt.strategy for attempt in fallback_attempts],
            fallback_used=bool(fallback_attempts),
            fallback_reason=fallback_reason if fallback_attempts else None,
            sufficiency_decisions=decisions,
            merged_candidate_count=sum(len(attempt.candidates) for attempt in attempts),
            deduped_candidate_count=len(final_candidates),
            final_selected_count=min(rerank_top_n, len(final_candidates)),
            no_context=no_context,
            budget_exhausted=budget_exhausted,
            qdrant_candidate_count=sum(attempt.qdrant_candidate_count for attempt in attempts),
            sparse_candidate_count=_sum_optional(
                attempt.sparse_candidate_count for attempt in attempts
            ),
            hybrid_candidate_count=_sum_optional(
                attempt.hybrid_candidate_count for attempt in attempts
            ),
            excluded_by_rdb_check_count=sum(
                attempt.excluded_by_rdb_check_count for attempt in attempts
            ),
        )


def merge_dedupe_candidates(
    attempts: list[RetrievalAttemptResult],
    *,
    limit: int,
) -> list[CheckedRetrievalCandidate]:
    by_chunk_id: dict[int, tuple[CheckedRetrievalCandidate, dict[str, object], set[str]]] = {}
    for attempt in attempts:
        source_name = f"{attempt.role}_{attempt.strategy.value}"
        for candidate in attempt.candidates:
            chunk_id = candidate.chunk.document_chunk_id
            existing = by_chunk_id.get(chunk_id)
            if existing is None:
                payload = dict(candidate.payload)
                payload["agentic_primary_source"] = _item_source_for_strategy(attempt.strategy)
                payload["agentic_sources"] = [source_name]
                payload["agentic_initial_strategy"] = attempts[0].strategy.value
                if attempt.role == "fallback":
                    payload["agentic_fallback_strategy"] = attempt.strategy.value
                by_chunk_id[chunk_id] = (candidate, payload, {source_name})
                continue

            existing_candidate, existing_payload, sources = existing
            sources.add(source_name)
            merged_payload = _merge_payloads(existing_payload, candidate.payload)
            merged_payload["agentic_sources"] = sorted(sources)
            if attempt.role == "fallback":
                merged_payload["agentic_fallback_strategy"] = attempt.strategy.value
            primary = _prefer_candidate(existing_candidate, candidate)
            if primary is candidate:
                merged_payload["agentic_primary_source"] = _item_source_for_strategy(
                    attempt.strategy
                )
            by_chunk_id[chunk_id] = (primary, merged_payload, sources)

    ranked = sorted(
        by_chunk_id.values(),
        key=lambda item: (
            -float(item[0].retrieval_score),
            item[0].chunk.document_chunk_id,
        ),
    )[:limit]
    return [
        CheckedRetrievalCandidate(
            chunk=candidate.chunk,
            document_version=candidate.document_version,
            logical_document=candidate.logical_document,
            retrieval_score=candidate.retrieval_score,
            rank_order=index,
            payload=payload,
        )
        for index, (candidate, payload, _) in enumerate(ranked, start=1)
    ]


def _prefer_candidate(
    left: CheckedRetrievalCandidate,
    right: CheckedRetrievalCandidate,
) -> CheckedRetrievalCandidate:
    if right.retrieval_score > left.retrieval_score:
        return right
    if right.retrieval_score == left.retrieval_score:
        return right if right.chunk.document_chunk_id < left.chunk.document_chunk_id else left
    return left


def _merge_payloads(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    merged = dict(left)
    for key, value in right.items():
        if key in {"dense_score", "sparse_score", "fused_score"}:
            merged[key] = _max_numeric(merged.get(key), value)
        elif key not in merged or merged[key] is None:
            merged[key] = value
    return merged


def _max_numeric(left: object, right: object) -> object:
    left_float = _optional_float(left)
    right_float = _optional_float(right)
    if left_float is None:
        return right
    if right_float is None:
        return left
    return max(left_float, right_float)


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _top_score(candidates: list[CheckedRetrievalCandidate]) -> float | None:
    if not candidates:
        return None
    return max(float(candidate.retrieval_score) for candidate in candidates)


def _source_diversity(candidates: list[CheckedRetrievalCandidate]) -> int:
    return len({candidate.logical_document.logical_document_id for candidate in candidates})


def _sufficiency_score(
    *,
    candidate_count: int,
    selected_count: int,
    top_score: float | None,
    min_candidates: int,
    min_selected: int,
    threshold: float,
    source_diversity: int,
    comparison_intent: bool,
) -> float:
    score = 0.0
    if candidate_count >= min_candidates:
        score += 0.35
    if selected_count >= min_selected:
        score += 0.25
    if top_score is not None and top_score >= threshold:
        score += 0.3
    if not comparison_intent or source_diversity >= 2:
        score += 0.1
    return round(min(1.0, score), 6)


def _bounded_max_calls(value: int) -> int:
    return min(MAX_RETRIEVAL_CALLS, max(MIN_RETRIEVAL_CALLS, int(value)))


def _configured_fallback_strategy(settings: Settings) -> RetrievalStrategy:
    if settings.router_fallback_strategy == RetrievalStrategy.DENSE.value:
        return RetrievalStrategy.DENSE
    return RetrievalStrategy.FALLBACK_DENSE


def _strategy_available(strategy: RetrievalStrategy, settings: Settings) -> bool:
    if strategy in {RetrievalStrategy.DENSE, RetrievalStrategy.FALLBACK_DENSE}:
        return bool(settings.router_enable_fallback_dense)
    if strategy == RetrievalStrategy.SPARSE:
        return bool(settings.sparse_enabled)
    if strategy == RetrievalStrategy.HYBRID:
        if not settings.router_enable_fallback_hybrid or not settings.hybrid_enabled:
            return False
        return settings.hybrid_sparse_weight <= 0 or settings.sparse_enabled
    return False


def _execution_family(strategy: RetrievalStrategy) -> str:
    if strategy in {RetrievalStrategy.DENSE, RetrievalStrategy.FALLBACK_DENSE}:
        return RetrievalStrategy.DENSE.value
    return strategy.value


def _item_source_for_strategy(strategy: RetrievalStrategy) -> str:
    if strategy == RetrievalStrategy.FALLBACK_DENSE:
        return RetrievalStrategy.FALLBACK_DENSE.value
    return strategy.value


def _sum_optional(values: Any) -> int | None:
    total = 0
    seen = False
    for value in values:
        if value is None:
            continue
        total += int(value)
        seen = True
    return total if seen else None
