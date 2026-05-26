from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routers.rag import rag_search_service
from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import (
    ChatMessage,
    Citation,
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    Role,
    User,
)
from app.db.session import get_db
from app.ingest.embedding import FakeEmbeddingAdapter
from app.ingest.qdrant import InMemoryQdrantClient, QdrantCollectionConfig, QdrantPoint
from app.main import create_app
from app.rag.generation import (
    AnswerGenerationError,
    FakeAnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    GenerationResult,
)
from app.rag.rerank import (
    FakeRerankerClient,
    RerankCandidate,
    RerankError,
    RerankResult,
    normalize_rerank_score,
)
from app.rag.retrieval import (
    InMemoryVectorSearchClient,
    RetrievalError,
    RetrievalFilters,
    VectorSearchCandidate,
)
from app.rag.sparse import SparseRetrievalStrategy, normalize_sparse_query, normalize_sparse_scores
from app.services.rag_service import RagService

ALLOWED_ORIGIN = "http://localhost:5173"
TEST_PASSWORD = "password"


@pytest.fixture
def rag_client() -> Iterator[tuple[TestClient, sessionmaker[Session], _StaticVectorClient]]:
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_auth(db)
        _seed_documents(db)
        db.commit()

    vector_client = _StaticVectorClient(
        [
            _candidate(100, 0.91, 1),
            _candidate(101, 0.82, 2),
            _candidate(200, 0.95, 3),
            _candidate(300, 0.94, 4),
            _candidate(400, 0.93, 5),
        ]
    )
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        search_snippet_max_chars=32,
    )
    service = RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
    )

    def override_db() -> Iterator[Session]:
        with session_factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[rag_search_service] = lambda: service
    try:
        yield TestClient(app), session_factory, vector_client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        engine.dispose()


def test_fake_reranker_is_deterministic_and_normalized() -> None:
    reranker = FakeRerankerClient()
    candidates = [
        RerankCandidate(document_chunk_id=1, text="alpha policy", retrieval_score=0.5),
        RerankCandidate(document_chunk_id=2, text="beta", retrieval_score=0.9),
    ]

    first = reranker.rerank(query="alpha", candidates=candidates)
    second = reranker.rerank(query="alpha", candidates=candidates)

    assert first == second
    assert [result.rerank_order for result in first] == [1, 2]
    assert all(0.0 <= result.rerank_score <= 1.0 for result in first)
    assert normalize_rerank_score(15.0, score_min=10.0, score_max=20.0) == 0.5
    assert normalize_rerank_score(25.0, score_min=10.0, score_max=20.0) == 1.0
    assert normalize_rerank_score(5.0, score_min=10.0, score_max=20.0) == 0.0


def test_fake_answer_generator_is_deterministic_and_redacts_context_text() -> None:
    generator = FakeAnswerGenerator()
    request = GenerationRequest(
        message="alpha policy",
        context_items=[
            GenerationContextItem(
                document_chunk_id=100,
                source_label="policy.md",
                text="raw context text that should not be echoed",
                page_from=1,
                page_to=1,
            )
        ],
        max_output_chars=200,
    )

    first = generator.generate(request)
    second = generator.generate(request)

    assert first == second
    assert "raw context text" not in first.content
    assert "policy.md p.1 chunk:100" in first.content


def test_retrieval_and_rerank_settings_validation() -> None:
    assert (
        Settings(
            app_env="test",
            retrieval_top_k_default=3,
            retrieval_top_k_max=5,
            rerank_top_n_default=2,
            rerank_top_n_max=3,
            rerank_provider="fake",
            rerank_score_min=0.0,
            rerank_score_max=1.0,
            ask_top_k_default=3,
            ask_rerank_top_n_default=2,
            generation_provider="fake",
        ).rerank_provider
        == "fake"
    )

    with pytest.raises(ValueError):
        Settings(rerank_provider="remote")
    with pytest.raises(ValueError):
        Settings(retrieval_top_k_default=10, retrieval_top_k_max=5)
    with pytest.raises(ValueError):
        Settings(rerank_top_n_default=6, rerank_top_n_max=5)
    with pytest.raises(ValueError):
        Settings(rerank_score_min=1.0, rerank_score_max=1.0)
    with pytest.raises(ValueError):
        Settings(ask_top_k_default=6, retrieval_top_k_max=5)
    with pytest.raises(ValueError):
        Settings(ask_rerank_top_n_default=6, rerank_top_n_max=5)
    with pytest.raises(ValueError):
        Settings(generation_provider="remote")


