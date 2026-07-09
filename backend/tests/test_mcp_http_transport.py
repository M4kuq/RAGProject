from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routers.mcp import get_mcp_adapter
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument, Role, User
from app.evaluation.rag_service import DatabaseVectorSearchClient
from app.ingest.embedding import FakeEmbeddingAdapter
from app.main import create_app
from app.mcp.adapters import McpServiceAdapter
from app.rag.generation import FakeAnswerGenerator
from app.rag.rerank import FakeRerankerClient
from app.services.rag_service import RagService

API_KEY = "test-mcp-http-key"
BASE_HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}


def _test_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "database_url": "sqlite://",
        "embedding_provider": "fake",
        "embedding_fake_dimension": 4,
        "rerank_provider": "fake",
        "generation_provider": "fake",
        "retrieval_top_k_default": 5,
        "retrieval_top_k_max": 5,
        "rerank_top_n_default": 2,
        "rerank_top_n_max": 5,
        "search_snippet_max_chars": 48,
        "mcp_transport": "http",
        "mcp_http_api_key": API_KEY,
        "mcp_snippet_max_chars": 48,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def mcp_http_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_data(db)
        db.commit()

    settings = _test_settings()

    def override_adapter() -> McpServiceAdapter:
        return McpServiceAdapter(
            settings=settings,
            session_factory=session_factory,
            rag_service_factory=lambda current_settings, db: RagService(
                settings=current_settings,
                embedding_adapter=FakeEmbeddingAdapter(dimension=4),
                vector_client=DatabaseVectorSearchClient(db),
                reranker=FakeRerankerClient(),
                answer_generator=FakeAnswerGenerator(),
            ),
        )

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("MCP_TRANSPORT", "http")
    monkeypatch.setenv("MCP_HTTP_API_KEY", API_KEY)
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_mcp_adapter] = override_adapter
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        engine.dispose()


def test_mcp_http_authentication_required(mcp_http_client: TestClient) -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    missing = mcp_http_client.post("/mcp", json=payload, headers={"Accept": "application/json"})
    missing_bad_accept = mcp_http_client.post(
        "/mcp",
        json=payload,
        headers={"Accept": "text/event-stream"},
    )
    wrong = mcp_http_client.post(
        "/mcp",
        json=payload,
        headers={"Authorization": "Bearer wrong-key", "Accept": "application/json"},
    )
    ok = mcp_http_client.post("/mcp", json=payload, headers=BASE_HEADERS)

    assert missing.status_code == 401
    assert missing_bad_accept.status_code == 401
    assert wrong.status_code == 401
    assert API_KEY not in missing.text
    assert API_KEY not in wrong.text
    assert ok.status_code == 200
    assert "rag_search" in {tool["name"] for tool in ok.json()["result"]["tools"]}


def test_mcp_http_origin_validation(mcp_http_client: TestClient) -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    invalid = mcp_http_client.post(
        "/mcp",
        json=payload,
        headers={**BASE_HEADERS, "Origin": "https://example.test"},
    )
    localhost = mcp_http_client.post(
        "/mcp",
        json=payload,
        headers={**BASE_HEADERS, "Origin": "http://localhost:3000"},
    )
    loopback = mcp_http_client.post(
        "/mcp",
        json=payload,
        headers={**BASE_HEADERS, "Origin": "http://127.0.0.1:3000"},
    )
    no_origin = mcp_http_client.post("/mcp", json=payload, headers=BASE_HEADERS)

    assert invalid.status_code == 403
    assert localhost.status_code == 200
    assert loopback.status_code == 200
    assert no_origin.status_code == 200


def test_mcp_http_accept_and_protocol_version_headers(mcp_http_client: TestClient) -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    accepted = mcp_http_client.post("/mcp", json=payload, headers=BASE_HEADERS)
    wildcard = mcp_http_client.post(
        "/mcp",
        json=payload,
        headers={"Authorization": f"Bearer {API_KEY}", "Accept": "*/*"},
    )
    event_stream_only = mcp_http_client.post(
        "/mcp",
        json=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "text/event-stream",
        },
    )
    unknown_protocol = mcp_http_client.post(
        "/mcp",
        json=payload,
        headers={**BASE_HEADERS, "MCP-Protocol-Version": "2024-11-05"},
    )

    assert accepted.status_code == 200
    assert wildcard.status_code == 200
    assert event_stream_only.status_code == 406
    assert unknown_protocol.status_code == 400
    assert "2024-11-05" not in unknown_protocol.text


