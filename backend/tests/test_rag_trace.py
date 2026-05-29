from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.rag.retrieval import RetrievalFilters
from app.rag.strategy import FusionMethod, RetrievalStrategy
from app.rag.trace import (
    LatencyTracker,
    TraceRedactor,
    build_default_dense_query_plan,
    build_default_dense_strategy_decision,
    build_dense_score_breakdown,
    build_hybrid_query_plan,
    build_hybrid_score_breakdown,
    build_hybrid_strategy_decision,
    build_retrieval_settings_snapshot,
    build_sparse_query_plan,
    build_sparse_score_breakdown,
    build_sparse_strategy_decision,
)
from app.repositories.retrieval_repository import RetrievalRepository


def test_latency_tracker_records_non_negative_durations() -> None:
    clock = _Clock([0.0, 0.01, 0.04, 0.05])
    tracker = LatencyTracker(clock=clock)

    with tracker.span("query_embedding_ms"):
        pass

    snapshot = tracker.snapshot()
    assert snapshot["schema_version"] == "phase2.trace.v1"
    assert snapshot["query_embedding_ms"] == 30
    assert snapshot["retrieval_ms"] == 30
    assert snapshot["total_ms"] == 50
    assert all(value >= 0 for value in snapshot.values() if isinstance(value, int))


def test_latency_tracker_excludes_nested_agentic_parent_spans_from_retrieval_total() -> None:
    clock = _Clock([0.0, 0.1])
    tracker = LatencyTracker(clock=clock)
    tracker.record_ms("initial_retrieval_ms", 100)
    tracker.record_ms("fallback_retrieval_ms", 80)
    tracker.record_ms("query_embedding_ms", 10)
    tracker.record_ms("qdrant_search_ms", 20)
    tracker.record_ms("rdb_final_check_ms", 5)
    tracker.record_ms("sparse_search_ms", 15)
    tracker.record_ms("sufficiency_check_ms", 2)

    snapshot = tracker.snapshot()

    assert snapshot["initial_retrieval_ms"] == 100
    assert snapshot["fallback_retrieval_ms"] == 80
    assert snapshot["retrieval_ms"] == 52
    assert isinstance(snapshot["total_ms"], int)
    assert 52 <= snapshot["total_ms"]


def test_latency_tracker_excludes_nested_llm_orchestrator_spans_from_retrieval_total() -> None:
    clock = _Clock([0.0, 0.2])
    tracker = LatencyTracker(clock=clock)
    tracker.record_ms("llm_orchestrator_ms", 100)
    tracker.record_ms("llm_tool_planning_ms", 25)
    tracker.record_ms("llm_tool_execution_ms", 70)
    tracker.record_ms("query_embedding_ms", 10)
    tracker.record_ms("qdrant_search_ms", 20)
    tracker.record_ms("rdb_final_check_ms", 5)
    tracker.record_ms("retrieval_items_persist_ms", 7)

    snapshot = tracker.snapshot()

    assert snapshot["llm_orchestrator_ms"] == 100
    assert snapshot["llm_tool_planning_ms"] == 25
    assert snapshot["llm_tool_execution_ms"] == 70
    assert snapshot["retrieval_ms"] == 107
    assert isinstance(snapshot["total_ms"], int)
    assert 107 <= snapshot["total_ms"]


def test_trace_redactor_removes_forbidden_fields_and_sensitive_values() -> None:
    redacted = TraceRedactor.safe_dict(
        {
            "safe_count": 1,
            "raw_prompt": "do not persist",
            "nested": {
                "api_key": "secret",
                "apikey": "secret",
                "api-key": "secret",
                "csrf": "secret",
                "session_id": "secret",
                "cookie": "secret",
                "private_key": "secret",
                "mode": "dense",
            },
            "provider": "fake",
            "operator": "person@example.com",
            "env_assignment": "OPENAI_API_KEY=sk-test",
            "db_assignment": "DATABASE_PASSWORD=hunter2",
            "internal_url": "http://qdrant:6333",
        }
    )

    assert redacted == {
        "safe_count": 1,
        "nested": {"mode": "dense"},
        "provider": "fake",
        "operator": "redacted",
        "env_assignment": "redacted",
        "db_assignment": "redacted",
        "internal_url": "redacted",
    }