def test_sparse_query_normalization_and_score_order_are_deterministic() -> None:
    normalized = normalize_sparse_query(
        "Alpha alpha secondary SQL_123 beta gamma delta",
        max_terms=4,
    )

    assert normalized.terms == ("alpha", "secondary", "sql_123", "beta")
    assert normalized.search_text == "alpha secondary sql_123 beta"

    ranked = normalize_sparse_scores([(2, 2.0), (1, 2.0), (3, 1.0), (4, 0.0)])
    assert [(candidate.document_chunk_id, candidate.rank_order) for candidate in ranked] == [
        (1, 1),
        (2, 2),
        (3, 3),
    ]
    assert ranked[0].sparse_score == 1.0
    assert ranked[2].sparse_score == 0.5
    rounded_tie_ranked = normalize_sparse_scores([(2, 1.0000002), (1, 1.0000001)])
    assert [candidate.document_chunk_id for candidate in rounded_tie_ranked] == [2, 1]
    assert rounded_tie_ranked[0].sparse_score == rounded_tie_ranked[1].sparse_score


def test_sparse_settings_validation() -> None:
    settings = Settings(
        sparse_provider="postgres_fts",
        sparse_language="english",
        sparse_min_query_terms=2,
        sparse_max_query_terms=4,
        sparse_score_normalization="max",
    )

    assert settings.sparse_provider == "postgres_fts"
    assert settings.sparse_language == "english"

    with pytest.raises(ValueError):
        Settings(sparse_provider="external")
    with pytest.raises(ValueError):
        Settings(sparse_language="japanese")
    with pytest.raises(ValueError):
        Settings(sparse_min_query_terms=5, sparse_max_query_terms=4)
    with pytest.raises(ValueError):
        Settings(sparse_score_normalization="none")


def test_in_memory_vector_search_client_scores_fake_qdrant_points() -> None:
    qdrant = InMemoryQdrantClient()
    qdrant.create_collection(
        QdrantCollectionConfig(name="document_chunks", vector_dimension=3),
    )
    qdrant.upsert_points(
        "document_chunks",
        [
            QdrantPoint(
                point_id=1,
                vector=[1.0, 0.0, 0.0],
                payload=_qdrant_payload(document_chunk_id=1),
            ),
            QdrantPoint(
                point_id=2,
                vector=[0.0, 1.0, 0.0],
                payload=_qdrant_payload(document_chunk_id=2),
            ),
        ],
    )

    candidates = InMemoryVectorSearchClient(qdrant).search(
        collection_name="document_chunks",
        query_vector=[1.0, 0.0, 0.0],
        limit=2,
        filters=RetrievalFilters(),
    )

    assert [candidate.document_chunk_id for candidate in candidates] == [1, 2]
    assert candidates[0].retrieval_score > candidates[1].retrieval_score


