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
from app.main import create_app
from app.rag.generation import (
    AnswerGenerationError,
    FakeAnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    GenerationResult,
)
from app.rag.llm_orchestrator import (
    LLMToolCall,
    LLMToolCallingRetrievalOrchestrator,
    LLMToolPlanningRequest,
    OpenAICompatibleJSONToolPlanner,
    create_llm_tool_call_planner,
)
from app.rag.rerank import FakeRerankerClient, RerankCandidate, RerankError
from app.rag.retrieval import RetrievalError, RetrievalFilters, VectorSearchCandidate
from app.rag.strategy import RetrievalStrategy
from app.schemas.rag_strategy import RouterDecisionTrace
from app.services.rag_service import RagService

ALLOWED_ORIGIN = "http://localhost:5173"
TEST_PASSWORD = "password"


@pytest.fixture
def rag_ask_client() -> Iterator[tuple[TestClient, sessionmaker[Session], _StaticVectorClient]]:
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

    vector_client = _StaticVectorClient([_candidate(100, 0.91, 1), _candidate(101, 0.82, 2)])
    service = RagService(
        settings=_settings(),
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


def test_fake_answer_generator_is_deterministic_and_redacts_context_text() -> None:
    generator = FakeAnswerGenerator()
    request = GenerationRequest(
        message="alpha policy",
        context_items=[
            GenerationContextItem(
                document_chunk_id=100,
                source_label="policy]\nsecret.md",
                text="raw context text that should not be echoed",
                page_from=1,
                page_to=1,
            ),
        ],
        max_output_chars=200,
    )

    first = generator.generate(request)
    second = generator.generate(request)

    assert first == second
    truncated = generator.generate(
        GenerationRequest(
            message="alpha policy",
            context_items=[
                GenerationContextItem(
                    document_chunk_id=100,
                    source_label="policy]\nsecret.md",
                    text="raw context text that should not be echoed",
                    page_from=1,
                    page_to=1,
                ),
            ],
            max_output_chars=20,
        )
    )
    assert "[1]" in truncated.content
    assert "raw context text" not in first.content
    assert "policy) secret.md p.1 chunk:100" in first.content


def test_rag_ask_success_replay_and_duplicate_state_handling(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    viewer_csrf = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, viewer_csrf, title="viewer ask")
    payload = {
        "chat_session_id": chat_session_id,
        "client_message_id": "viewer-msg-1",
        "message": "alpha policy summary",
        "model_key": "lmstudio:qwen3.5-9b",
        "top_k": 2,
        "rerank_top_n": 1,
    }

    first = client.post("/api/v1/rag/ask", json=payload, headers=_unsafe_headers(viewer_csrf))

    assert first.status_code == 200
    body = first.json()
    assert body["meta"]["replayed"] is False
    data = body["data"]
    assert data["user_message"]["role"] == "user"
    assert data["user_message"]["client_message_id"] == "viewer-msg-1"
    assert data["assistant_message"]["role"] == "assistant"
    assert data["assistant_message"]["linked_retrieval_run_id"] == data["retrieval_run_id"]
    assert "Fake answer" in data["assistant_message"]["content"]
    assert "[1]" in data["assistant_message"]["content"]
    assert data["citations"][0]["local_citation_id"] == 1
    assert data["citations"][0]["document_chunk_id"] == 100
    assert data["citations"][0]["source_label"] == "hand book.pdf"
    assert data["citations"][0]["old_version_flag"] is False
    assert 0.0 <= data["confidence"]["answer_confidence"] <= 1.0
    assert 0.0 <= data["confidence"]["groundedness_score"] <= 1.0
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
        assert run.chat_session_id == chat_session_id
        assert run.request_message_id == data["user_message"]["chat_message_id"]
        assert run.status == "succeeded"
        assert run.strategy_type == "dense"
        _assert_safe_run_trace(run, raw_query="alpha policy summary")
        retrieval_settings = run.retrieval_settings_json
        assert retrieval_settings is not None
        assert retrieval_settings["schema_version"] == "phase2.trace.v1"
        assert retrieval_settings["strategy_type"] == "dense"
        assert retrieval_settings["top_k"] == 2
        assert retrieval_settings["rerank_top_n"] == 1
        assert retrieval_settings["embedding_provider"] == "fake"
        assert retrieval_settings["rerank_provider"] == "fake"
        assert retrieval_settings["generation_provider"] == "fake"
        assert retrieval_settings["qdrant_collection"] == "document_chunks"
        assert run.latency_breakdown_json is not None
        assert "generation_ms" in run.latency_breakdown_json
        assert "citation_build_ms" in run.latency_breakdown_json
        assert "confidence_ms" in run.latency_breakdown_json
        assert run.answer_confidence is not None
        assert run.groundedness_score is not None
        assert run.confidence_label in {"High", "Medium", "Low"}
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 2
        run_items = (
            db.query(RetrievalRunItem)
            .filter_by(retrieval_run_id=run.retrieval_run_id)
            .order_by(RetrievalRunItem.rank_order.asc())
            .all()
        )
        assert len(run_items) == 2
        assert all(item.retrieval_source == "dense" for item in run_items)
        assert all(item.score_breakdown_json is not None for item in run_items)
        first_breakdown = run_items[0].score_breakdown_json
        assert first_breakdown is not None
        assert first_breakdown["schema_version"] == "phase2.trace.v1"
        assert first_breakdown["final_rank"] == 1
        assert "content_text" not in str(first_breakdown)
        assert "raw_chunk_text" not in str(first_breakdown)
        citation = db.query(Citation).one()
        assert citation.retrieval_run_id == run.retrieval_run_id
        assert citation.document_chunk_id == 100
        assert citation.rank_order == 1
        db.add(
            Citation(
                retrieval_run_id=run.retrieval_run_id,
                document_chunk_id=101,
                snippet="non-selected chunk should stay hidden",
                page_from=2,
                page_to=2,
                display_label="hand book.pdf",
                rank_order=2,
            )
        )
        db.commit()

    replay = client.post("/api/v1/rag/ask", json=payload, headers=_unsafe_headers(viewer_csrf))
    assert replay.status_code == 200
    assert replay.json()["meta"]["replayed"] is True
    assert replay.json()["data"]["citations"] == data["citations"]
    assert replay.json()["data"]["confidence"] == data["confidence"]
    assert (
        replay.json()["data"]["user_message"]["chat_message_id"]
        == data["user_message"]["chat_message_id"]
    )
    with session_factory() as db:
        version = db.get(DocumentVersion, 10)
        assert version is not None
        version.is_active = False
        db.commit()
    old_version_replay = client.post(
        "/api/v1/rag/ask",
        json=payload,
        headers=_unsafe_headers(viewer_csrf),
    )
    assert old_version_replay.status_code == 200
    assert old_version_replay.json()["data"]["citations"][0]["old_version_flag"] is True
    with session_factory() as db:
        version = db.get(DocumentVersion, 10)
        assert version is not None
        version.is_active = True
        db.commit()
    with session_factory() as db:
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 2

    different_body = client.post(
        "/api/v1/rag/ask",
        json={**payload, "message": "different body"},
        headers=_unsafe_headers(viewer_csrf),
    )
    assert different_body.status_code == 409
    assert different_body.json()["error"]["code"] == "client_message_conflict"

    now = datetime.now(UTC)
    with session_factory() as db:
        running_message = ChatMessage(
            chat_session_id=chat_session_id,
            role="user",
            content="still running",
            client_message_id="running-msg",
        )
        failed_message = ChatMessage(
            chat_session_id=chat_session_id,
            role="user",
            content="failed body",
            client_message_id="failed-msg",
        )
        db.add_all([running_message, failed_message])
        db.flush()
        db.add_all(
            [
                RetrievalRun(
                    chat_session_id=chat_session_id,
                    request_message_id=running_message.chat_message_id,
                    status="running",
                    started_at=now,
                ),
                RetrievalRun(
                    chat_session_id=chat_session_id,
                    request_message_id=failed_message.chat_message_id,
                    status="failed",
                    error_code="retrieval_failed",
                    started_at=now,
                    finished_at=now,
                ),
            ]
        )
        db.commit()

    running = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "running-msg",
            "message": "still running",
        },
        headers=_unsafe_headers(viewer_csrf),
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
        headers=_unsafe_headers(viewer_csrf),
    )
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "conflict"

    client.cookies.clear()
    admin_csrf = _login(client, email="admin@example.com")
    admin_session_id = _create_chat_session(client, admin_csrf, title="admin ask")
    admin = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": admin_session_id,
            "client_message_id": "admin-msg-1",
            "message": "alpha policy admin",
        },
        headers=_unsafe_headers(admin_csrf),
    )
    assert admin.status_code == 200


