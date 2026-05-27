from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import Settings
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument
from app.rag.agentic import (
    AgenticRetrievalExecutor,
    ContextSufficiencyChecker,
    RetrievalAttemptResult,
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