def test_rag_search_admin_success_persists_standalone_run_and_items(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha policy", "top_k": 5, "rerank_top_n": 1},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "succeeded"
    assert len(vector_client.query_vectors) == 1
    assert len(vector_client.query_vectors[0]) == 4
    summary = data["retrieval_score_summary"]
    assert summary["top1_rerank_score"] is not None
    assert 0.0 <= summary["top1_rerank_score"] <= 1.0
    assert summary == {
        "requested_top_k": 5,
        "qdrant_candidate_count": 5,
        "sparse_candidate_count": None,
        "post_filter_candidate_count": 2,
        "selected_count": 1,
        "excluded_by_rdb_check_count": 3,
        "top1_retrieval_score": 0.91,
        "top3_avg_retrieval_score": 0.865,
        "top1_rerank_score": summary["top1_rerank_score"],
    }
    assert data["items"]
    assert [item["rerank_order"] for item in data["items"]] == [1, 2]
    assert sum(1 for item in data["items"] if item["selected_flag"]) == 1
    first = data["items"][0]
    assert first["source_label"] == "hand book.pdf"
    assert len(first["snippet"]) <= 32
    assert "full active chunk text should not be returned whole" not in first["snippet"]
    assert "content_text" not in str(first["payload_snapshot"])
    assert "document_name" not in first["payload_snapshot"]
    assert first["payload_snapshot"]["source_label"] == first["source_label"]
    assert "storage_key" not in str(data).lower()
    assert "password" not in str(data).lower()

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.chat_session_id is None
        assert run.request_message_id is None
        assert run.status == "succeeded"
        assert run.strategy_type == "dense"
        assert run.query_plan_json == {
            "schema_version": "phase2.trace.v1",
            "strategy_type": "dense",
            "query_mode": "single_query",
            "query_hash": hashlib.sha256(b"alpha policy").hexdigest(),
            "rewrite_applied": False,
            "sub_query_count": 0,
            "metadata_filter_applied": False,
            "metadata_filter_count": 0,
            "logical_document_filter_count": 0,
            "reason_codes": ["phase1_compat_default_dense"],
        }
        assert run.strategy_decision_json == {
            "schema_version": "phase2.trace.v1",
            "selected_strategy": "dense",
            "fallback_strategy": "dense",
            "fallback_used": False,
            "router_enabled": False,
            "decision_source": "default",
            "decision_policy": "static_dense",
            "reason_codes": ["phase1_compat_default_dense"],
        }
        assert run.retrieval_settings_json == {
            "schema_version": "phase2.trace.v1",
            "strategy_type": "dense",
            "default_strategy": "dense",
            "top_k": 5,
            "rerank_top_n": 1,
            "embedding_provider": "fake",
            "rerank_provider": "fake",
            "generation_provider": "fake",
            "qdrant_collection": "document_chunks",
            "rdb_final_check_enabled": True,
            "modality": "text",
            "logical_document_filter_count": 0,
            "hybrid_enabled": False,
            "router_enabled": False,
            "trace_enabled": True,
            "fusion_method": "rrf",
        }
        assert run.latency_breakdown_json is not None
        latency = run.latency_breakdown_json
        assert latency["schema_version"] == "phase2.trace.v1"
        assert latency["total_ms"] >= 0
        assert latency["query_embedding_ms"] >= 0
        assert latency["qdrant_search_ms"] >= 0
        assert latency["rdb_final_check_ms"] >= 0
        assert latency["rerank_ms"] >= 0
        assert latency["retrieval_items_persist_ms"] >= 0
        assert "generation_ms" not in latency
        assert "alpha policy" not in str(run.query_plan_json)
        assert "query_plan_json" not in str(data)
        assert run.answer_confidence is None
        assert run.groundedness_score is None
        assert run.confidence_label is None
        assert run.retrieval_score_summary == data["retrieval_score_summary"]
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 2
        )
        assert db.query(Citation).count() == 0
        items = (
            db.query(RetrievalRunItem)
            .filter_by(retrieval_run_id=run.retrieval_run_id)
            .order_by(RetrievalRunItem.rank_order.asc())
            .all()
        )
        assert [item.retrieval_source for item in items] == ["dense", "dense"]
        first_score_breakdown = items[0].score_breakdown_json
        assert first_score_breakdown is not None
        assert first_score_breakdown == {
            "schema_version": "phase2.trace.v1",
            "retrieval_source": "dense",
            "dense_score": 0.91,
            "rerank_score": first_score_breakdown["rerank_score"],
            "rank_order": 1,
            "rerank_order": 1,
            "final_rank": 1,
            "selected_flag": True,
        }
        snapshots = [item.payload_snapshot for item in items]
        for snapshot in snapshots:
            assert snapshot is not None
            assert "content_text" not in str(snapshot)
            assert "document_name" not in snapshot
        assert "content_text" not in str(first_score_breakdown)
        assert "raw_chunk_text" not in str(first_score_breakdown)