def test_rag_ask_agentic_router_opt_in_persists_router_decision(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="agentic ask")
    message = "HTTP 500 API_ERROR SQL_ERROR alpha secondary"

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "agentic-msg-1",
            "message": message,
            "strategy": "agentic_router",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["retrieval_run_id"] == data["assistant_message"]["linked_retrieval_run_id"]
    assert "[1]" in data["assistant_message"]["content"]
    assert "content_text" not in str(response.json())
    assert len(vector_client.query_vectors) == 1

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.strategy_type == "agentic_router"
        assert run.query_plan_json is not None
        assert run.query_plan_json["strategy_type"] == "agentic_router"
        assert (
            run.query_plan_json["query_hash"] == hashlib.sha256(message.encode("utf-8")).hexdigest()
        )
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["requested_strategy"] == "agentic_router"
        assert run.strategy_decision_json["selected_strategy"] == "hybrid"
        assert run.strategy_decision_json["execution_strategy"] == "hybrid"
        assert run.strategy_decision_json["fallback_used"] is False
        assert run.latency_breakdown_json is not None
        assert run.latency_breakdown_json["strategy_router_ms"] >= 0
        assert "generation_ms" in run.latency_breakdown_json
        items = db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        assert items
        assert all(item.retrieval_source == "hybrid" for item in items)
        dumped = str({"query_plan": run.query_plan_json, "decision": run.strategy_decision_json})
        assert message not in dumped
        assert "raw_prompt" not in dumped
        assert "raw_chunk" not in dumped
        assert "content_text" not in dumped


