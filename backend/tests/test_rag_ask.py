from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, cast

import httpx
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
    TokenUsage,
)
from app.rag.langchain_agentic import (
    LangChainPlanningState,
    LangChainToolCall,
    LangChainToolResult,
    _plan_next_calls,
)
from app.rag.langgraph_agentic import LangGraphAgenticState, LangGraphToolCall, _plan_next_call
from app.rag.llm_orchestrator import (
    LLMToolCall,
    LLMToolCallingRetrievalOrchestrator,
    LLMToolPlanningRequest,
    OpenAICompatibleJSONToolPlanner,
    create_llm_tool_call_planner,
)
from app.rag.rerank import FakeRerankerClient, NoopRerankerClient, RerankCandidate, RerankError
from app.rag.retrieval import RetrievalError, RetrievalFilters, VectorSearchCandidate
from app.rag.strategy import RetrievalStrategy
from app.schemas.rag_strategy import RouterDecisionTrace
from app.services.rag_service import RagService, _retrieval_summary_response, _safe_generation_label

ALLOWED_ORIGIN = "http://localhost:5173"
TEST_PASSWORD = "password"
TEST_LMSTUDIO_MODEL = "qwen3.5-9b"
OBSERVED_CITED_ANSWER = "提供された文脈では、alpha policy の要点が確認できます [1]。"
OBSERVED_INSUFFICIENT_EVIDENCE_ANSWER = (
    "検索された文書には、この質問に答えるための十分な根拠がありません。 [1]"
)
INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER = (
    "検索された文書には、この質問に直接答えるための十分な根拠がありません [1]。"
)


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
        settings=_lmstudio_test_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
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
    assert first.usage is not None
    assert first.usage == second.usage
    assert first.usage.input_tokens is not None
    assert first.usage.input_tokens > 0
    assert first.usage.output_tokens is not None
    assert first.usage.output_tokens > 0
    assert first.usage.total_tokens == first.usage.input_tokens + first.usage.output_tokens
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


def test_generation_label_redacts_secret_like_values() -> None:
    assert _safe_generation_label("sk-test-secret-token-1234567890", max_length=128) == "redacted"
    assert _safe_generation_label("qwen3.5-9b", max_length=128) == "qwen3.5-9b"


def test_retrieval_summary_prefers_cached_graph_fallback_score_summary() -> None:
    run = RetrievalRun(
        retrieval_run_id=7,
        strategy_type=RetrievalStrategy.GRAPH.value,
        strategy_decision_json={
            "selected_strategy": "graph_neo4j",
            "execution_strategy": "hybrid",
            "fallback_used": False,
            "graph_requested_provider": "neo4j",
        },
        retrieval_score_summary={
            "fallback_used": True,
            "fallback_reason": "graph_no_evidence_fallback",
            "graph_store_provider": "neo4j",
            "graph_fallback_reason_codes": ["graph_no_evidence_fallback"],
        },
    )

    summary = _retrieval_summary_response(run)

    assert summary.fallback_used is True
    assert summary.fallback_reason == "graph_no_evidence_fallback"
    assert summary.graph_store_provider == "neo4j"
    assert summary.graph_fallback_reason_codes == ["graph_no_evidence_fallback"]


def test_retrieval_summary_does_not_report_success_graph_reason_as_fallback() -> None:
    run = RetrievalRun(
        retrieval_run_id=8,
        strategy_type=RetrievalStrategy.GRAPH.value,
        strategy_decision_json={
            "selected_strategy": "graph_postgres",
            "execution_strategy": "graph",
            "fallback_used": False,
            "graph_store_provider": "postgres",
        },
        retrieval_score_summary={
            "graph_store_provider": "postgres",
            "graph_reason_codes": ["graph_search_completed"],
            "graph_fallback_used": False,
        },
    )

    summary = _retrieval_summary_response(run)

    assert summary.fallback_used is False
    assert summary.fallback_reason is None
    assert summary.graph_store_provider == "postgres"
    assert summary.graph_fallback_reason_codes == []


@pytest.mark.parametrize(
    "provider_reason_code",
    ["neo4j_not_configured", "neo4j_connection_failed"],
)
def test_retrieval_summary_includes_graph_provider_failure_reason_codes(
    provider_reason_code: str,
) -> None:
    run = RetrievalRun(
        retrieval_run_id=9,
        strategy_type=RetrievalStrategy.GRAPH.value,
        strategy_decision_json={
            "selected_strategy": "graph_neo4j",
            "execution_strategy": "hybrid",
            "fallback_used": False,
            "graph_requested_provider": "neo4j",
        },
        retrieval_score_summary={
            "fallback_used": True,
            "fallback_reason": "graph_no_evidence_fallback",
            "graph_store_provider": "postgres",
            "graph_reason_codes": [
                provider_reason_code,
                "graph_no_evidence_fallback",
                "graph_fallback_hybrid",
            ],
        },
    )

    summary = _retrieval_summary_response(run)

    assert summary.fallback_used is True
    assert summary.fallback_reason == provider_reason_code
    assert summary.graph_requested_provider == "neo4j"
    assert summary.graph_store_provider == "postgres"
    assert provider_reason_code in summary.graph_fallback_reason_codes
    assert "graph_no_evidence_fallback" in summary.graph_fallback_reason_codes