def test_rag_search_sparse_success_persists_trace_and_score_breakdown(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={
            "query": "alpha secondary material",
            "top_k": 5,
            "rerank_top_n": 1,
            "strategy": "sparse",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "succeeded"
    assert vector_client.query_vectors == []
    summary = data["retrieval_score_summary"]
    assert summary["qdrant_candidate_count"] == 0
    assert summary["sparse_candidate_count"] == 2
    assert summary["post_filter_candidate_count"] == 2
    assert summary["excluded_by_rdb_check_count"] == 0
    assert summary["selected_count"] == 1
    assert summary["top1_rerank_score"] is None
    assert len(data["items"]) == 2
    assert all(item["rerank_score"] is None for item in data["items"])
    assert all(item["rerank_order"] is None for item in data["items"])
    assert [item["rank_order"] for item in data["items"]] == [1, 2]
    assert sum(1 for item in data["items"] if item["selected_flag"]) == 1
    assert "content_text" not in str(data)
    assert "OPENAI_API_KEY" not in str(data)
    assert "full active chunk text should not be returned whole" not in str(data)

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.strategy_type == "sparse"
        _assert_safe_run_trace(run, raw_query="alpha secondary material", strategy="sparse")
        assert run.query_plan_json is not None
        assert run.query_plan_json["reason_codes"] == [
            "phase2_sparse_lexical",
            "normalized_terms:3",
        ]
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["decision_source"] == "request"
        assert run.retrieval_settings_json is not None
        assert run.retrieval_settings_json["sparse_provider"] == "postgres_fts"
        assert run.retrieval_settings_json["sparse_language"] == "simple"
        assert run.latency_breakdown_json is not None
        assert run.latency_breakdown_json["sparse_search_ms"] >= 0
        assert run.latency_breakdown_json["rdb_final_check_ms"] >= 0
        assert run.latency_breakdown_json["retrieval_items_persist_ms"] >= 0
        assert "qdrant_search_ms" not in run.latency_breakdown_json
        assert "query_embedding_ms" not in run.latency_breakdown_json
        assert "rerank_ms" not in run.latency_breakdown_json
        items = (
            db.query(RetrievalRunItem)
            .filter_by(retrieval_run_id=run.retrieval_run_id)
            .order_by(RetrievalRunItem.rank_order.asc())
            .all()
        )
        assert [item.retrieval_source for item in items] == ["sparse", "sparse"]
        first_score_breakdown = items[0].score_breakdown_json
        assert first_score_breakdown is not None
        assert first_score_breakdown == {
            "schema_version": "phase2.trace.v1",
            "retrieval_source": "sparse",
            "sparse_score": first_score_breakdown["sparse_score"],
            "rank_order": 1,
            "final_rank": 1,
            "selected_flag": True,
        }
        assert 0.0 <= first_score_breakdown["sparse_score"] <= 1.0
        assert "content_text" not in str(first_score_breakdown)
        assert "raw_chunk_text" not in str(first_score_breakdown)
        assert all(item.rerank_score is None and item.rerank_order is None for item in items)


def test_rag_search_sparse_filters_invalid_candidates_before_limit(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    with session_factory() as db:
        for document_chunk_id in (200, 300, 400):
            chunk = db.get(DocumentChunk, document_chunk_id)
            assert chunk is not None
            chunk.content_text = "alpha " * 200
            chunk.char_count = len(chunk.content_text)
        db.commit()

    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha", "top_k": 1, "strategy": "sparse"},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert vector_client.query_vectors == []
    assert len(data["items"]) == 1
    assert data["items"][0]["document_chunk_id"] in {100, 101}
    summary = data["retrieval_score_summary"]
    assert summary["sparse_candidate_count"] == 1
    assert summary["post_filter_candidate_count"] == 1
    assert summary["excluded_by_rdb_check_count"] == 0
    assert "content_text" not in str(data)

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.strategy_type == "sparse"
        items = db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        assert len(items) == 1
        assert items[0].retrieval_source == "sparse"
        assert items[0].document_chunk_id in {100, 101}


def test_rag_search_sparse_no_result_succeeds_without_items(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "zzzz-no-match", "strategy": "sparse"},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == []
    assert vector_client.query_vectors == []
    assert data["retrieval_score_summary"]["sparse_candidate_count"] == 0
    assert data["retrieval_score_summary"]["selected_count"] == 0
    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.strategy_type == "sparse"
        _assert_safe_run_trace(run, raw_query="zzzz-no-match", strategy="sparse")
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )


def test_rag_search_unsupported_strategy_returns_safe_error(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha secret-token", "strategy": "hybrid"},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "strategy_not_enabled"
    assert "secret-token" not in str(body)
    assert vector_client.query_vectors == []
    with session_factory() as db:
        assert db.query(RetrievalRun).count() == 0


def test_rag_search_zero_result_succeeds_without_items(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    vector_client.candidates = []
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "missing"},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["items"] == []
    assert data["retrieval_score_summary"]["selected_count"] == 0
    assert data["retrieval_score_summary"]["post_filter_candidate_count"] == 0
    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.strategy_type == "dense"
        assert run.query_plan_json is not None
        assert run.query_plan_json["query_hash"] == hashlib.sha256(b"missing").hexdigest()
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["selected_strategy"] == "dense"
        assert run.latency_breakdown_json is not None
        assert run.latency_breakdown_json["total_ms"] >= 0
        assert "generation_ms" not in run.latency_breakdown_json
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )


def test_rag_search_auth_admin_and_csrf_required(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, _, _ = rag_client

    unauthenticated = client.post("/api/v1/rag/search", json={"query": "alpha"})
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "auth_required"

    viewer_csrf = _login(client, email="viewer@example.com")
    viewer = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha"},
        headers=_unsafe_headers(viewer_csrf),
    )
    assert viewer.status_code == 403
    assert viewer.json()["error"]["code"] == "permission_denied"
    client.cookies.clear()

    _login(client, email="admin@example.com")
    missing_csrf = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha"},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_missing"


def test_rag_search_retrieval_failure_marks_run_failed(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    vector_client.fail = True
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha secret-token"},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "retrieval_failed"
    assert "secret-token" not in str(body)
    with session_factory() as db:
        run = db.query(RetrievalRun).order_by(RetrievalRun.retrieval_run_id.desc()).first()
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "retrieval_failed"
        assert run.answer_confidence is None
        _assert_safe_run_trace(run, raw_query="alpha secret-token")


def test_rag_search_sparse_failure_marks_run_failed_without_raw_query(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        qdrant_collection_name="document_chunks",
    )
    failing_service = RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        sparse_strategy=_FailingSparseStrategy(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: failing_service
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha secret-token", "strategy": "sparse"},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "retrieval_failed"
    assert "secret-token" not in str(body)
    assert vector_client.query_vectors == []
    with session_factory() as db:
        run = db.query(RetrievalRun).order_by(RetrievalRun.retrieval_run_id.desc()).first()
        assert run is not None
        assert run.status == "failed"
        assert run.strategy_type == "sparse"
        assert run.error_code == "retrieval_failed"
        _assert_safe_run_trace(run, raw_query="alpha secret-token", strategy="sparse")
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )


def test_rag_search_rerank_failure_marks_run_failed_without_items(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        qdrant_collection_name="document_chunks",
    )
    failing_service = RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=_FailingReranker(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: failing_service
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha"},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rerank_failed"
    with session_factory() as db:
        run = db.query(RetrievalRun).order_by(RetrievalRun.retrieval_run_id.desc()).first()
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "rerank_failed"
        _assert_safe_run_trace(run, raw_query="alpha")
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )


def test_rag_search_clips_rerank_scores_from_adapter(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, _, vector_client = rag_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        qdrant_collection_name="document_chunks",
    )
    clipping_service = RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=_OutOfRangeReranker(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: clipping_service
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha", "top_k": 5, "rerank_top_n": 1},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["rerank_score"] for item in data["items"]] == [1.0, 0.0]
    assert data["retrieval_score_summary"]["top1_rerank_score"] == 1.0


def test_rag_search_malformed_rerank_results_mark_run_failed(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        qdrant_collection_name="document_chunks",
    )
    malformed_service = RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=_DuplicateOrderReranker(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: malformed_service
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha"},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rerank_failed"
    with session_factory() as db:
        run = db.query(RetrievalRun).order_by(RetrievalRun.retrieval_run_id.desc()).first()
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "rerank_failed"
        _assert_safe_run_trace(run, raw_query="alpha")
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )


def test_rag_search_invalid_rerank_order_type_marks_run_failed(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        qdrant_collection_name="document_chunks",
    )
    malformed_service = RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=_InvalidOrderTypeReranker(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: malformed_service
    csrf_token = _login(client)

    response = client.post(
        "/api/v1/rag/search",
        json={"query": "alpha"},
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rerank_failed"
    with session_factory() as db:
        run = db.query(RetrievalRun).order_by(RetrievalRun.retrieval_run_id.desc()).first()
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "rerank_failed"
        _assert_safe_run_trace(run, raw_query="alpha")
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )


def test_rag_ask_viewer_and_admin_success_persists_messages_run_items_with_citations(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    viewer_csrf = _login(client, email="viewer@example.com")
    viewer_session_id = _create_chat_session(client, viewer_csrf, title="viewer ask")

    viewer_response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": viewer_session_id,
            "client_message_id": "viewer-msg-1",
            "message": "alpha policy summary",
            "top_k": 5,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(viewer_csrf),
    )

    assert viewer_response.status_code == 200
    body = viewer_response.json()
    assert body["meta"]["replayed"] is False
    data = body["data"]
    assert data["chat_session_id"] == viewer_session_id
    assert data["user_message"]["role"] == "user"
    assert data["user_message"]["client_message_id"] == "viewer-msg-1"
    assert data["assistant_message"]["role"] == "assistant"
    assert data["assistant_message"]["linked_retrieval_run_id"] == data["retrieval_run_id"]
    assert "Fake answer" in data["assistant_message"]["content"]
    assert data["citations"][0]["local_citation_id"] == 1
    assert data["citations"][0]["document_chunk_id"] == 100
    assert data["citations"][0]["source_label"] == "hand book.pdf"
    assert data["citations"][0]["old_version_flag"] is False
    assert data["confidence"]["confidence_label"] in {"High", "Medium", "Low"}
    assert (
        "full active chunk text should not be returned whole"
        not in data["assistant_message"]["content"]
    )
    assert len(data["citations"][0]["snippet"]) <= 240
    assert "token" not in str(body).lower()
    assert "storage_key" not in str(body).lower()
    assert len(vector_client.query_vectors) == 1

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.chat_session_id == viewer_session_id
        assert run.request_message_id == data["user_message"]["chat_message_id"]
        assert run.status == "succeeded"
        assert run.strategy_type == "dense"
        _assert_safe_run_trace(run, raw_query="alpha policy summary")
        assert run.latency_breakdown_json is not None
        assert "generation_ms" in run.latency_breakdown_json
        assert "citation_build_ms" in run.latency_breakdown_json
        assert "confidence_ms" in run.latency_breakdown_json
        assert run.answer_confidence is not None
        assert run.groundedness_score is not None
        assert run.confidence_label in {"High", "Medium", "Low"}
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 2
        )
        citation = db.query(Citation).one()
        assert citation.retrieval_run_id == run.retrieval_run_id
        assert citation.document_chunk_id == 100
        assert citation.rank_order == 1
        messages = (
            db.query(ChatMessage)
            .filter_by(chat_session_id=viewer_session_id)
            .order_by(ChatMessage.chat_message_id.asc())
            .all()
        )
        assert [message.role for message in messages] == ["user", "assistant"]
        assert messages[1].linked_retrieval_run_id == run.retrieval_run_id

    client.cookies.clear()
    admin_csrf = _login(client, email="admin@example.com")
    admin_session_id = _create_chat_session(client, admin_csrf, title="admin ask")
    admin_response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": admin_session_id,
            "client_message_id": "admin-msg-1",
            "message": "alpha policy summary",
        },
        headers=_unsafe_headers(admin_csrf),
    )
    assert admin_response.status_code == 200
    assert admin_response.json()["data"]["chat_session_id"] == admin_session_id


def test_rag_ask_replay_and_duplicate_state_handling(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, _ = rag_client
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="duplicates")
    payload = {
        "chat_session_id": chat_session_id,
        "client_message_id": "dup-msg-1",
        "message": "alpha policy replay",
    }

    first = client.post("/api/v1/rag/ask", json=payload, headers=_unsafe_headers(csrf_token))
    assert first.status_code == 200
    replay = client.post("/api/v1/rag/ask", json=payload, headers=_unsafe_headers(csrf_token))
    assert replay.status_code == 200
    assert replay.json()["meta"]["replayed"] is True
    assert (
        replay.json()["data"]["user_message"]["chat_message_id"]
        == first.json()["data"]["user_message"]["chat_message_id"]
    )
    assert replay.json()["data"]["citations"] == first.json()["data"]["citations"]
    assert replay.json()["data"]["confidence"] == first.json()["data"]["confidence"]
    with session_factory() as db:
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 2

    conflict = client.post(
        "/api/v1/rag/ask",
        json={**payload, "message": "different body"},
        headers=_unsafe_headers(csrf_token),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "client_message_conflict"

    now = datetime.now(UTC)
    with session_factory() as db:
        db.add(ChatMessage(chat_session_id=chat_session_id, role="user", content="still running"))
        db.flush()
        running_message = db.query(ChatMessage).order_by(ChatMessage.chat_message_id.desc()).first()
        assert running_message is not None
        running_message.client_message_id = "running-msg"
        db.add(
            RetrievalRun(
                chat_session_id=chat_session_id,
                request_message_id=running_message.chat_message_id,
                status="running",
                started_at=now,
            )
        )
        db.add(ChatMessage(chat_session_id=chat_session_id, role="user", content="failed body"))
        db.flush()
        failed_message = db.query(ChatMessage).order_by(ChatMessage.chat_message_id.desc()).first()
        assert failed_message is not None
        failed_message.client_message_id = "failed-msg"
        db.add(
            RetrievalRun(
                chat_session_id=chat_session_id,
                request_message_id=failed_message.chat_message_id,
                status="failed",
                error_code="retrieval_failed",
                started_at=now,
                finished_at=now,
            )
        )
        db.commit()

    running = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "running-msg",
            "message": "still running",
        },
        headers=_unsafe_headers(csrf_token),
    )
    assert running.status_code == 409
    assert running.json()["error"]["code"] == "request_in_progress"

    failed = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "failed-msg",
            "message": "failed body",
        },
        headers=_unsafe_headers(csrf_token),
    )
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "conflict"