def test_rag_ask_hybrid_opt_in_generates_answer_with_hybrid_trace(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="hybrid ask")
    message = "Compare alpha secondary retrieval evidence"

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "hybrid-msg-1",
            "message": message,
            "strategy": "hybrid",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "[1]" in data["assistant_message"]["content"]
    assert "content_text" not in str(response.json())
    assert len(vector_client.query_vectors) == 1

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.strategy_type == "hybrid"
        assert run.query_plan_json is not None
        assert run.query_plan_json["strategy_type"] == "hybrid"
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["selected_strategy"] == "hybrid"
        assert run.strategy_decision_json["execution_strategy"] == "hybrid"
        assert run.latency_breakdown_json is not None
        assert "generation_ms" in run.latency_breakdown_json
        items = db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        assert items
        assert all(item.retrieval_source == "hybrid" for item in items)


def test_rag_ask_llm_tool_orchestrator_uses_bounded_tools_and_saves_safe_trace(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm agentic ask")
    message = "Compare alpha secondary retrieval evidence"

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-agentic-msg-1",
            "message": message,
            "strategy": "llm_tool_orchestrator",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "[1]" in data["assistant_message"]["content"]
    assert "content_text" not in str(response.json())
    assert len(vector_client.query_vectors) == 1

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.strategy_type == "llm_tool_orchestrator"
        assert run.query_plan_json is not None
        assert run.query_plan_json["strategy_type"] == "llm_tool_orchestrator"
        assert run.query_plan_json["query_mode"] == "llm_tool_calling_retrieval"
        assert "analysis" in run.query_plan_json
        assert "planner" in run.query_plan_json
        assert "intent" in run.query_plan_json
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["selected_strategy"] == "llm_tool_orchestrator"
        assert run.strategy_decision_json["tool_call_count"] == 2
        assert run.strategy_decision_json["search_call_count"] == 1
        assert run.strategy_decision_json["finalize_called"] is True
        assert run.strategy_decision_json["no_context"] is False
        assert run.latency_breakdown_json is not None
        assert "llm_orchestrator_ms" in run.latency_breakdown_json
        assert "generation_ms" in run.latency_breakdown_json
        settings_snapshot = run.retrieval_settings_json
        assert settings_snapshot is not None
        assert settings_snapshot["max_tool_calls"] == 5
        items = db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        assert items
        assert all(item.retrieval_source == "hybrid" for item in items)
        assert all(item.score_breakdown_json is not None for item in items)
        assert items[0].score_breakdown_json is not None
        assert items[0].score_breakdown_json["retrieval_source"] == "llm_tool_orchestrator"
        dumped = str(
            {
                "query_plan": run.query_plan_json,
                "decision": run.strategy_decision_json,
                "settings": run.retrieval_settings_json,
                "summary": run.retrieval_score_summary,
                "items": [item.score_breakdown_json for item in items],
            }
        )
        assert message not in dumped
        assert "full active chunk text" not in dumped
        assert "raw_prompt" not in dumped
        assert "raw_chunk" not in dumped
        assert "content_text" not in dumped
        assert "normalized_query_preview" not in dumped
        assert "rewritten_query_preview" not in dumped


def test_rag_ask_hybrid_disabled_returns_strategy_not_enabled(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=2,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        search_snippet_max_chars=32,
        generation_provider="fake",
        hybrid_enabled=False,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="hybrid disabled")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "hybrid-disabled-msg-1",
            "message": "alpha policy",
            "strategy": "hybrid",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "strategy_not_enabled"
    assert vector_client.query_vectors == []
    with session_factory() as db:
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 0


def test_rag_ask_llm_tool_orchestrator_budget_exhausted_returns_no_context(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=2,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        search_snippet_max_chars=32,
        generation_provider="fake",
        llm_orchestrator_max_tool_calls=1,
        llm_orchestrator_max_search_calls=1,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm budget")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-budget-msg-1",
            "message": "alpha policy budget",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "no_context_found"
        assert run.strategy_type == "llm_tool_orchestrator"
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["budget_exhausted"] is True
        assert run.strategy_decision_json["finalize_called"] is False
        assert run.strategy_decision_json["no_context"] is True


def test_rag_ask_llm_tool_orchestrator_retrieval_failure_propagates(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    vector_client.fail = True
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm retrieval failure")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-retrieval-failure-msg-1",
            "message": "alpha policy failure",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retrieval_failed"
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "retrieval_failed"


def test_rag_ask_llm_tool_orchestrator_disabled_hybrid_tool_is_not_executed(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=2,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        search_snippet_max_chars=32,
        generation_provider="fake",
        sparse_enabled=False,
        hybrid_enabled=False,
        llm_orchestrator_max_tool_calls=2,
        llm_orchestrator_max_search_calls=2,
    )
    planner = _HybridOnlyPlanner()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=planner,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="disabled hybrid tool")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-disabled-hybrid-tool-msg-1",
            "message": "alpha policy",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    assert planner.requests
    assert "dense_search" in planner.requests[0].available_tools
    assert "finalize_answer" in planner.requests[0].available_tools
    assert "sparse_search" not in planner.requests[0].available_tools
    assert "hybrid_search" not in planner.requests[0].available_tools
    assert vector_client.query_vectors == []
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["search_call_count"] == 0
        assert run.strategy_decision_json["budget_exhausted"] is True
        assert run.strategy_decision_json["tool_results"][0]["error_code"] == "strategy_not_enabled"


def test_rag_ask_llm_tool_orchestrator_empty_finalize_selection_returns_no_context(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _settings()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_EmptyFinalizeAfterSearchPlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="empty finalize")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-empty-finalize-msg-1",
            "message": "alpha empty finalize",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    assert len(vector_client.query_vectors) == 1
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "no_context_found"
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["finalize_called"] is True
        assert run.strategy_decision_json["no_context"] is True
        assert "finalize_answer_empty_selection" in run.strategy_decision_json["reason_codes"]
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )


def test_lmstudio_tool_planner_preserves_executable_query_and_normalizes_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"tool_calls":[{"tool":"dense_search","arguments":'
                                '{"query":"https://example.com/callback support@example.com"}}]}'
                            )
                        }
                    }
                ]
            }

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> Response:
        captured["json"] = json
        return Response()

    monkeypatch.setattr("app.rag.llm_orchestrator.httpx.post", fake_post)
    planner = create_llm_tool_call_planner(
        Settings(
            app_env="test",
            generation_provider="lmstudio",
            generation_model_name="lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M",
            lmstudio_api_key="lm-studio",
            lmstudio_base_url="http://host.docker.internal:1234/v1",
        )
    )

    assert isinstance(planner, OpenAICompatibleJSONToolPlanner)
    calls = planner.plan(
        LLMToolPlanningRequest(
            user_query="Find https://example.com/callback and support@example.com",
            top_k=2,
            max_query_chars=500,
            remaining_timeout_seconds=5,
            remaining_tool_calls=2,
            remaining_search_calls=1,
            available_tools=("dense_search", "finalize_answer"),
            tool_results=[],
        )
    )

    user_payload = captured["json"]["messages"][1]["content"]
    assert captured["json"]["model"] == "qwen3.5-9b"
    assert "https://example.com/callback" in user_payload
    assert "support@example.com" in user_payload
    assert "redacted" not in user_payload
    assert calls == [
        LLMToolCall(
            tool_name="dense_search",
            arguments={"query": "https://example.com/callback support@example.com"},
        )
    ]