def test_mcp_http_notification_and_get_contract(mcp_http_client: TestClient) -> None:
    notification = mcp_http_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=BASE_HEADERS,
    )
    get_response = mcp_http_client.get("/mcp", headers=BASE_HEADERS)

    assert notification.status_code == 202
    assert notification.content == b""
    assert get_response.status_code == 405


def test_mcp_http_rejects_batch_messages(mcp_http_client: TestClient) -> None:
    response = mcp_http_client.post(
        "/mcp",
        json=[{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}],
        headers=BASE_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600


def test_mcp_http_initialize_tools_list_and_rag_search_flow(
    mcp_http_client: TestClient,
) -> None:
    initialize = mcp_http_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
        headers=BASE_HEADERS,
    )
    tools = mcp_http_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers=BASE_HEADERS,
    )
    search = mcp_http_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "rag_search",
                "arguments": {"query": "alpha citation", "top_k": 3, "rerank_top_n": 2},
            },
        },
        headers=BASE_HEADERS,
    )

    assert initialize.status_code == 200
    assert initialize.json()["result"]["protocolVersion"] == "2025-06-18"
    assert tools.status_code == 200
    assert "rag_search" in {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert search.status_code == 200
    result = search.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["status"] == "succeeded"
    assert result["structuredContent"]["items"]
    content = json.loads(result["content"][0]["text"])
    assert content == result["structuredContent"]
    dumped = json.dumps(result)
    assert "RAW_CHUNK_SHOULD_NOT_APPEAR" not in dumped
    assert "secret_token" not in dumped.lower()
    assert "raw_prompt" not in dumped.lower()
    assert "full_context" not in dumped.lower()
    assert API_KEY not in dumped


def test_mcp_http_requires_api_key_when_enabled() -> None:
    with pytest.raises(ValueError, match="MCP_HTTP_API_KEY"):
        Settings(_env_file=None, app_env="test", mcp_transport="http")


def test_mcp_route_is_not_registered_in_stdio_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    monkeypatch.delenv("MCP_HTTP_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"},
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 404


def _seed_data(db: Session) -> None:
    now = datetime.now(UTC)
    role = Role(role_name="admin", description="Admin")
    db.add(role)
    db.flush()
    user = User(
        role_id=role.role_id,
        email="admin@example.com",
        display_name="Admin",
        password_hash=None,
        status="active",
    )
    db.add(user)
    db.flush()
    document = LogicalDocument(
        logical_document_id=1,
        owner_user_id=user.user_id,
        title="Alpha handbook",
        status="active",
    )
    db.add(document)
    version = DocumentVersion(
        document_version_id=1,
        logical_document_id=1,
        version_no=1,
        content_hash="a" * 64,
        status="ready",
        is_active=True,
        file_name="C:\\private\\Alpha handbook.md",
        mime_type="text/markdown",
        file_size_bytes=128,
        storage_key="/app/storage/uploads/private-alpha.md",
        page_count=1,
        created_by=user.user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(version)
    db.flush()
    db.add(
        DocumentChunk(
            document_chunk_id=1,
            document_version_id=1,
            chunk_index=0,
            chunk_hash="b" * 64,
            content_text=(
                "Alpha citation policy uses deterministic adapters. "
                "RAW_CHUNK_SHOULD_NOT_APPEAR secret_token=abcd1234 "
                "This sentence is intentionally long so MCP snippets truncate."
            ),
            token_count=20,
            char_count=160,
            page_from=1,
            page_to=1,
            section_title="Alpha section",
            metadata_json={
                "source_type": "url",
                "source_url": "https://example.com/api_key/alpha?token=abcd1234",
            },
            modality="text",
        ),
    )