def test_rag_ask_context_assembly_is_bounded_to_selected_items(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, _, vector_client = rag_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        generation_max_context_chars=100,
    )
    generator = _RecordingAnswerGenerator()
    service = RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=generator,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: service
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="context")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "context-msg",
            "message": "alpha policy context",
            "top_k": 5,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    assert generator.last_request is not None
    assert len(generator.last_request.context_items) == 1
    assert sum(len(item.text) for item in generator.last_request.context_items) <= 100
    assert generator.last_request.context_items[0].source_label == "hand book.pdf"


def test_rag_ask_no_context_marks_run_failed_without_assistant(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    vector_client.candidates = []
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="no context")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "no-context-msg",
            "message": "missing context",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    assert "missing context" not in str(response.json())
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "no_context_found"
        assert run.request_message_id == messages[0].chat_message_id
        _assert_safe_run_trace(run, raw_query="missing context")
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )
        assert db.query(Citation).count() == 0


def test_rag_ask_generation_failure_keeps_user_message_without_placeholder(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        qdrant_collection_name="document_chunks",
    )
    failing_service = RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_FailingAnswerGenerator(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: failing_service
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="generation failure")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "generation-fail-msg",
            "message": "alpha policy generation failure",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "generation_failed"
    assert "alpha policy generation failure" not in str(response.json())
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "generation_failed"
        assert run.request_message_id == messages[0].chat_message_id
        _assert_safe_run_trace(run, raw_query="alpha policy generation failure")
        assert run.latency_breakdown_json is not None
        assert "generation_ms" in run.latency_breakdown_json
        run_items = (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        )
        assert run_items
        assert all(item.score_breakdown_json is not None for item in run_items)
        assert db.query(Citation).count() == 0