def test_rag_ask_llm_tool_orchestrator_timeout_stops_before_retrieval(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=2,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        search_snippet_max_chars=32,
        generation_provider="fake",
        llm_orchestrator_timeout_seconds=1,
    )
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_SingleDensePlanner(),
        clock=_SequenceClock([0.0, 0.0, 2.0]),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm timeout")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-timeout-msg-1",
            "message": "alpha timeout",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    assert vector_client.query_vectors == []
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["timeout_exceeded"] is True
        assert run.strategy_decision_json["search_call_count"] == 0


def test_rag_ask_llm_tool_orchestrator_repeated_query_is_blocked_safely(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _settings()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_RepeatingDensePlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm repeat")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-repeat-msg-1",
            "message": "alpha repeat",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["repeated_query_detected"] is True
        assert run.strategy_decision_json["search_call_count"] == 1
        assert run.strategy_decision_json["no_context"] is True
        dumped = str(run.strategy_decision_json)
        assert "alpha repeat" not in dumped
        assert "full active chunk text" not in dumped


def test_rag_ask_agentic_router_disabled_uses_single_fallback_dense_retrieval(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=2,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        search_snippet_max_chars=32,
        generation_provider="fake",
        router_enabled=False,
        router_sufficiency_top_score_threshold=0.99,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="agentic ask disabled")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "agentic-disabled-msg-1",
            "message": "alpha policy disabled router",
            "strategy": "agentic_router",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    assert len(vector_client.query_vectors) == 1
    data = response.json()["data"]
    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.strategy_type == "agentic_router"
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["execution_strategy"] == "fallback_dense"
        assert run.strategy_decision_json["fallback_reason"] == "router_disabled"
        assert "retrieval_call_count" not in run.strategy_decision_json
        assert run.latency_breakdown_json is not None
        assert "initial_retrieval_ms" not in run.latency_breakdown_json
        assert "fallback_retrieval_ms" not in run.latency_breakdown_json
        items = db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        assert items
        assert all(item.retrieval_source == "fallback_dense" for item in items)


def test_rag_ask_agentic_router_budget_exhausted_returns_no_context_without_assistant(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    vector_client.candidates = []
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="agentic no context")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "agentic-no-context-msg-1",
            "message": "alpha policy missing context",
            "strategy": "agentic_router",
            "filters": {"logical_document_ids": [999]},
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "no_context_found"
        assert run.strategy_type == "agentic_router"
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["retrieval_call_count"] == 2
        assert run.strategy_decision_json["budget_exhausted"] is True
        assert run.strategy_decision_json["no_context"] is True
        assert run.latency_breakdown_json is not None
        assert run.latency_breakdown_json["initial_retrieval_ms"] >= 0
        assert run.latency_breakdown_json["fallback_retrieval_ms"] >= 0
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )


def test_rag_ask_agentic_router_no_context_skips_reranker_failure(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=2,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        search_snippet_max_chars=32,
        generation_provider="fake",
        router_max_retrieval_calls=1,
        router_max_fallback_calls=0,
        router_sufficiency_top_score_threshold=0.99,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=_FailingReranker(),
        strategy_router=cast(Any, _DenseRouter(settings)),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="agentic low score")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "agentic-low-score-msg-1",
            "message": "alpha policy low score",
            "strategy": "agentic_router",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "no_context_found"
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["retrieval_call_count"] == 1
        assert run.strategy_decision_json["budget_exhausted"] is True
        assert run.strategy_decision_json["no_context"] is True
        items = (
            db.query(RetrievalRunItem)
            .filter_by(retrieval_run_id=run.retrieval_run_id)
            .order_by(RetrievalRunItem.rank_order.asc())
            .all()
        )
        assert items
        assert all(item.rerank_score is None for item in items)
        assert all(item.rerank_order is None for item in items)
        assert all(not item.selected_flag for item in items)


def test_rag_ask_agentic_router_respects_store_decision_trace_false(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=2,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        search_snippet_max_chars=32,
        generation_provider="fake",
        router_store_decision_trace=False,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="agentic ask no decision")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "agentic-no-decision-msg-1",
            "message": "HTTP 500 API_ERROR SQL_ERROR alpha secondary",
            "strategy": "agentic_router",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.strategy_type == "agentic_router"
        assert run.query_plan_json is not None
        assert run.strategy_decision_json is None


@pytest.mark.parametrize("strategy", ["sparse", "fallback_dense"])
def test_rag_ask_request_rejects_non_public_strategies_without_persisting_messages(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
    strategy: str,
) -> None:
    client, session_factory, vector_client = rag_ask_client
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="invalid ask strategy")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": f"invalid-strategy-{strategy}",
            "message": "alpha secret-token",
            "strategy": strategy,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "secret-token" not in str(body)
    assert vector_client.query_vectors == []
    with session_factory() as db:
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).count() == 0