def test_retrieval_summary_reads_graph_strategy_from_score_summary_when_trace_missing() -> None:
    run = RetrievalRun(
        retrieval_run_id=10,
        strategy_type=RetrievalStrategy.HYBRID.value,
        strategy_decision_json=None,
        retrieval_score_summary={
            "selected_strategy": "graph_neo4j",
            "execution_strategy": "hybrid",
            "fallback_used": True,
            "fallback_reason": "graph_no_evidence_fallback",
            "graph_store_provider": "postgres",
            "graph_reason_codes": [
                "neo4j_connection_failed",
                "graph_no_evidence_fallback",
                "graph_fallback_hybrid",
            ],
        },
    )

    summary = _retrieval_summary_response(run)

    assert summary.strategy_type == RetrievalStrategy.HYBRID
    assert summary.selected_strategy == "graph_neo4j"
    assert summary.execution_strategy == "hybrid"
    assert summary.graph_requested_provider == "neo4j"
    assert summary.graph_store_provider == "postgres"
    assert summary.fallback_used is True
    assert summary.fallback_reason == "neo4j_connection_failed"


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
    assert data["assistant_message"]["content"] == OBSERVED_CITED_ANSWER
    assert "[1]" in data["assistant_message"]["content"]
    assert data["citations"][0]["local_citation_id"] == 1
    assert data["citations"][0]["document_chunk_id"] == 100
    assert data["citations"][0]["source_label"] == "hand book.pdf"
    assert data["citations"][0]["old_version_flag"] is False
    assert 0.0 <= data["confidence"]["answer_confidence"] <= 1.0
    assert 0.0 <= data["confidence"]["groundedness_score"] <= 1.0
    assert data["confidence"]["confidence_label"] in {"High", "Medium", "Low"}
    assert data["confidence"]["confidence_basis"] == "retrieval_signals"
    assert data["retrieval_summary"] == {
        "retrieval_run_id": data["retrieval_run_id"],
        "strategy_type": "dense",
        "selected_strategy": "dense",
        "execution_strategy": "dense",
        "tools_used": [],
        "fallback_used": False,
        "fallback_reason": None,
        "graph_store_provider": None,
        "graph_requested_provider": None,
        "graph_fallback_reason_codes": [],
        "no_context": None,
    }
    generation = data["generation"]
    assert generation["provider"] == "lmstudio"
    assert generation["model"] == TEST_LMSTUDIO_MODEL
    assert generation["input_tokens"] > 0
    assert generation["output_tokens"] > 0
    assert generation["total_tokens"] == generation["input_tokens"] + generation["output_tokens"]
    assert generation["estimated_cost_usd"] == 0.0
    assert isinstance(generation["latency_ms"], int)
    assert generation["latency_ms"] >= 0
    assert (
        "full active chunk text should not be returned whole"
        not in data["assistant_message"]["content"]
    )
    assert len(data["citations"][0]["snippet"]) <= 240
    generation_dump = str(generation)
    assert "alpha policy summary" not in generation_dump
    assert "full active chunk text should not be returned whole" not in generation_dump
    assert "raw_prompt" not in generation_dump
    assert "raw_answer" not in generation_dump
    assert "raw_context" not in generation_dump
    assert "lm-studio" not in str(body).lower()
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
        assert retrieval_settings["generation_provider"] == "lmstudio"
        assert retrieval_settings["qdrant_collection"] == "document_chunks"
        assert run.latency_breakdown_json is not None
        assert "generation_ms" in run.latency_breakdown_json
        assert "citation_build_ms" in run.latency_breakdown_json
        assert "confidence_ms" in run.latency_breakdown_json
        assert run.context_budget_json is not None
        assert run.context_budget_json["schema_version"] == "phase2.context_budget.v1"
        assert run.context_budget_json["items"]["candidate_count"] == 2
        assert run.context_budget_json["items"]["selected_count"] == 1
        assert run.context_budget_json["items"]["dropped_count"] == 1
        assert run.context_budget_json["drop_reasons"] == {"not_selected_by_rerank": 1}
        assert run.context_budget_json["usage"]["estimated_context_tokens"] > 0
        budget_dump = str(run.context_budget_json)
        assert "full active chunk text should not be returned whole" not in budget_dump
        assert "raw_prompt" not in budget_dump
        assert "full_context" not in budget_dump
        assert run.context_compression_json is not None
        assert run.context_compression_json["schema_version"] == "phase2.context_compression.v1"
        assert run.context_compression_json["input"]["selected_context_items"] == 1
        assert run.context_compression_json["output"]["evidence_item_count"] == 1
        assert run.context_compression_json["output"]["evidence_group_count"] == 1
        assert run.context_compression_json["output"]["compression_ratio"] <= 1
        assert (
            run.context_compression_json["evidence_item_refs"][0]["retrieval_run_item_id"]
            == run.context_budget_json["selected_item_refs"][0]["retrieval_run_item_id"]
        )
        compression_dump = str(run.context_compression_json)
        assert "full active chunk text should not be returned whole" not in compression_dump
        assert "evidence_text_for_generation" not in compression_dump
        assert "raw_prompt" not in compression_dump
        assert "full_context" not in compression_dump
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
        assert run.context_budget_json["selected_item_refs"][0]["retrieval_run_item_id"] == (
            run_items[0].retrieval_run_item_id
        )
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
    assert replay.json()["data"]["generation"] is None
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


