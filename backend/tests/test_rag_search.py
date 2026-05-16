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
from app.rag.rerank import (
    FakeRerankerClient,
    RerankCandidate,
    RerankError,
    normalize_rerank_score,
)
from app.rag.retrieval import (
    InMemoryVectorSearchClient,
    RetrievalError,
    RetrievalFilters,
    VectorSearchCandidate,
)
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
        assert run.answer_confidence is None
        assert run.groundedness_score is None
        assert run.confidence_label is None
        assert run.retrieval_score_summary == data["retrieval_score_summary"]
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 2
        )
        assert db.query(Citation).count() == 0
        snapshots = [
            item.payload_snapshot
            for item in db.query(RetrievalRunItem)
            .filter_by(retrieval_run_id=run.retrieval_run_id)
            .all()
        ]
        for snapshot in snapshots:
            assert snapshot is not None
            assert "content_text" not in str(snapshot)
            assert "document_name" not in snapshot


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
        assert (
            db.query(RetrievalRunItem).filter_by(retrieval_run_id=run.retrieval_run_id).count() == 0
        )


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


def _unsafe_headers(csrf_token: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN}