def test_rag_ask_retrieval_failure_marks_run_failed_without_assistant(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    vector_client.fail = True
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="retrieval failure")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "retrieval-fail-msg",
            "message": "alpha policy retrieval failure",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retrieval_failed"
    assert "alpha policy retrieval failure" not in str(response.json())
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "retrieval_failed"
        assert run.request_message_id == messages[0].chat_message_id
        _assert_safe_run_trace(run, raw_query="alpha policy retrieval failure")
        assert db.query(Citation).count() == 0


def test_rag_ask_rerank_failure_marks_run_failed_without_assistant(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        qdrant_collection_name="document_chunks",
    )
    failing_service = RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=_FailingReranker(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: failing_service
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="rerank failure")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "rerank-fail-msg",
            "message": "alpha policy rerank failure",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rerank_failed"
    assert "alpha policy rerank failure" not in str(response.json())
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "rerank_failed"
        assert run.request_message_id == messages[0].chat_message_id
        _assert_safe_run_trace(run, raw_query="alpha policy rerank failure")
        assert db.query(Citation).count() == 0


def test_rag_ask_auth_csrf_and_client_message_id_required(
    rag_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, _, _ = rag_client

    unauthenticated = client.post(
        "/api/v1/rag/ask",
        json={"chat_session_id": 1, "client_message_id": "x", "message": "alpha"},
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "auth_required"

    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="auth")
    missing_csrf = client.post(
        "/api/v1/rag/ask",
        json={"chat_session_id": chat_session_id, "client_message_id": "x", "message": "alpha"},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_missing"

    missing_client_id = client.post(
        "/api/v1/rag/ask",
        json={"chat_session_id": chat_session_id, "message": "alpha"},
        headers=_unsafe_headers(csrf_token),
    )
    assert missing_client_id.status_code == 422
    assert missing_client_id.json()["error"]["code"] == "validation_error"


class _StaticVectorClient:
    def __init__(self, candidates: list[VectorSearchCandidate]) -> None:
        self.candidates = candidates
        self.fail = False
        self.query_vectors: list[list[float]] = []

    def search(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters,
    ) -> list[VectorSearchCandidate]:
        if self.fail:
            raise RetrievalError()
        self.query_vectors.append([float(value) for value in query_vector])
        return self.candidates[:limit]


def _candidate(
    document_chunk_id: int,
    retrieval_score: float,
    qdrant_order: int,
) -> VectorSearchCandidate:
    return VectorSearchCandidate(
        document_chunk_id=document_chunk_id,
        retrieval_score=retrieval_score,
        qdrant_order=qdrant_order,
        payload={},
    )


class _FailingReranker:
    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[Any]:
        raise RerankError()


class _FailingSparseStrategy(SparseRetrievalStrategy):
    def search(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        settings: Settings,
    ) -> list[VectorSearchCandidate]:
        raise RetrievalError()


class _FailingAnswerGenerator:
    def generate(self, request: GenerationRequest) -> Any:
        raise AnswerGenerationError()


class _RecordingAnswerGenerator:
    def __init__(self) -> None:
        self.last_request: GenerationRequest | None = None

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.last_request = request
        return GenerationResult(content="recorded answer [1]")


class _OutOfRangeReranker:
    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankResult]:
        return [
            RerankResult(
                document_chunk_id=candidate.document_chunk_id,
                rerank_score=10.0 if index == 1 else -2.0,
                rerank_order=index,
            )
            for index, candidate in enumerate(candidates, start=1)
        ]


class _DuplicateOrderReranker:
    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankResult]:
        return [
            RerankResult(
                document_chunk_id=candidate.document_chunk_id,
                rerank_score=0.5,
                rerank_order=1,
            )
            for candidate in candidates
        ]