def test_rag_ask_context_budget_finalizes_trace_after_context_assembly(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings(
        generation_max_context_chars=130,
        context_budget_max_context_tokens=1000,
        context_budget_reserve_answer_tokens=0,
        context_budget_max_tokens_per_item=1000,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=NoopRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="budget char cap")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "budget-char-cap-msg",
            "message": "alpha policy summary",
            "top_k": 2,
            "rerank_top_n": 2,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["citations"][0]["document_chunk_id"] == 100

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.context_budget_json is not None
        assert run.context_budget_json["selected_item_refs"][0]["document_chunk_id"] == 100
        assert run.context_budget_json["dropped_item_refs"][0]["document_chunk_id"] == 101
        assert run.context_budget_json["drop_reasons"] == {"over_budget": 1}
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 1
        assert run.context_compression_json["drops"] == {"max_total_chars": 1}
        assert run.context_compression_json["evidence_item_refs"][0]["document_chunk_id"] == 100
        assert run.retrieval_score_summary is not None
        assert run.retrieval_score_summary["selected_count"] == 1
        assert run.retrieval_score_summary["top1_retrieval_score"] == 0.91
        assert run.retrieval_score_summary["top1_rerank_score"] == 0.91
        assert str(run.rerank_score_top1) == "0.910000"
        items = (
            db.query(RetrievalRunItem)
            .filter_by(retrieval_run_id=run.retrieval_run_id)
            .order_by(RetrievalRunItem.rank_order.asc())
            .all()
        )
        assert [item.selected_flag for item in items] == [True, False]


def test_rag_ask_evidence_pack_disabled_bypasses_evidence_caps(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings(
        evidence_pack_enabled=False,
        evidence_pack_max_items=1,
        evidence_pack_max_items_per_source=1,
        evidence_pack_max_chars_per_item=20,
        evidence_pack_max_total_chars=20,
        generation_max_context_chars=1000,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=NoopRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="evidence disabled")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "evidence-disabled-msg",
            "message": "alpha policy summary",
            "top_k": 2,
            "rerank_top_n": 2,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.context_budget_json is not None
        assert run.context_budget_json["items"]["selected_count"] == 2
        assert run.context_compression_json is not None
        assert run.context_compression_json["enabled"] is False
        assert run.context_compression_json["policy"]["max_total_chars"] == 1000
        assert run.context_compression_json["output"]["evidence_item_count"] == 2
        assert run.context_compression_json["output"]["output_char_count"] > 20
        assert run.context_compression_json["drops"] == {}


def test_rag_ask_context_budget_drop_all_saves_safe_failed_trace(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, session_factory, vector_client = rag_ask_client
    caplog.set_level("INFO", logger="app.services.rag_service")
    settings = _lmstudio_test_settings(
        context_budget_max_context_tokens=1,
        context_budget_reserve_answer_tokens=0,
        context_budget_max_tokens_per_item=1,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="budget ask")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "budget-msg-1",
            "message": "alpha policy summary",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    budget_logs = [
        record for record in caplog.records if record.getMessage() == "rag.context_budget.exhausted"
    ]
    assert budget_logs
    log_payload = budget_logs[-1].__dict__["rag_context_budget"]
    assert log_payload["selected_count"] == 0
    assert log_payload["budget_exhausted"] is True
    assert "raw" not in str(log_payload).lower()
    assert "secret" not in str(log_payload).lower()
    evidence_logs = [
        record for record in caplog.records if record.getMessage() == "rag.evidence_pack.built"
    ]
    assert evidence_logs
    evidence_payload = evidence_logs[-1].__dict__["rag_evidence_pack"]
    assert evidence_payload["input_item_count"] == 0
    assert evidence_payload["output_item_count"] == 0
    assert "raw" not in str(evidence_payload).lower()
    assert "secret" not in str(evidence_payload).lower()
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "no_context_found"
        assert run.context_budget_json is not None
        assert run.context_budget_json["items"]["candidate_count"] == 2
        assert run.context_budget_json["items"]["selected_count"] == 0
        assert run.context_budget_json["drop_reasons"]["over_budget"] == 2
        dumped = str(run.context_budget_json)
        assert "full active chunk text should not be returned whole" not in dumped
        assert "raw_prompt" not in dumped
        assert run.context_compression_json is not None
        assert run.context_compression_json["input"]["candidate_context_items"] == 2
        assert run.context_compression_json["input"]["selected_context_items"] == 0
        assert run.context_compression_json["output"]["evidence_item_count"] == 0


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
        assert run.context_budget_json is not None
        assert run.context_budget_json["strategy"]["strategy_type"] == "agentic_router"
        assert run.context_budget_json["items"]["selected_count"] == 1
        assert run.context_budget_json["usage"]["estimated_context_tokens"] > 0
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 1
        assert run.context_compression_json["evidence_item_refs"][0]["retrieval_source"] == "hybrid"
        items = db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        assert items
        assert all(item.retrieval_source == "hybrid" for item in items)
        dumped = str({"query_plan": run.query_plan_json, "decision": run.strategy_decision_json})
        assert message not in dumped
        assert "raw_prompt" not in dumped
        assert "raw_chunk" not in dumped
        assert "content_text" not in dumped


def test_rag_ask_agentic_router_uses_llm_planner_after_insufficient_context(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            '{"action":"retrieve","strategy":"dense","confidence":0.7,'
            '"reason_codes":["planner_initial_dense"]}',
            '{"action":"retrieve","strategy":"hybrid","confidence":0.8,'
            '"reason_codes":["planner_after_low_score"]}',
        ]
    )
    captured_models: list[str] = []

    class Response:
        status_code = 200

        def __init__(self, content: str) -> None:
            self.content = content

        def json(self) -> dict[str, Any]:
            return {"choices": [{"message": {"content": self.content}}]}

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> Response:
        captured_models.append(str(json["model"]))
        return Response(next(responses))

    class StepVectorClient(_StaticVectorClient):
        def __init__(self) -> None:
            super().__init__([_candidate(100, 0.91, 1), _candidate(101, 0.82, 2)])
            self.search_calls = 0

        def search(
            self,
            *,
            collection_name: str,
            query_vector: Sequence[float],
            limit: int,
            filters: RetrievalFilters,
        ) -> list[VectorSearchCandidate]:
            self.search_calls += 1
            if self.search_calls == 1:
                self.query_vectors.append([float(value) for value in query_vector])
                return []
            return super().search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=limit,
                filters=filters,
            )

    monkeypatch.setattr("app.rag.agentic_planner.httpx.post", fake_post)
    client, session_factory, _ = rag_ask_client
    vector_client = StepVectorClient()
    settings = _settings(
        graph_retrieval_enabled=False,
        router_mode="llm",
        generation_provider="lmstudio",
        generation_model_name="qwen3.5-9b",
        router_llm_planner_model_name="qwen3.5-4b",
        router_sufficiency_top_score_threshold=0.2,
        router_max_retrieval_calls=2,
        router_max_fallback_calls=1,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="agentic llm ask")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "agentic-llm-fallback-msg-1",
            "message": "alpha policy",
            "strategy": "agentic_router",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert captured_models == ["qwen3.5-4b", "qwen3.5-4b"]
    assert vector_client.search_calls >= 2
    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.strategy_decision_json is not None
        decision = run.strategy_decision_json
        assert decision["selected_strategy"] == "dense"
        assert decision["execution_strategy"] == "dense"
        assert decision["decision_source"] == "llm_planner"
        assert decision["retrieval_call_count"] == 2
        assert decision["fallback_used"] is True
        assert decision["fallback_strategy"] == "hybrid"
        assert decision["llm_planner_used"] is True
        assert decision["planner_selected_strategy"] == "hybrid"
        assert decision["planner_reason_codes"] == ["planner_after_low_score"]
        assert [event["phase"] for event in decision["planner_events"]] == [
            "initial",
            "fallback",
        ]


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
        assert run.context_budget_json is not None
        assert run.context_budget_json["strategy"]["strategy_type"] == "hybrid"
        assert run.context_budget_json["items"]["selected_count"] == 1
        assert run.context_budget_json["usage"]["estimated_context_tokens"] > 0
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 1
        assert run.context_compression_json["evidence_item_refs"][0]["retrieval_source"] == "hybrid"
        items = db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        assert items
        assert all(item.retrieval_source == "hybrid" for item in items)


@pytest.mark.parametrize(
    ("strategy", "client_message_id"),
    [
        ("dense", "dense-retry-insufficient-high-support"),
        ("hybrid", "hybrid-retry-insufficient-high-support"),
    ],
)
def test_rag_ask_retries_once_on_insufficient_answer_when_support_is_high(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
    strategy: str,
    client_message_id: str,
) -> None:
    client, session_factory, vector_client = rag_ask_client
    answer_generator = _HedgeThenCitationAnswerGenerator()
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=_lmstudio_test_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=answer_generator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title=f"{strategy} retry hedge")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": client_message_id,
            "message": "alpha policy retry generated answer",
            "strategy": strategy,
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] == OBSERVED_CITED_ANSWER
    assert data["assistant_message"]["content"] != INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER
    assert data["confidence"]["confidence_label"] != "Low"
    assert data["generation"]["total_tokens"] == 40
    assert answer_generator.call_count == 2
    assert answer_generator.requests[0].system_instructions is None
    retry_instructions = answer_generator.requests[1].system_instructions
    assert retry_instructions is not None
    assert "Do not use the insufficient-evidence sentence" in retry_instructions
    assert answer_generator.requests[1].temperature == 0.0

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.confidence_label != "Low"
        assert run.latency_breakdown_json is not None
        assert run.latency_breakdown_json["generation_retry_count"] == 1
        assert run.latency_breakdown_json["generation_retry_ms"] >= 0
        assert "content_text" not in str(run.latency_breakdown_json)
        assert "alpha policy full active chunk text" not in str(run.latency_breakdown_json)


def test_rag_ask_retry_instruction_preserves_explicit_language_request(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    answer_generator = _SequenceAnswerGenerator(
        [
            OBSERVED_INSUFFICIENT_EVIDENCE_ANSWER,
            "The alpha policy is confirmed by the handbook [1].",
        ]
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=_lmstudio_test_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=answer_generator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="english retry hedge")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "dense-retry-explicit-english",
            "message": "Please answer in English: what is the alpha policy?",
            "strategy": "dense",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] == (
        "The alpha policy is confirmed by the handbook [1]."
    )
    assert data["assistant_message"]["content"] != INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER
    assert data["confidence"]["confidence_label"] != "Low"
    assert answer_generator.call_count == 2
    retry_instructions = answer_generator.requests[1].system_instructions
    assert retry_instructions is not None
    assert (
        "Answer in Japanese unless the user explicitly asks for another language"
        in retry_instructions
    )
    assert "Answer in Japanese using only" not in retry_instructions

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.confidence_label != "Low"
        assert run.latency_breakdown_json is not None
        assert run.latency_breakdown_json["generation_retry_count"] == 1


@pytest.mark.parametrize(
    ("retry_content", "client_message_id"),
    [
        ("The retry cites a marker that was not displayed [99].", "dense-retry-bad-marker"),
        ("answer leaks password=verysecret [1]", "dense-retry-sensitive-output"),
    ],
)
def test_rag_ask_retry_validation_errors_degrade_to_low_confidence_citations(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
    retry_content: str,
    client_message_id: str,
) -> None:
    client, session_factory, vector_client = rag_ask_client
    answer_generator = _SequenceAnswerGenerator(
        [OBSERVED_INSUFFICIENT_EVIDENCE_ANSWER, retry_content]
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=_lmstudio_test_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=answer_generator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title=client_message_id)

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": client_message_id,
            "message": "alpha policy retry validation fallback",
            "strategy": "dense",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] == INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER
    assert data["assistant_message"]["content"] != retry_content
    assert data["citations"][0]["local_citation_id"] == 1
    assert data["confidence"]["confidence_label"] == "Low"
    assert data["generation"]["total_tokens"] == 40
    assert answer_generator.call_count == 2
    assert "verysecret" not in str(response.json())

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.error_code is None
        assert run.confidence_label == "Low"
        assert run.latency_breakdown_json is not None
        assert run.latency_breakdown_json["generation_retry_count"] == 1
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 1
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user", "assistant"]


