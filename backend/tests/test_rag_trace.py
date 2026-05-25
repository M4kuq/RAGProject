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
from app.rag.trace import (
    LatencyTracker,
    TraceRedactor,
    build_default_dense_query_plan,
    build_default_dense_strategy_decision,
    build_dense_score_breakdown,
    build_retrieval_settings_snapshot,
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


def test_trace_redactor_removes_forbidden_fields_and_sensitive_values() -> None:
    redacted = TraceRedactor.safe_dict(
        {
            "safe_count": 1,
            "raw_prompt": "do not persist",
            "nested": {"api_key": "secret", "mode": "dense"},
            "provider": "fake",
            "operator": "person@example.com",
            "env_secret": "OPENAI_API_KEY=sk-test",
            "db_secret": "DATABASE_PASSWORD=hunter2",
            "internal_url": "http://qdrant:6333",
        }
    )

    assert redacted == {
        "safe_count": 1,
        "nested": {"mode": "dense"},
        "provider": "fake",
        "operator": "redacted",
        "env_secret": "redacted",
        "db_secret": "redacted",
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