class _InvalidOrderTypeReranker:
    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankResult]:
        return [
            RerankResult(
                document_chunk_id=candidate.document_chunk_id,
                rerank_score=0.5,
                rerank_order=cast(Any, "1" if index == 1 else index),
            )
            for index, candidate in enumerate(candidates, start=1)
        ]


def _seed_auth(db: Session) -> None:
    admin_role = Role(role_name="admin", description="Admin")
    viewer_role = Role(role_name="viewer", description="Viewer")
    db.add_all([admin_role, viewer_role])
    db.flush()
    password_hash = hash_password(TEST_PASSWORD)
    db.add_all(
        [
            User(
                role_id=admin_role.role_id,
                email="admin@example.com",
                display_name="Admin",
                password_hash=password_hash,
                status="active",
            ),
            User(
                role_id=viewer_role.role_id,
                email="viewer@example.com",
                display_name="Viewer",
                password_hash=password_hash,
                status="active",
            ),
        ]
    )


def _seed_documents(db: Session) -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            LogicalDocument(logical_document_id=10, owner_user_id=1, title="Active"),
            LogicalDocument(
                logical_document_id=20,
                owner_user_id=1,
                title="Archived",
                status="archived",
                archived_at=now,
            ),
            LogicalDocument(
                logical_document_id=30,
                owner_user_id=1,
                title="InactiveVersion",
            ),
            LogicalDocument(
                logical_document_id=40,
                owner_user_id=1,
                title="FailedVersion",
            ),
        ]
    )
    db.add_all(
        [
            _version(10, 10, "ready", True, "a", file_name="C:\\unsafe\\hand\tbook.pdf"),
            _version(20, 20, "ready", True, "b"),
            _version(30, 30, "ready", False, "c"),
            _version(40, 40, "failed", False, "d", error_code="embedding_failed"),
        ]
    )
    db.add_all(
        [
            _chunk(
                100,
                10,
                "alpha policy full active chunk text should not be returned whole " * 4,
            ),
            _chunk(
                101,
                10,
                "alpha secondary material " * 5,
                chunk_index=1,
                page_from=2,
            ),
            _chunk(200, 20, "alpha archived"),
            _chunk(300, 30, "alpha inactive"),
            _chunk(400, 40, "alpha failed"),
        ]
    )