def test_rag_ask_does_not_retry_insufficient_answer_when_support_is_low(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    vector_client.candidates = [_candidate(100, 0.1, 1)]
    answer_generator = _HedgeThenCitationAnswerGenerator()
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=_lmstudio_test_settings(),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=NoopRerankerClient(),
        answer_generator=answer_generator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="dense weak support")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "dense-no-retry-insufficient-low-support",
            "message": "alpha policy weak support generated answer",
            "strategy": "dense",
            "top_k": 1,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] == INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER
    assert data["confidence"]["confidence_label"] == "Low"
    assert answer_generator.call_count == 1

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.confidence_label == "Low"
        assert run.retrieval_score_summary is not None
        assert run.retrieval_score_summary["top1_retrieval_score"] == 0.1
        assert run.retrieval_score_summary["top1_rerank_score"] == 0.1
        assert run.latency_breakdown_json is not None
        assert "generation_retry_count" not in run.latency_breakdown_json
        assert "generation_retry_ms" not in run.latency_breakdown_json


def test_rag_ask_retry_flag_off_preserves_insufficient_fallback(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    answer_generator = _HedgeThenCitationAnswerGenerator()
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=_lmstudio_test_settings(generation_retry_on_insufficient_evidence=False),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=answer_generator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="retry off")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "dense-retry-disabled",
            "message": "alpha policy retry disabled",
            "strategy": "dense",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] == INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER
    assert data["confidence"]["confidence_label"] == "Low"
    assert answer_generator.call_count == 1

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.confidence_label == "Low"
        assert run.latency_breakdown_json is not None
        assert "generation_retry_count" not in run.latency_breakdown_json


@pytest.mark.parametrize(
    ("strategy", "client_message_id"),
    [
        ("dense", "dense-insufficient-with-context"),
        ("hybrid", "hybrid-insufficient-with-context"),
    ],
)
def test_rag_ask_with_context_and_insufficient_answer_returns_low_confidence_citations(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
    strategy: str,
    client_message_id: str,
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings()
    answer_generator = _ObservedInsufficientEvidenceAnswerGenerator()
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=answer_generator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title=f"{strategy} weak answer")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": client_message_id,
            "message": "alpha policy weak generated answer",
            "strategy": strategy,
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] != OBSERVED_INSUFFICIENT_EVIDENCE_ANSWER
    assert data["assistant_message"]["content"] == INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER
    assert data["citations"][0]["local_citation_id"] == 1
    assert data["citations"][0]["document_chunk_id"] == 100
    assert data["confidence"]["confidence_label"] == "Low"
    assert data["confidence"]["answer_confidence"] < settings.confidence_medium_threshold
    assert "content_text" not in str(response.json())
    assert answer_generator.call_count == 2

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.error_code is None
        assert run.strategy_type == strategy
        assert run.confidence_label == "Low"
        assert run.latency_breakdown_json is not None
        assert run.latency_breakdown_json["generation_retry_count"] == 1
        assert run.latency_breakdown_json["generation_retry_ms"] >= 0
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 1
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 1
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user", "assistant"]


@pytest.mark.integration
def test_rag_ask_live_lmstudio_generation_does_not_fall_back_to_fake(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    if os.getenv("RAG_LIVE_LLM") != "1":
        pytest.skip("set RAG_LIVE_LLM=1 to run the LM Studio integration test")

    client, session_factory, vector_client = rag_ask_client
    settings = _live_lmstudio_settings()
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="live lmstudio insufficient")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "live-lmstudio-insufficient-msg-1",
            "message": (
                "この文書だけに基づいて、Mercury 計画の打ち上げ番号を答えてください。"
                "文書に直接根拠がない場合は"
                "「検索された文書には、この質問に答えるための十分な根拠がありません」"
                "と答えてください。"
            ),
            "strategy": "dense",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] != OBSERVED_INSUFFICIENT_EVIDENCE_ANSWER
    assert data["generation"]["provider"] == "lmstudio"
    assert data["generation"]["provider"] != "fake"
    assert data["citations"][0]["local_citation_id"] == 1
    assert data["citations"][0]["document_chunk_id"] == 100
    if data["assistant_message"]["content"] == INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER:
        assert data["confidence"]["confidence_label"] == "Low"
        assert data["confidence"]["answer_confidence"] < settings.confidence_medium_threshold
    else:
        assert data["confidence"]["confidence_label"] in {"High", "Medium", "Low"}
    assert "content_text" not in str(response.json())

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.error_code is None
        assert run.confidence_label == data["confidence"]["confidence_label"]
        assert run.retrieval_settings_json is not None
        assert run.retrieval_settings_json["generation_provider"] == "lmstudio"
        assert run.retrieval_settings_json["generation_provider"] != "fake"
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 1


@pytest.mark.parametrize(
    ("strategy", "client_message_id"),
    [
        ("dense", "dense-empty-retrieval"),
        ("hybrid", "hybrid-empty-retrieval"),
    ],
)
def test_rag_ask_true_empty_retrieval_returns_no_context(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
    strategy: str,
    client_message_id: str,
) -> None:
    client, session_factory, vector_client = rag_ask_client
    vector_client.candidates = []
    settings = _lmstudio_test_settings(hybrid_sparse_weight=0.0, hybrid_dense_weight=1.0)
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title=f"{strategy} empty")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": client_message_id,
            "message": "alpha policy no retrievable context",
            "strategy": strategy,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "failed"
        assert run.error_code == "no_context_found"
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user"]


def test_rag_ask_llm_tool_orchestrator_uses_bounded_tools_and_saves_safe_trace(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_HybridThenFinalizePlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
        llm_tool_orchestrator=orchestrator,
    )
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
        assert run.strategy_decision_json["retrieval_call_count"] == 1
        assert run.strategy_decision_json["fallback_used"] is False
        assert run.strategy_decision_json["finalize_called"] is True
        assert run.strategy_decision_json["no_context"] is False
        assert run.latency_breakdown_json is not None
        assert "llm_orchestrator_ms" in run.latency_breakdown_json
        assert "generation_ms" in run.latency_breakdown_json
        assert run.context_budget_json is not None
        assert run.context_budget_json["strategy"]["strategy_type"] == "llm_tool_orchestrator"
        assert run.context_budget_json["strategy"]["selected_strategy"] == "llm_tool_orchestrator"
        assert run.context_budget_json["items"]["selected_count"] == 1
        assert run.context_budget_json["usage"]["estimated_context_tokens"] > 0
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 1
        assert run.context_compression_json["evidence_item_refs"][0]["retrieval_source"] == "hybrid"
        assert run.tool_result_compression_json is not None
        assert (
            run.tool_result_compression_json["schema_version"]
            == "phase2.tool_result_compression.v1"
        )
        assert run.tool_result_compression_json["summary"]["search_tool_call_count"] == 1
        assert run.tool_result_compression_json["summary"]["output_item_count"] >= 1
        assert run.tool_result_compression_json["item_refs"][0]["retrieval_run_item_id"] is not None
        compression_dump = json.dumps(run.tool_result_compression_json, sort_keys=True)
        assert '"snippet":' not in compression_dump
        assert "full active chunk text" not in compression_dump
        settings_snapshot = run.retrieval_settings_json
        assert settings_snapshot is not None
        assert settings_snapshot["max_tool_calls"] == 8
        assert settings_snapshot["max_search_calls"] == 8
        assert settings_snapshot["timeout_seconds"] == 600.0
        assert settings_snapshot["tool_result_compression_enabled"] is True
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
                "tool_result_compression": run.tool_result_compression_json,
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