def test_rag_ask_rejects_unsupported_model_key_without_persisting_message(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, _ = rag_ask_client
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="unsupported model")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "unsupported-model-msg",
            "message": "alpha policy summary",
            "model_key": "openai:gpt-5.5",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_model"
    with session_factory() as db:
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).count() == 0


def test_rag_ask_persists_and_replays_multiple_citations(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    service = RagService(
        settings=_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_StaticAnswerGenerator("answer cites second [2] then first[1]"),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: service
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="multi citation")
    payload = {
        "chat_session_id": chat_session_id,
        "client_message_id": "multi-citation-msg",
        "message": "alpha policy multi citation",
        "top_k": 2,
        "rerank_top_n": 2,
    }

    response = client.post("/api/v1/rag/ask", json=payload, headers=_unsafe_headers(csrf_token))

    assert response.status_code == 200
    data = response.json()["data"]
    assert [citation["local_citation_id"] for citation in data["citations"]] == [1, 2]
    assert [citation["document_chunk_id"] for citation in data["citations"]] == [100, 101]
    assert data["citations"][0]["source_label"] == "hand book.pdf"
    assert data["citations"][1]["source_label"] == "hand book.pdf"
    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        citations = (
            db.query(Citation)
            .filter_by(retrieval_run_id=run.retrieval_run_id)
            .order_by(Citation.rank_order.asc())
            .all()
        )
        assert [citation.rank_order for citation in citations] == [1, 2]
        assert [citation.document_chunk_id for citation in citations] == [100, 101]

    replay = client.post("/api/v1/rag/ask", json=payload, headers=_unsafe_headers(csrf_token))
    assert replay.status_code == 200
    assert replay.json()["meta"]["replayed"] is True
    assert replay.json()["data"]["citations"] == data["citations"]
    assert replay.json()["data"]["confidence"] == data["confidence"]


def test_rag_ask_no_context_and_generation_failure_do_not_create_assistant(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    vector_client.candidates = []
    csrf_token = _login(client, email="viewer@example.com")
    no_context_session_id = _create_chat_session(client, csrf_token, title="no context")

    no_context = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": no_context_session_id,
            "client_message_id": "no-context-msg",
            "message": "missing context",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert no_context.status_code == 422
    assert no_context.json()["error"]["code"] == "no_context_found"
    assert "missing context" not in str(no_context.json())
    with session_factory() as db:
        no_context_roles = [
            m.role for m in db.query(ChatMessage).filter_by(chat_session_id=no_context_session_id)
        ]
        assert no_context_roles == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=no_context_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "no_context_found"
        _assert_safe_run_trace(run, raw_query="missing context")
        assert db.query(Citation).count() == 0

    vector_client.candidates = [_candidate(100, 0.91, 1)]
    failing_service = RagService(
        settings=_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_FailingAnswerGenerator(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: failing_service
    generation_session_id = _create_chat_session(client, csrf_token, title="generation failure")

    failed_generation = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": generation_session_id,
            "client_message_id": "generation-fail-msg",
            "message": "alpha policy generation failure",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert failed_generation.status_code == 503
    assert failed_generation.json()["error"]["code"] == "generation_failed"
    assert "alpha policy generation failure" not in str(failed_generation.json())
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=generation_session_id).all()
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=generation_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "generation_failed"
        _assert_safe_run_trace(run, raw_query="alpha policy generation failure")
        assert run.latency_breakdown_json is not None
        assert "generation_ms" in run.latency_breakdown_json
        run_items = (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        )
        assert run_items
        assert all(item.score_breakdown_json is not None for item in run_items)
        assert db.query(Citation).count() == 0

    no_marker_service = RagService(
        settings=_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_StaticAnswerGenerator("answer without citation marker"),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: no_marker_service
    no_marker_session_id = _create_chat_session(client, csrf_token, title="no marker")

    no_marker = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": no_marker_session_id,
            "client_message_id": "no-marker-msg",
            "message": "alpha policy no marker",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert no_marker.status_code == 200
    no_marker_data = no_marker.json()["data"]
    assert no_marker_data["assistant_message"]["content"] == ("answer without citation marker [1]")
    assert no_marker_data["citations"][0]["local_citation_id"] == 1
    assert no_marker_data["citations"][0]["document_chunk_id"] == 100
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=no_marker_session_id).all()
        assert [message.role for message in messages] == ["user", "assistant"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=no_marker_session_id).one()
        assert run.status == "succeeded"
        assert run.error_code is None
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 1

    unknown_marker_service = RagService(
        settings=_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_StaticAnswerGenerator("answer with unknown marker [9]"),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: unknown_marker_service
    unknown_marker_session_id = _create_chat_session(client, csrf_token, title="unknown marker")

    unknown_marker = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": unknown_marker_session_id,
            "client_message_id": "unknown-marker-msg",
            "message": "alpha policy unknown marker",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert unknown_marker.status_code == 200
    unknown_marker_data = unknown_marker.json()["data"]
    assert unknown_marker_data["assistant_message"]["content"] == ("answer with unknown marker [1]")
    assert unknown_marker_data["citations"][0]["local_citation_id"] == 1
    assert unknown_marker_data["citations"][0]["document_chunk_id"] == 100
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=unknown_marker_session_id).all()
        assert [message.role for message in messages] == ["user", "assistant"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=unknown_marker_session_id).one()
        assert run.status == "succeeded"
        assert run.error_code is None
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 1

    marker_only_service = RagService(
        settings=_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_StaticAnswerGenerator("[9]"),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: marker_only_service
    marker_only_session_id = _create_chat_session(client, csrf_token, title="marker only")

    marker_only = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": marker_only_session_id,
            "client_message_id": "marker-only-msg",
            "message": "alpha policy marker only",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert marker_only.status_code == 200
    marker_only_data = marker_only.json()["data"]
    assert marker_only_data["assistant_message"]["content"] == (
        "検索された文書には、この質問に直接答えるための十分な根拠がありません [1]。"
    )
    assert marker_only_data["citations"][0]["local_citation_id"] == 1
    assert marker_only_data["citations"][0]["document_chunk_id"] == 100
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=marker_only_session_id).all()
        assert [message.role for message in messages] == ["user", "assistant"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=marker_only_session_id).one()
        assert run.status == "succeeded"
        assert run.error_code is None
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 1

    sensitive_output_service = RagService(
        settings=_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_StaticAnswerGenerator("answer leaks password=verysecret [1]"),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: (
        sensitive_output_service
    )
    sensitive_output_session_id = _create_chat_session(
        client,
        csrf_token,
        title="sensitive output",
    )

    sensitive_output = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": sensitive_output_session_id,
            "client_message_id": "sensitive-output-msg",
            "message": "alpha policy sensitive output",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert sensitive_output.status_code == 500
    assert sensitive_output.json()["error"]["code"] == "citation_build_failed"
    assert "verysecret" not in str(sensitive_output.json())
    with session_factory() as db:
        messages = (
            db.query(ChatMessage).filter_by(chat_session_id=sensitive_output_session_id).all()
        )
        assert [message.role for message in messages] == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=sensitive_output_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "citation_build_failed"
        _assert_safe_run_trace(run, raw_query="alpha policy sensitive output")
        assert run.latency_breakdown_json is not None
        assert "citation_build_ms" in run.latency_breakdown_json
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0


def test_rag_ask_retrieval_and_rerank_failures_keep_only_user_message(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    csrf_token = _login(client, email="viewer@example.com")
    retrieval_session_id = _create_chat_session(client, csrf_token, title="retrieval failure")
    vector_client.fail = True

    retrieval_failed = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": retrieval_session_id,
            "client_message_id": "retrieval-fail-msg",
            "message": "alpha policy retrieval failure",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert retrieval_failed.status_code == 503
    assert retrieval_failed.json()["error"]["code"] == "retrieval_failed"
    with session_factory() as db:
        retrieval_roles = [
            m.role for m in db.query(ChatMessage).filter_by(chat_session_id=retrieval_session_id)
        ]
        assert retrieval_roles == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=retrieval_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "retrieval_failed"
        _assert_safe_run_trace(run, raw_query="alpha policy retrieval failure")

    vector_client.fail = False
    failing_service = RagService(
        settings=_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=_FailingReranker(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: failing_service
    rerank_session_id = _create_chat_session(client, csrf_token, title="rerank failure")

    rerank_failed = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": rerank_session_id,
            "client_message_id": "rerank-fail-msg",
            "message": "alpha policy rerank failure",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert rerank_failed.status_code == 503
    assert rerank_failed.json()["error"]["code"] == "rerank_failed"
    with session_factory() as db:
        rerank_roles = [
            m.role for m in db.query(ChatMessage).filter_by(chat_session_id=rerank_session_id)
        ]
        assert rerank_roles == ["user"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=rerank_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "rerank_failed"
        _assert_safe_run_trace(run, raw_query="alpha policy rerank failure")


def test_rag_ask_auth_csrf_and_client_message_id_required(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, _, _ = rag_ask_client

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


class _DenseRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def route(self, **_: object) -> RouterDecisionTrace:
        return RouterDecisionTrace(
            requested_strategy=RetrievalStrategy.AGENTIC_ROUTER,
            selected_strategy=RetrievalStrategy.DENSE,
            execution_strategy=RetrievalStrategy.DENSE,
            decision_source="test",
            fallback_used=False,
            router_enabled=True,
            confidence=1.0,
            reason_codes=["test_dense"],
            store_decision_trace=self.settings.router_store_decision_trace,
        )


class _RepeatingDensePlanner:
    def plan(self, request: LLMToolPlanningRequest) -> list[LLMToolCall]:
        if len(request.tool_results) >= 2:
            return []
        return [
            LLMToolCall(
                tool_name="dense_search",
                arguments={"query": "same repeated query"},
            )
        ]


class _HybridOnlyPlanner:
    def __init__(self) -> None:
        self.requests: list[LLMToolPlanningRequest] = []

    def plan(self, request: LLMToolPlanningRequest) -> list[LLMToolCall]:
        self.requests.append(request)
        return [LLMToolCall(tool_name="hybrid_search", arguments={"query": "hybrid only"})]


class _EmptyFinalizeAfterSearchPlanner:
    def plan(self, request: LLMToolPlanningRequest) -> list[LLMToolCall]:
        if not request.tool_results:
            return [LLMToolCall(tool_name="dense_search", arguments={"query": "dense once"})]
        return [
            LLMToolCall(
                tool_name="finalize_answer",
                arguments={"selected_tool_call_ids": []},
            )
        ]


class _SingleDensePlanner:
    def plan(self, request: LLMToolPlanningRequest) -> list[LLMToolCall]:
        return [LLMToolCall(tool_name="dense_search", arguments={"query": "dense once"})]


class _SequenceClock:
    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.index = 0

    def __call__(self) -> float:
        if self.index >= len(self.values):
            return self.values[-1]
        value = self.values[self.index]
        self.index += 1
        return value


class _FailingAnswerGenerator:
    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise AnswerGenerationError()


class _StaticAnswerGenerator:
    def __init__(self, content: str) -> None:
        self.content = content

    def generate(self, request: GenerationRequest) -> GenerationResult:
        return GenerationResult(content=self.content)


def _settings() -> Settings:
    return Settings(
        app_env="test",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        retrieval_top_k_default=2,
        retrieval_top_k_max=5,
        rerank_provider="fake",
        rerank_top_n_default=1,
        rerank_top_n_max=5,
        qdrant_collection_name="document_chunks",
        search_snippet_max_chars=32,
        generation_provider="fake",
    )


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
        ]
    )
    db.add_all(
        [
            _version(10, 10, "ready", True, "a", file_name="C:\\unsafe\\hand\tbook.pdf"),
            _version(20, 20, "ready", True, "b"),
        ]
    )
    db.add_all(
        [
            _chunk(
                100,
                10,
                "alpha policy full active chunk text should not be returned whole " * 4,
            ),
            _chunk(101, 10, "alpha secondary material " * 5, chunk_index=1, page_from=2),
            _chunk(200, 20, "alpha archived"),
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
) -> DocumentVersion:
    return DocumentVersion(
        document_version_id=document_version_id,
        logical_document_id=logical_document_id,
        version_no=1,
        content_hash=hash_prefix * 64,
        status=status,
        is_active=is_active,
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


def _assert_safe_run_trace(run: RetrievalRun, *, raw_query: str) -> None:
    assert run.query_plan_json is not None
    assert run.query_plan_json["schema_version"] == "phase2.trace.v1"
    assert run.query_plan_json["strategy_type"] == "dense"
    assert (
        run.query_plan_json["query_hash"] == hashlib.sha256(raw_query.encode("utf-8")).hexdigest()
    )
    assert run.strategy_decision_json is not None
    assert run.strategy_decision_json["selected_strategy"] == "dense"
    assert run.strategy_decision_json["router_enabled"] is False
    assert run.retrieval_settings_json is not None
    assert run.retrieval_settings_json["strategy_type"] == "dense"
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
    assert "full_context" not in dumped