def _version(
    document_version_id: int,
    logical_document_id: int,
    status: str,
    is_active: bool,
    hash_prefix: str,
    *,
    file_name: str | None = None,
    error_code: str | None = None,
) -> DocumentVersion:
    return DocumentVersion(
        document_version_id=document_version_id,
        logical_document_id=logical_document_id,
        version_no=1,
        content_hash=hash_prefix * 64,
        status=status,
        is_active=is_active,
        error_code=error_code,
        file_name=file_name or f"{hash_prefix}.txt",
        mime_type="text/plain",
        file_size_bytes=10,
        storage_key=f"storage/{hash_prefix}",
        created_by=1,
    )


def _chunk(
    document_chunk_id: int,
    document_version_id: int,
    text: str,
    *,
    chunk_index: int = 0,
    page_from: int | None = 1,
) -> DocumentChunk:
    return DocumentChunk(
        document_chunk_id=document_chunk_id,
        document_version_id=document_version_id,
        chunk_index=chunk_index,
        chunk_hash=f"{document_chunk_id:064x}",
        content_text=text,
        token_count=10,
        char_count=len(text),
        page_from=page_from,
        page_to=page_from,
        section_title="Intro",
        modality="text",
    )


def _qdrant_payload(*, document_chunk_id: int) -> dict[str, object]:
    return {
        "document_chunk_id": document_chunk_id,
        "logical_document_id": 1,
        "document_version_id": 1,
        "is_active": True,
        "logical_document_status": "active",
        "document_version_status": "ready",
        "modality": "text",
    }


def _login(client: TestClient, email: str = "admin@example.com") -> str:
    csrf_response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert csrf_response.status_code == 200
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
        headers={
            "X-CSRF-Token": csrf_response.json()["data"]["csrf_token"],
            "Origin": ALLOWED_ORIGIN,
        },
    )
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def _create_chat_session(client: TestClient, csrf_token: str, *, title: str) -> int:
    response = client.post(
        "/api/v1/chat/sessions",
        json={"title": title},
        headers=_unsafe_headers(csrf_token),
    )
    assert response.status_code == 201
    return int(response.json()["data"]["chat_session_id"])


def _unsafe_headers(csrf_token: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN}


def _assert_safe_run_trace(
    run: RetrievalRun,
    *,
    raw_query: str,
    strategy: str = "dense",
) -> None:
    assert run.query_plan_json is not None
    assert run.query_plan_json["schema_version"] == "phase2.trace.v1"
    assert run.query_plan_json["strategy_type"] == strategy
    assert (
        run.query_plan_json["query_hash"] == hashlib.sha256(raw_query.encode("utf-8")).hexdigest()
    )
    assert run.strategy_decision_json is not None
    assert run.strategy_decision_json["selected_strategy"] == strategy
    assert run.strategy_decision_json["router_enabled"] is False
    assert run.retrieval_settings_json is not None
    assert run.retrieval_settings_json["strategy_type"] == strategy
    assert run.latency_breakdown_json is not None
    assert run.latency_breakdown_json["schema_version"] == "phase2.trace.v1"
    assert run.latency_breakdown_json["total_ms"] >= 0
    dumped = str(
        {
            "query_plan": run.query_plan_json,
            "strategy_decision": run.strategy_decision_json,
            "settings": run.retrieval_settings_json,
            "latency": run.latency_breakdown_json,
        }
    )
    assert raw_query not in dumped
    assert "raw_prompt" not in dumped
    assert "raw_chunk" not in dumped
    assert "content_text" not in dumped
    assert "secret-token" not in dumped