def test_default_dense_query_plan_has_hash_without_raw_query() -> None:
    raw_query = "alpha policy secret-token person@example.com"
    query_hash = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()
    plan = build_default_dense_query_plan(
        query_hash=query_hash,
        filters=RetrievalFilters(logical_document_ids=(1, 2), modality="text"),
    )

    dumped = json.dumps(plan)
    assert plan["schema_version"] == "phase2.trace.v1"
    assert plan["strategy_type"] == "dense"
    assert plan["query_hash"] == query_hash
    assert plan["metadata_filter_applied"] is True
    assert plan["logical_document_filter_count"] == 2
    assert raw_query not in dumped
    assert "secret-token" not in dumped
    assert "person@example.com" not in dumped


def test_default_dense_strategy_decision_has_no_prompt_or_context() -> None:
    decision = build_default_dense_strategy_decision()
    dumped = json.dumps(decision)

    assert decision["selected_strategy"] == "dense"
    assert decision["decision_source"] == "default"
    assert decision["router_enabled"] is False
    assert decision["fallback_used"] is False
    assert "prompt" not in dumped
    assert "context" not in dumped


def test_sparse_trace_builders_keep_only_safe_metadata() -> None:
    raw_query = "alpha secondary OPENAI_API_KEY=sk-test"
    query_hash = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()

    plan = build_sparse_query_plan(
        query_hash=query_hash,
        filters=RetrievalFilters(logical_document_ids=(10,), modality="text"),
        normalized_term_count=3,
    )
    decision = build_sparse_strategy_decision()
    score = build_sparse_score_breakdown(
        sparse_score=0.8123456,
        rank_order=1,
        final_rank=1,
        selected_flag=True,
    )
    dumped = json.dumps({"plan": plan, "decision": decision, "score": score})

    assert plan["strategy_type"] == "sparse"
    assert plan["query_hash"] == query_hash
    assert plan["metadata_filter_applied"] is True
    assert plan["reason_codes"] == ["phase2_sparse_lexical", "normalized_terms:3"]
    assert decision["selected_strategy"] == "sparse"
    assert decision["decision_source"] == "request"
    assert score["retrieval_source"] == "sparse"
    assert score["sparse_score"] == 0.812346
    assert raw_query not in dumped
    assert "sk-test" not in dumped
    assert "content_text" not in dumped


def test_hybrid_trace_builders_keep_only_safe_metadata() -> None:
    raw_query = "alpha secondary DATABASE_PASSWORD=hunter2"
    query_hash = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()

    plan = build_hybrid_query_plan(
        query_hash=query_hash,
        filters=RetrievalFilters(logical_document_ids=(10,), modality="text"),
        normalized_term_count=2,
        fusion_method=FusionMethod.RRF,
    )
    decision = build_hybrid_strategy_decision(fusion_method=FusionMethod.RRF)
    score = build_hybrid_score_breakdown(
        dense_score=0.91,
        sparse_score=0.82,
        fused_score=0.95,
        rank_order=1,
        final_rank=1,
        selected_flag=True,
        fusion_method=FusionMethod.RRF,
        dense_rank=1,
        sparse_rank=2,
    )
    dumped = json.dumps({"plan": plan, "decision": decision, "score": score})

    assert plan["strategy_type"] == "hybrid"
    assert plan["query_hash"] == query_hash
    assert plan["query_mode"] == "dense_sparse_single_query"
    assert plan["reason_codes"] == [
        "phase2_hybrid_dense_sparse",
        "fusion_method:rrf",
        "normalized_terms:2",
    ]
    assert decision["selected_strategy"] == "hybrid"
    assert decision["decision_policy"] == "explicit_hybrid_rrf"
    assert score["retrieval_source"] == "hybrid"
    assert score["dense_score"] == 0.91
    assert score["sparse_score"] == 0.82
    assert score["fused_score"] == 0.95
    assert score["fusion_method"] == "rrf"
    assert raw_query not in dumped
    assert "hunter2" not in dumped
    assert "content_text" not in dumped


