from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.mcp.adapters import McpServiceAdapter
from app.mcp.server import JsonRpcMcpServer, run_stdio


@pytest.fixture
def mcp_adapter() -> Iterator[McpServiceAdapter]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        rerank_provider="fake",
        generation_provider="fake",
        mcp_snippet_max_chars=48,
    )
    try:
        yield McpServiceAdapter(settings=settings, session_factory=session_factory)
    finally:
        engine.dispose()


def test_jsonrpc_list_methods_are_exposed(mcp_adapter: McpServiceAdapter) -> None:
    server = JsonRpcMcpServer(mcp_adapter)

    tools = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    resources = server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
    )
    templates = server.handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/templates/list"},
    )
    prompts = server.handle_message({"jsonrpc": "2.0", "id": 4, "method": "prompts/list"})

    assert tools is not None
    assert "rag_search" in {tool["name"] for tool in tools["result"]["tools"]}
    assert resources is not None
    assert resources["result"]["resources"][0]["uri"] == "rag://documents"
    assert templates is not None
    assert len(templates["result"]["resourceTemplates"]) == 3
    assert prompts is not None
    prompt_names = [prompt["name"] for prompt in prompts["result"]["prompts"]]
    assert prompt_names == sorted(prompt_names)
    assert "rag_answer_with_citations" in prompt_names


def test_jsonrpc_rejects_prompt_args_and_page_size_bounds(
    mcp_adapter: McpServiceAdapter,
) -> None:
    server = JsonRpcMcpServer(mcp_adapter)
    cases = [
        {
            "method": "prompts/get",
            "params": {"name": "rag_search_debug", "arguments": []},
        },
        {
            "method": "tools/call",
            "params": {"name": "list_documents", "arguments": {"page_size": 101}},
        },
        {
            "method": "tools/call",
            "params": {"name": "list_evaluation_runs", "arguments": {"page_size": 101}},
        },
    ]

    for index, payload in enumerate(cases, start=1):
        response = server.handle_message({"jsonrpc": "2.0", "id": index, **payload})
        assert response is not None
        assert response["error"]["code"] == -32602


def test_jsonrpc_unsafe_string_ids_are_rejected_without_echo(
    mcp_adapter: McpServiceAdapter,
) -> None:
    server = JsonRpcMcpServer(mcp_adapter)
    unsafe_ids = [
        "request secret_token=abcd1234",
        "Bearer abcdefghijklmnop",
        "sk-testshouldberemoved1234567890",
        "ghp_testshouldberemoved1234567890",
        "eyJabc.def.ghi",
        "https://user:pass@example.test/path",
    ]

    for request_id in unsafe_ids:
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": request_id, "method": "unknown"},
        )
        dumped = json.dumps(response)
        assert response is not None
        assert response["id"] is None
        assert response["error"]["code"] == -32600
        assert request_id not in dumped


def test_stdio_initialized_notification_does_not_emit_response(
    mcp_adapter: McpServiceAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdin = io.StringIO(
        json.dumps(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            ],
        )
        + "\n",
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    assert run_stdio(JsonRpcMcpServer(mcp_adapter)) == 0

    batch = json.loads(stdout.getvalue())
    assert len(batch) == 2
    assert batch[0]["result"]["serverInfo"]["name"] == "ragproject-mcp"
    assert "rag_search" in {tool["name"] for tool in batch[1]["result"]["tools"]}