def test_rag_ask_langchain_agentic_uses_langchain_tools_and_saves_safe_trace(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="langchain agentic ask")
    message = "Compare alpha secondary retrieval evidence"

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "langchain-agentic-msg-1",
            "message": message,
            "strategy": "langchain_agentic",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "[1]" in data["assistant_message"]["content"]
    assert data["retrieval_summary"]["strategy_type"] == "langchain_agentic"
    assert data["retrieval_summary"]["selected_strategy"] == "langchain_agentic"
    assert "content_text" not in str(response.json())
    assert len(vector_client.query_vectors) == 1

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.strategy_type == "langchain_agentic"
        assert run.query_plan_json is not None
        assert run.query_plan_json["strategy_type"] == "langchain_agentic"
        assert run.query_plan_json["query_mode"] == "langchain_agentic_retrieval"
        assert "analysis" in run.query_plan_json
        assert "planner" in run.query_plan_json
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["selected_strategy"] == "langchain_agentic"
        assert run.strategy_decision_json["execution_strategy"] == "langchain_agentic"
        assert run.strategy_decision_json["orchestrator_provider"] == "langchain"
        assert run.strategy_decision_json["tool_call_count"] == 2
        assert run.strategy_decision_json["search_call_count"] == 1
        assert run.strategy_decision_json["retrieval_call_count"] == 1
        assert run.strategy_decision_json["fallback_used"] is False
        assert run.strategy_decision_json["finalize_called"] is True
        assert run.strategy_decision_json["no_context"] is False
        assert "langchain_runnable_planner" in run.strategy_decision_json["reason_codes"]
        assert "langchain_structured_tools" in run.strategy_decision_json["reason_codes"]
        assert run.latency_breakdown_json is not None
        assert "langchain_agentic_ms" in run.latency_breakdown_json
        assert "langchain_planning_ms" in run.latency_breakdown_json
        assert "langchain_tool_execution_ms" in run.latency_breakdown_json
        assert "generation_ms" in run.latency_breakdown_json
        assert run.context_budget_json is not None
        assert run.context_budget_json["strategy"]["strategy_type"] == "langchain_agentic"
        assert run.context_budget_json["strategy"]["selected_strategy"] == "langchain_agentic"
        assert run.context_budget_json["items"]["selected_count"] == 1
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 1
        assert run.context_compression_json["evidence_item_refs"][0]["retrieval_source"] == "hybrid"
        assert run.tool_result_compression_json is not None
        assert (
            run.tool_result_compression_json["schema_version"]
            == "phase2.tool_result_compression.v1"
        )
        assert run.tool_result_compression_json["summary"]["search_tool_call_count"] == 1
        assert run.tool_result_compression_json["summary"]["output_item_count"] >= 1
        settings_snapshot = run.retrieval_settings_json
        assert settings_snapshot is not None
        assert settings_snapshot["orchestrator_provider"] == "langchain"
        assert settings_snapshot["langchain_agentic_enabled"] is True
        assert settings_snapshot["max_tool_calls"] == 8
        assert settings_snapshot["max_search_calls"] == 8
        assert settings_snapshot["timeout_seconds"] == 600.0
        items = db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        assert items
        assert all(item.retrieval_source == "hybrid" for item in items)
        assert items[0].score_breakdown_json is not None
        assert items[0].score_breakdown_json["retrieval_source"] == "langchain_agentic"
        dumped = str(
            {
                "query_plan": run.query_plan_json,
                "decision": run.strategy_decision_json,
                "settings": run.retrieval_settings_json,
                "summary": run.retrieval_score_summary,
                "tool_result_compression": run.tool_result_compression_json,
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


def test_rag_ask_langchain_agentic_insufficient_answer_with_context_returns_citations(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings()
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedInsufficientEvidenceAnswerGenerator(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="langchain insufficient")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "langchain-insufficient-msg-1",
            "message": "Compare alpha secondary retrieval evidence",
            "strategy": "langchain_agentic",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] == INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER
    assert data["citations"][0]["document_chunk_id"] in {100, 101}
    assert data["confidence"]["confidence_label"] == "Low"
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "succeeded"
        assert run.error_code is None
        assert run.confidence_label == "Low"
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 1


def test_rag_ask_langgraph_agentic_uses_state_graph_and_saves_safe_trace(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="langgraph agentic ask")
    message = "Compare alpha secondary retrieval evidence"

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "langgraph-agentic-msg-1",
            "message": message,
            "strategy": "langgraph_agentic",
            "top_k": 2,
            "rerank_top_n": 1,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "[1]" in data["assistant_message"]["content"]
    assert data["retrieval_summary"]["strategy_type"] == "langgraph_agentic"
    assert data["retrieval_summary"]["selected_strategy"] == "langgraph_agentic"
    assert "content_text" not in str(response.json())
    assert len(vector_client.query_vectors) == 1

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.status == "succeeded"
        assert run.strategy_type == "langgraph_agentic"
        assert run.query_plan_json is not None
        assert run.query_plan_json["strategy_type"] == "langgraph_agentic"
        assert run.query_plan_json["query_mode"] == "langgraph_agentic_retrieval"
        assert "analysis" in run.query_plan_json
        assert "planner" in run.query_plan_json
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["selected_strategy"] == "langgraph_agentic"
        assert run.strategy_decision_json["execution_strategy"] == "langgraph_agentic"
        assert run.strategy_decision_json["orchestrator_provider"] == "langgraph"
        assert run.strategy_decision_json["tool_call_count"] == 2
        assert run.strategy_decision_json["search_call_count"] == 1
        assert run.strategy_decision_json["retrieval_call_count"] == 1
        assert run.strategy_decision_json["fallback_used"] is False
        assert run.strategy_decision_json["finalize_called"] is True
        assert run.strategy_decision_json["no_context"] is False
        assert run.strategy_decision_json["graph_node_count"] >= 4
        assert run.strategy_decision_json["graph_transition_count"] >= 2
        assert "langgraph_state_graph" in run.strategy_decision_json["reason_codes"]
        assert "langgraph_plan_execute_nodes" in run.strategy_decision_json["reason_codes"]
        assert run.latency_breakdown_json is not None
        assert "langgraph_agentic_ms" in run.latency_breakdown_json
        assert "langgraph_planning_ms" in run.latency_breakdown_json
        assert "langgraph_tool_execution_ms" in run.latency_breakdown_json
        assert "generation_ms" in run.latency_breakdown_json
        assert run.context_budget_json is not None
        assert run.context_budget_json["strategy"]["strategy_type"] == "langgraph_agentic"
        assert run.context_budget_json["strategy"]["selected_strategy"] == "langgraph_agentic"
        assert run.context_budget_json["items"]["selected_count"] == 1
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 1
        assert run.context_compression_json["evidence_item_refs"][0]["retrieval_source"] == "hybrid"
        assert run.tool_result_compression_json is not None
        assert (
            run.tool_result_compression_json["schema_version"]
            == "phase2.tool_result_compression.v1"
        )
        assert run.tool_result_compression_json["summary"]["search_tool_call_count"] == 1
        assert run.tool_result_compression_json["summary"]["output_item_count"] >= 1
        settings_snapshot = run.retrieval_settings_json
        assert settings_snapshot is not None
        assert settings_snapshot["orchestrator_provider"] == "langgraph"
        assert settings_snapshot["langgraph_agentic_enabled"] is True
        assert settings_snapshot["max_tool_calls"] == 8
        assert settings_snapshot["max_search_calls"] == 8
        assert settings_snapshot["timeout_seconds"] == 600.0
        items = db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        assert items
        assert all(item.retrieval_source == "hybrid" for item in items)
        assert items[0].score_breakdown_json is not None
        assert items[0].score_breakdown_json["retrieval_source"] == "langgraph_agentic"
        dumped = str(
            {
                "query_plan": run.query_plan_json,
                "decision": run.strategy_decision_json,
                "settings": run.retrieval_settings_json,
                "summary": run.retrieval_score_summary,
                "tool_result_compression": run.tool_result_compression_json,
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


def test_langchain_agentic_planner_tries_alternate_tools_after_empty_searches() -> None:
    query = "alpha beta gamma delta epsilon zeta retrieval evidence"
    available_tools = ("dense_search", "sparse_search", "hybrid_search", "finalize_answer")
    first_call = _plan_next_calls(
        LangChainPlanningState(
            user_query=query,
            max_query_chars=200,
            remaining_tool_calls=4,
            remaining_search_calls=4,
            available_tools=available_tools,
            tool_results=[],
        )
    )

    assert first_call == [LangChainToolCall(tool_name="hybrid_search", arguments={"query": query})]

    second_call = _plan_next_calls(
        LangChainPlanningState(
            user_query=query,
            max_query_chars=200,
            remaining_tool_calls=3,
            remaining_search_calls=3,
            available_tools=available_tools,
            tool_results=[
                LangChainToolResult(
                    tool_call_id="lc_1",
                    tool_name="hybrid_search",
                    status="succeeded",
                    item_count=0,
                    normalized_query=query,
                )
            ],
        )
    )

    assert second_call == [LangChainToolCall(tool_name="sparse_search", arguments={"query": query})]

    third_call = _plan_next_calls(
        LangChainPlanningState(
            user_query=query,
            max_query_chars=200,
            remaining_tool_calls=2,
            remaining_search_calls=2,
            available_tools=available_tools,
            tool_results=[
                LangChainToolResult(
                    tool_call_id="lc_1",
                    tool_name="hybrid_search",
                    status="succeeded",
                    item_count=0,
                    normalized_query=query,
                ),
                LangChainToolResult(
                    tool_call_id="lc_2",
                    tool_name="sparse_search",
                    status="succeeded",
                    item_count=0,
                    normalized_query=query,
                ),
            ],
        )
    )

    assert third_call == [LangChainToolCall(tool_name="dense_search", arguments={"query": query})]


def test_langgraph_agentic_planner_tries_alternate_tools_after_empty_searches() -> None:
    query = "alpha beta gamma delta epsilon zeta retrieval evidence"
    available_tools = ("dense_search", "sparse_search", "hybrid_search", "finalize_answer")
    base_state: LangGraphAgenticState = {
        "user_query": query,
        "max_query_chars": 200,
        "max_tool_calls": 4,
        "max_search_calls": 4,
        "started_at": 0.0,
        "timeout_seconds": 600.0,
        "available_tools": available_tools,
        "tool_results": [],
        "attempts_by_tool_call_id": {},
        "selected_tool_call_ids": [],
        "seen_searches": set(),
        "tool_call_count": 0,
        "search_call_count": 0,
        "timeout_exceeded": False,
        "repeated_query_detected": False,
        "finalize_called": False,
        "stop_requested": False,
        "reason_codes": [],
    }

    assert _plan_next_call(base_state) == LangGraphToolCall(
        tool_name="hybrid_search",
        arguments={"query": query},
    )
    assert _plan_next_call(
        {
            **base_state,
            "tool_results": [
                LangChainToolResult(
                    tool_call_id="lg_1",
                    tool_name="hybrid_search",
                    status="succeeded",
                    item_count=0,
                    normalized_query=query,
                )
            ],
        }
    ) == LangGraphToolCall(tool_name="sparse_search", arguments={"query": query})
    assert _plan_next_call(
        {
            **base_state,
            "tool_results": [
                LangChainToolResult(
                    tool_call_id="lg_1",
                    tool_name="hybrid_search",
                    status="succeeded",
                    item_count=0,
                    normalized_query=query,
                ),
                LangChainToolResult(
                    tool_call_id="lg_2",
                    tool_name="sparse_search",
                    status="succeeded",
                    item_count=0,
                    normalized_query=query,
                ),
            ],
        }
    ) == LangGraphToolCall(tool_name="dense_search", arguments={"query": query})


def test_rag_ask_hybrid_disabled_returns_strategy_not_enabled(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings(
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


def test_rag_ask_llm_tool_orchestrator_budget_exhausted_best_effort_finalizes_with_candidates(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings(
        sparse_enabled=False,
        hybrid_enabled=False,
        llm_orchestrator_max_tool_calls=1,
        llm_orchestrator_max_search_calls=1,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
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

    assert response.status_code == 200
    data = response.json()["data"]
    assert "[1]" in data["assistant_message"]["content"]
    with session_factory() as db:
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user", "assistant"]
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "succeeded"
        assert run.error_code is None
        assert run.strategy_type == "llm_tool_orchestrator"
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["budget_exhausted"] is True
        assert run.strategy_decision_json["finalize_called"] is False
        assert run.strategy_decision_json["best_effort_finalize_used"] is True
        assert run.strategy_decision_json["no_context"] is False
        assert (
            "best_effort_finalize_after_budget_or_timeout"
            in run.strategy_decision_json["reason_codes"]
        )
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() > 0
        )


def test_rag_ask_llm_tool_orchestrator_budget_exhausted_without_candidates_returns_no_context(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    vector_client.candidates = []
    settings = _lmstudio_test_settings(
        sparse_enabled=False,
        hybrid_enabled=False,
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
    chat_session_id = _create_chat_session(client, csrf_token, title="llm empty budget")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-empty-budget-msg-1",
            "message": "alpha policy budget empty",
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
        assert run.strategy_decision_json["best_effort_finalize_used"] is True
        assert run.strategy_decision_json["no_context"] is True


def test_rag_ask_llm_tool_orchestrator_oversized_tool_output_rejected_safely(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings(
        tool_result_compression_max_snippet_chars=100,
        tool_result_compression_max_tokens_per_tool=1,
        tool_result_compression_max_total_tool_result_tokens=1,
        llm_orchestrator_max_tool_calls=1,
        llm_orchestrator_max_search_calls=1,
    )
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_SingleDensePlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm oversized")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-oversized-msg-1",
            "message": "alpha oversized",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_context_found"
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        compression_trace = run.tool_result_compression_json
        assert compression_trace is not None
        assert compression_trace["summary"]["oversized_rejected_count"] == 1
        assert compression_trace["drop_reasons"]["oversized_rejected"] >= 1
        strategy_decision = run.strategy_decision_json
        assert strategy_decision is not None
        assert strategy_decision["tool_results"][0]["error_code"] == ("oversized_tool_output")
        dumped = json.dumps(compression_trace, sort_keys=True)
        assert '"snippet":' not in dumped
        assert "full active chunk text" not in dumped


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
    settings = _lmstudio_test_settings(
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
        answer_generator=_ObservedCitationAnswerGenerator(),
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
    settings = _lmstudio_test_settings()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_EmptyFinalizeAfterSearchPlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
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
    system_payload = captured["json"]["messages"][0]["content"]
    assert captured["json"]["model"] == "qwen3.5-9b"
    assert captured["json"]["max_tokens"] == 256
    assert "Do not write analysis" in system_payload
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
    settings = _lmstudio_test_settings(
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
        answer_generator=_ObservedCitationAnswerGenerator(),
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


def test_rag_ask_llm_tool_orchestrator_repeated_query_best_effort_finalizes_with_candidates(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_RepeatingDensePlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
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

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["assistant_message"]["role"] == "assistant"
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["repeated_query_detected"] is True
        assert run.strategy_decision_json["search_call_count"] == 1
        assert run.strategy_decision_json["finalize_called"] is False
        assert run.strategy_decision_json["best_effort_finalize_used"] is True
        assert run.strategy_decision_json["no_context"] is False
        assert (
            "best_effort_finalize_after_repeated_query"
            in run.strategy_decision_json["reason_codes"]
        )
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 2
        assert db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count()
        dumped = str(run.strategy_decision_json)
        assert "alpha repeat" not in dumped
        assert "full active chunk text" not in dumped


def test_rag_ask_llm_tool_orchestrator_repeated_tool_result_is_traced(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_DenseDifferentQueriesThenNoToolPlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm repeated result")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-repeated-result-msg-1",
            "message": "alpha repeated result",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    assert len(vector_client.query_vectors) >= 1
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        compression_trace = run.tool_result_compression_json
        assert compression_trace is not None
        assert compression_trace["summary"]["search_tool_call_count"] == 2
        assert compression_trace["summary"]["repeated_result_count"] == 1
        assert compression_trace["drop_reasons"] == {"repeated_result": 2}
        strategy_decision = run.strategy_decision_json
        assert strategy_decision is not None
        assert "repeated_tool_result_detected" in strategy_decision["reason_codes"]


def test_rag_ask_llm_tool_orchestrator_compression_disabled_preserves_final_context(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings(
        tool_result_compression_enabled=False,
        tool_result_compression_max_items_per_tool=1,
        tool_result_compression_max_total_items_per_turn=1,
        llm_orchestrator_max_tool_result_items=1,
    )
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_DenseThenNoToolPlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=NoopRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm compression disabled")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-compression-disabled-msg-1",
            "message": "alpha policy compression disabled",
            "strategy": "llm_tool_orchestrator",
            "top_k": 2,
            "rerank_top_n": 2,
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["tool_result_compression_enabled"] is False
        assert "tool_result_compression_skipped" in run.strategy_decision_json["reason_codes"]
        assert "tool_result_compression_applied" not in run.strategy_decision_json["reason_codes"]
        assert run.strategy_decision_json["tool_results"][0]["item_count"] == 2
        assert run.context_budget_json is not None
        assert run.context_budget_json["items"]["selected_count"] == 2
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 2
        assert run.tool_result_compression_json is not None
        assert run.tool_result_compression_json["enabled"] is False
        assert run.tool_result_compression_json["summary"]["output_item_count"] == 0


def test_rag_ask_llm_tool_orchestrator_repeated_query_without_candidates_returns_no_context(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    vector_client.candidates = []
    settings = _lmstudio_test_settings(sparse_enabled=False, hybrid_enabled=False)
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
    chat_session_id = _create_chat_session(client, csrf_token, title="llm repeat empty")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-repeat-empty-msg-1",
            "message": "alpha repeat empty",
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
        assert run.strategy_decision_json["best_effort_finalize_used"] is True
        assert run.strategy_decision_json["no_context"] is True
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 1


def test_rag_ask_llm_tool_orchestrator_falls_back_to_hybrid_for_dense_hybrid_comparison(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_DenseThenNoToolPlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm hybrid compare")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-hybrid-compare-msg-1",
            "message": "denseとhybridで比較してください",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.strategy_decision_json is not None
        assert run.strategy_decision_json["search_call_count"] == 2
        assert run.strategy_decision_json["retrieval_call_count"] == 2
        assert run.strategy_decision_json["fallback_used"] is True
        assert run.strategy_decision_json["fallback_strategy"] == "hybrid"
        assert run.strategy_decision_json["fallback_reason"] == "llm_tool_additional_search"
        assert run.strategy_decision_json["tools_used"] == ["dense_search", "hybrid_search"]
        assert run.strategy_decision_json["finalize_called"] is True
        assert (
            "planner_no_tool_call_fallback_hybrid_comparison"
            in run.strategy_decision_json["reason_codes"]
        )


def test_rag_ask_llm_tool_orchestrator_prefers_named_project_source_for_citations(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    with session_factory() as db:
        db.add(
            LogicalDocument(
                logical_document_id=50,
                owner_user_id=1,
                title="RAGProject Phase2 Hybrid Retrieval",
            )
        )
        db.add(
            LogicalDocument(
                logical_document_id=60,
                owner_user_id=1,
                title="LLM Research - ReAct",
            )
        )
        db.add(
            _version(
                50,
                50,
                "ready",
                True,
                "e",
                file_name="hybrid_retrieval.md",
            )
        )
        db.add(_version(60, 60, "ready", True, "f", file_name="2022-react-reasoning-acting.md"))
        db.add(
            _chunk(
                500,
                50,
                "RAGProject Hybrid Retrieval compares dense and sparse fusion for Phase2. " * 3,
            )
        )
        db.add(_chunk(600, 60, "ReAct reasoning and acting generic agent paper. " * 4))
        db.commit()
    vector_client.candidates = [_candidate(600, 0.99, 1), _candidate(500, 0.50, 2)]
    settings = _lmstudio_test_settings()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_DenseThenNoToolPlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm source affinity")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-source-affinity-msg-1",
            "message": "RAGProject dense hybrid comparison",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["retrieval_summary"]["tools_used"] == ["dense_search", "hybrid_search"]
    assert data["citations"][0]["document_chunk_id"] == 500
    assert data["citations"][0]["source_label"].startswith("hybrid_retrieval.md")
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        first_selected = (
            db.query(RetrievalRunItem)
            .filter_by(retrieval_run_id=run.retrieval_run_id, selected_flag=True)
            .order_by(RetrievalRunItem.rerank_order.asc())
            .first()
        )
        assert first_selected is not None
        assert first_selected.document_chunk_id == 500


def test_rag_ask_llm_tool_orchestrator_insufficient_answer_with_context_returns_citations(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings()
    orchestrator = LLMToolCallingRetrievalOrchestrator(
        settings,
        planner=_DenseThenNoToolPlanner(),
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedInsufficientEvidenceAnswerGenerator(),
        llm_tool_orchestrator=orchestrator,
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="llm insufficient")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "llm-insufficient-msg-1",
            "message": "denseとhybridで比較してください",
            "strategy": "llm_tool_orchestrator",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["assistant_message"]["content"] == INSUFFICIENT_EVIDENCE_FALLBACK_ANSWER
    assert data["citations"][0]["document_chunk_id"] == 100
    assert data["confidence"]["confidence_label"] == "Low"
    with session_factory() as db:
        run = db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).one()
        assert run.status == "succeeded"
        assert run.error_code is None
        assert run.confidence_label == "Low"
        messages = db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).all()
        assert [message.role for message in messages] == ["user", "assistant"]
        assert db.query(Citation).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 1


def test_rag_ask_agentic_router_disabled_uses_single_fallback_dense_retrieval(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings(
        router_enabled=False,
        router_sufficiency_top_score_threshold=0.99,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
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
    settings = _lmstudio_test_settings(
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
    settings = _lmstudio_test_settings(
        router_store_decision_trace=False,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
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


def test_rag_ask_uses_nvidia_model_key_and_persists_safe_generation_metadata(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _lmstudio_test_settings(nvidia_api_key="test-nvidia-key")

    def fake_create_answer_generator(*args: Any, **kwargs: Any) -> _ObservedCitationAnswerGenerator:
        assert args[0] is settings
        assert kwargs["provider"] == "nvidia"
        assert kwargs["model_name"] == "meta/llama-3.3-70b-instruct"
        return _ObservedCitationAnswerGenerator()

    monkeypatch.setattr(
        "app.services.rag_service.create_answer_generator",
        fake_create_answer_generator,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="nvidia model")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "nvidia-model-msg",
            "message": "alpha policy summary",
            "model_key": "nvidia:meta/llama-3.3-70b-instruct",
            "strategy": "dense",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["generation"] == {
        "provider": "nvidia",
        "model": "meta/llama-3.3-70b-instruct",
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
        "estimated_cost_usd": 0.0,
        "latency_ms": data["generation"]["latency_ms"],
    }
    assert data["generation"]["latency_ms"] >= 0
    assert "test-nvidia-key" not in str(response.json())
    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.retrieval_settings_json is not None
        assert run.retrieval_settings_json["generation_provider"] == "nvidia"
        assert run.retrieval_settings_json["generation_model"] == "meta/llama-3.3-70b-instruct"
        assert "test-nvidia-key" not in str(run.retrieval_settings_json)


def test_rag_ask_rejects_nvidia_outside_local_without_persisting_message(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    settings = _settings(
        app_env="production",
        nvidia_api_key="test-nvidia-key",
        session_cookie_secure=True,
        session_secret="x" * 32,
    )
    cast(Any, client.app).dependency_overrides[rag_search_service] = lambda: RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=FakeRerankerClient(),
        answer_generator=_ObservedCitationAnswerGenerator(),
    )
    csrf_token = _login(client, email="viewer@example.com")
    chat_session_id = _create_chat_session(client, csrf_token, title="nvidia production block")

    response = client.post(
        "/api/v1/rag/ask",
        json={
            "chat_session_id": chat_session_id,
            "client_message_id": "nvidia-production-msg",
            "message": "alpha policy summary",
            "model_key": "nvidia:meta/llama-3.3-70b-instruct",
        },
        headers=_unsafe_headers(csrf_token),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_model"
    assert "test-nvidia-key" not in str(response.json())
    assert vector_client.query_vectors == []
    with session_factory() as db:
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 0
        assert db.query(RetrievalRun).filter_by(chat_session_id=chat_session_id).count() == 0


def test_rag_ask_persists_and_replays_multiple_citations(
    rag_ask_client: tuple[TestClient, sessionmaker[Session], _StaticVectorClient],
) -> None:
    client, session_factory, vector_client = rag_ask_client
    service = RagService(
        settings=_lmstudio_test_settings(),
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
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 0
        assert db.query(Citation).count() == 0

    vector_client.candidates = [_candidate(100, 0.91, 1)]
    failing_service = RagService(
        settings=_lmstudio_test_settings(),
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
        assert "evidence_pack_ms" in run.latency_breakdown_json
        assert run.context_compression_json is not None
        assert run.context_compression_json["output"]["evidence_item_count"] == 1
        run_items = (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).all()
        )
        assert run_items
        assert all(item.score_breakdown_json is not None for item in run_items)
        assert db.query(Citation).count() == 0

    no_marker_service = RagService(
        settings=_lmstudio_test_settings(),
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
        settings=_lmstudio_test_settings(),
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
        settings=_lmstudio_test_settings(),
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
        settings=_lmstudio_test_settings(),
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
        settings=_lmstudio_test_settings(),
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


class _HybridThenFinalizePlanner:
    def plan(self, request: LLMToolPlanningRequest) -> list[LLMToolCall]:
        if request.tool_results:
            return [
                LLMToolCall(
                    tool_name="finalize_answer",
                    arguments={
                        "selected_tool_call_ids": [
                            result.tool_call_id
                            for result in request.tool_results
                            if result.item_count > 0
                        ],
                    },
                )
            ]
        return [LLMToolCall(tool_name="hybrid_search", arguments={"query": request.user_query})]


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


class _DenseThenNoToolPlanner:
    def plan(self, request: LLMToolPlanningRequest) -> list[LLMToolCall]:
        if not request.tool_results:
            return [LLMToolCall(tool_name="dense_search", arguments={"query": request.user_query})]
        return []


class _DenseDifferentQueriesThenNoToolPlanner:
    def plan(self, request: LLMToolPlanningRequest) -> list[LLMToolCall]:
        if len(request.tool_results) == 0:
            return [LLMToolCall(tool_name="dense_search", arguments={"query": "alpha first"})]
        if len(request.tool_results) == 1:
            return [LLMToolCall(tool_name="dense_search", arguments={"query": "alpha second"})]
        return []


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


class _SequenceAnswerGenerator:
    def __init__(self, contents: Sequence[str]) -> None:
        self.contents = list(contents)
        self.requests: list[GenerationRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if not request.context_items:
            raise AnswerGenerationError()
        index = min(self.call_count - 1, len(self.contents) - 1)
        return GenerationResult(
            content=self.contents[index],
            usage=TokenUsage(input_tokens=12, output_tokens=8, total_tokens=20),
        )


class _ObservedCitationAnswerGenerator:
    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not request.context_items:
            raise AnswerGenerationError()
        return GenerationResult(
            content=OBSERVED_CITED_ANSWER,
            usage=TokenUsage(input_tokens=12, output_tokens=8, total_tokens=20),
        )


class _ObservedInsufficientEvidenceAnswerGenerator:
    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if not request.context_items:
            raise AnswerGenerationError()
        return GenerationResult(
            content=OBSERVED_INSUFFICIENT_EVIDENCE_ANSWER,
            usage=TokenUsage(input_tokens=12, output_tokens=8, total_tokens=20),
        )


class _HedgeThenCitationAnswerGenerator:
    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        if not request.context_items:
            raise AnswerGenerationError()
        content = (
            OBSERVED_INSUFFICIENT_EVIDENCE_ANSWER if self.call_count == 1 else OBSERVED_CITED_ANSWER
        )
        return GenerationResult(
            content=content,
            usage=TokenUsage(input_tokens=12, output_tokens=8, total_tokens=20),
        )


def _lmstudio_test_settings(**overrides: Any) -> Settings:
    return _settings(
        generation_provider="lmstudio",
        generation_model_name=TEST_LMSTUDIO_MODEL,
        **overrides,
    )


def _live_lmstudio_settings() -> Settings:
    base_url = os.getenv("RAG_LIVE_LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").strip()
    base_url = base_url.rstrip("/")
    if not base_url:
        pytest.skip("RAG_LIVE_LMSTUDIO_BASE_URL must not be empty")
    model_name = os.getenv("RAG_LIVE_LMSTUDIO_MODEL")
    try:
        models_response = httpx.get(f"{base_url}/models", timeout=2.0)
    except httpx.HTTPError:
        pytest.skip("LM Studio is not reachable")
    if models_response.status_code >= 400:
        pytest.skip("LM Studio models endpoint is not available")
    if not model_name:
        try:
            models_payload = models_response.json()
        except ValueError:
            pytest.skip("LM Studio models endpoint did not return JSON")
        model_entries = models_payload.get("data") if isinstance(models_payload, dict) else None
        if isinstance(model_entries, list):
            for entry in model_entries:
                entry_id = entry.get("id") if isinstance(entry, dict) else None
                if isinstance(entry_id, str):
                    model_name = entry_id
                    break
    if not model_name:
        pytest.skip("LM Studio has no loaded model; set RAG_LIVE_LMSTUDIO_MODEL")
    try:
        timeout_seconds = float(os.getenv("RAG_LIVE_LMSTUDIO_TIMEOUT_SECONDS", "60"))
    except ValueError:
        pytest.skip("RAG_LIVE_LMSTUDIO_TIMEOUT_SECONDS must be numeric")
    if timeout_seconds <= 0:
        pytest.skip("RAG_LIVE_LMSTUDIO_TIMEOUT_SECONDS must be positive")
    return _settings(
        generation_provider="lmstudio",
        generation_model_name=model_name,
        lmstudio_base_url=base_url,
        lmstudio_timeout_seconds=timeout_seconds,
        generation_max_output_chars=800,
    )


def _settings(**overrides: Any) -> Settings:
    return Settings(
        **{
            "app_env": "test",
            "embedding_provider": "fake",
            "embedding_fake_dimension": 4,
            "retrieval_top_k_default": 2,
            "retrieval_top_k_max": 5,
            "rerank_provider": "fake",
            "rerank_top_n_default": 1,
            "rerank_top_n_max": 5,
            "qdrant_collection_name": "document_chunks",
            "search_snippet_max_chars": 32,
            "generation_provider": "fake",
            **overrides,
        }
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
            "context_compression": run.context_compression_json,
        }
    )
    assert raw_query not in dumped
    assert "raw_prompt" not in dumped
    assert "raw_chunk" not in dumped
    assert "content_text" not in dumped
    assert "full_context" not in dumped