def test_retrieval_settings_snapshot_has_safe_provider_metadata_only() -> None:
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        rerank_provider="fake",
        generation_provider="fake",
        qdrant_collection_name="http://qdrant:6333",
    )

    snapshot = build_retrieval_settings_snapshot(
        settings=settings,
        top_k=5,
        rerank_top_n=2,
        filters=RetrievalFilters(),
    )

    assert snapshot["schema_version"] == "phase2.trace.v1"
    assert snapshot["strategy_type"] == "dense"
    assert snapshot["embedding_provider"] == "fake"
    assert snapshot["rerank_provider"] == "fake"
    assert snapshot["generation_provider"] == "fake"
    assert snapshot["qdrant_collection"] == "redacted"
    assert "password" not in json.dumps(snapshot).lower()

    sparse_snapshot = build_retrieval_settings_snapshot(
        settings=settings,
        top_k=5,
        rerank_top_n=2,
        filters=RetrievalFilters(),
        strategy_type=RetrievalStrategy.SPARSE,
    )
    assert sparse_snapshot["strategy_type"] == "sparse"
    assert sparse_snapshot["sparse_provider"] == "postgres_fts"
    assert sparse_snapshot["sparse_language"] == "simple"
    assert sparse_snapshot["sparse_score_normalization"] == "max"

    hybrid_snapshot = build_retrieval_settings_snapshot(
        settings=settings,
        top_k=5,
        rerank_top_n=2,
        filters=RetrievalFilters(),
        strategy_type=RetrievalStrategy.HYBRID,
    )
    assert hybrid_snapshot["strategy_type"] == "hybrid"
    assert hybrid_snapshot["hybrid_enabled"] is True
    assert hybrid_snapshot["fusion_method"] == "rrf"
    assert hybrid_snapshot["hybrid_rrf_k"] == 60
    assert hybrid_snapshot["sparse_provider"] == "postgres_fts"


def test_score_breakdown_has_scores_without_chunk_text() -> None:
    breakdown = build_dense_score_breakdown(
        dense_score=0.8123456,
        rank_order=1,
        rerank_score=0.9,
        rerank_order=1,
        final_rank=1,
        selected_flag=True,
    )

    assert breakdown == {
        "schema_version": "phase2.trace.v1",
        "retrieval_source": "dense",
        "dense_score": 0.812346,
        "rerank_score": 0.9,
        "rank_order": 1,
        "rerank_order": 1,
        "final_rank": 1,
        "selected_flag": True,
    }
    assert "chunk" not in json.dumps(breakdown).lower()
    assert "text" not in json.dumps(breakdown).lower()


def test_repository_updates_retrieval_run_trace_fields() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        Base.metadata.create_all(engine)
        repository = RetrievalRepository()
        query_hash = "a" * 64
        with Session() as db:
            run = repository.create_standalone_run(
                db,
                top_k=5,
                query_hash=query_hash,
                request_id="trace-test",
                started_at=datetime.now(UTC),
            )
            repository.update_retrieval_run_trace(
                db,
                run=run,
                query_plan_json={"schema_version": "phase2.trace.v1", "query_hash": query_hash},
                strategy_decision_json={"selected_strategy": "dense"},
                latency_breakdown_json={"total_ms": 10},
                retrieval_settings_json={"strategy_type": "dense"},
            )
            db.commit()

        with Session() as db:
            stored = repository.get_run(db, retrieval_run_id=1)
            assert stored is not None
            assert stored.query_plan_json == {
                "schema_version": "phase2.trace.v1",
                "query_hash": query_hash,
            }
            assert stored.strategy_decision_json == {"selected_strategy": "dense"}
            assert stored.latency_breakdown_json == {"total_ms": 10}
            assert stored.retrieval_settings_json == {"strategy_type": "dense"}
    finally:
        engine.dispose()


class _Clock:
    def __init__(self, values: list[float]) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)
