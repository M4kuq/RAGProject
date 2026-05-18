from __future__ import annotations

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
from app.rag.rerank import FakeRerankerClient, RerankCandidate, RerankError
from app.rag.retrieval import RetrievalError, RetrievalFilters, VectorSearchCandidate
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
                source_label="policy.md",
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
    assert "raw context text" not in first.content
    assert "policy.md p.1 chunk:100" in first.content


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
    assert "full active chunk text should not be returned whole" not in str(body)
    assert "token" not in str(body).lower()
    assert "storage_key" not in str(body).lower()
    assert len(vector_client.query_vectors) == 1

    with session_factory() as db:
        run = db.get(RetrievalRun, data["retrieval_run_id"])
        assert run is not None
        assert run.chat_session_id == chat_session_id
        assert run.request_message_id == data["user_message"]["chat_message_id"]
        assert run.status == "succeeded"
        assert run.answer_confidence is None
        assert run.groundedness_score is None
        assert run.confidence_label is None
        assert db.query(ChatMessage).filter_by(chat_session_id=chat_session_id).count() == 2
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 2
        )
        assert db.query(Citation).count() == 0

    replay = client.post("/api/v1/rag/ask", json=payload, headers=_unsafe_headers(viewer_csrf))
    assert replay.status_code == 200
    assert replay.json()["meta"]["replayed"] is True
    assert (
        replay.json()["data"]["user_message"]["chat_message_id"]
        == data["user_message"]["chat_message_id"]
    )
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
        assert db.query(Citation).count() == 0


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


class _FailingAnswerGenerator:
    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise AnswerGenerationError()


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
