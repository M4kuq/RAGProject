from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db import evaluation_models as _evaluation_models  # noqa: F401
from app.db.base import Base
from app.db.evaluation_models import EvaluationResult
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    EvaluationRun,
    EvaluationRunItem,
    Job,
    LogicalDocument,
    Role,
    User,
)
from app.evaluation.rag_service import DatabaseVectorSearchClient
from app.ingest.embedding import FakeEmbeddingAdapter
from app.mcp.adapters import McpServiceAdapter, _safe_rag_ask_output
from app.mcp.prompts import get_prompt, list_prompts
from app.mcp.redaction import redact_data, safe_metric_details, truncate_text
from app.mcp.resources import list_resource_templates, list_resources, read_resource
from app.mcp.server import JsonRpcMcpServer, main, run_stdio
from app.mcp.settings import get_mcp_settings
from app.mcp.tools import build_tool_registry
from app.rag.generation import FakeAnswerGenerator
from app.rag.rerank import FakeRerankerClient
from app.services.rag_service import RagService


@pytest.fixture
def mcp_adapter() -> Iterator[McpServiceAdapter]:
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
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        rerank_provider="fake",
        generation_provider="fake",
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_top_n_default=2,
        rerank_top_n_max=5,
        ask_top_k_default=5,
        ask_rerank_top_n_default=2,
        search_snippet_max_chars=48,
        mcp_snippet_max_chars=48,
    )
    try:
        yield McpServiceAdapter(
            settings=settings,
            session_factory=session_factory,
            rag_service_factory=lambda settings, db: RagService(
                settings=settings,
                embedding_adapter=FakeEmbeddingAdapter(dimension=4),
                vector_client=DatabaseVectorSearchClient(db),
                reranker=FakeRerankerClient(),
                answer_generator=FakeAnswerGenerator(),
            ),
        )
    finally:
        engine.dispose()


@pytest.fixture
def empty_mcp_adapter() -> Iterator[McpServiceAdapter]:
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
        retrieval_top_k_default=5,
        retrieval_top_k_max=5,
        rerank_top_n_default=2,
        rerank_top_n_max=5,
        ask_top_k_default=5,
        ask_rerank_top_n_default=2,
        search_snippet_max_chars=48,
        mcp_snippet_max_chars=48,
    )
    try:
        yield McpServiceAdapter(
            settings=settings,
            session_factory=session_factory,
            rag_service_factory=lambda settings, db: RagService(
                settings=settings,
                embedding_adapter=FakeEmbeddingAdapter(dimension=4),
                vector_client=DatabaseVectorSearchClient(db),
                reranker=FakeRerankerClient(),
                answer_generator=FakeAnswerGenerator(),
            ),
        )
    finally:
        engine.dispose()


def test_mcp_settings_phase1_guardrails() -> None:
    settings = Settings(_env_file=None, app_env="test")

    mcp_settings = get_mcp_settings(settings)

    assert mcp_settings.transport == "stdio"
    assert mcp_settings.local_only is True
    assert mcp_settings.allow_write_tools is False
    with pytest.raises(ValueError, match="MCP_ALLOW_WRITE_TOOLS"):
        Settings(_env_file=None, app_env="test", mcp_allow_write_tools=True)
    with pytest.raises(ValueError, match="MCP_LOCAL_ONLY"):
        Settings(_env_file=None, app_env="test", mcp_local_only=False)
    with pytest.raises(ValueError, match="MCP_TRANSPORT"):
        Settings(_env_file=None, app_env="test", mcp_transport="http")
    with pytest.raises(ValueError, match="MCP_ACTOR_MODE"):
        Settings(_env_file=None, app_env="test", mcp_actor_mode="admin")
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="test", mcp_tool_timeout_seconds=0)
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="test", mcp_snippet_max_chars=19)
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="test", mcp_snippet_max_chars=2001)


def test_mcp_adapter_injects_storage_without_global_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_get_settings() -> Settings:
        raise AssertionError("MCP adapter must not use global .env-backed settings")

    monkeypatch.setattr("app.storage.file_storage.get_settings", fail_get_settings)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        storage_root=tmp_path / "mcp-storage",
    )

    try:
        adapter = McpServiceAdapter(settings=settings, session_factory=session_factory)
    finally:
        engine.dispose()

    assert adapter.document_service.storage.base_dir == settings.storage_root


def test_tool_registry_exposes_only_phase1_tools(
    mcp_adapter: McpServiceAdapter,
) -> None:
    registry = build_tool_registry(mcp_adapter)

    assert set(registry) == {
        "rag_search",
        "rag_ask",
        "list_documents",
        "get_document_status",
        "get_job_status",
        "list_evaluation_runs",
        "get_evaluation_result",
    }
    forbidden = {"upload", "approve", "archive", "retry", "create_evaluation_run"}
    assert forbidden.isdisjoint(registry)


def test_rag_search_and_ask_return_safe_truncated_output(
    mcp_adapter: McpServiceAdapter,
) -> None:
    search = mcp_adapter.rag_search(
        {"query": "alpha citation", "top_k": 3, "rerank_top_n": 2},
    )

    assert search["status"] == "succeeded"
    assert search["items"]
    assert all(len(item["snippet"]) <= 48 for item in search["items"])
    assert "RAW_CHUNK_SHOULD_NOT_APPEAR" not in json.dumps(search)
    assert "secret_token" not in json.dumps(search).lower()
    assert "C:\\" not in json.dumps(search)
    assert "storage_key" not in json.dumps(search).lower()

    ask = mcp_adapter.rag_ask({"question": "Summarize alpha citation", "top_k": 3})

    assert ask["status"] == "succeeded"
    assert ask["answer"]
    assert len(ask["answer"]) > 48
    assert ask["citations"]
    assert all(len(citation["snippet"]) <= 48 for citation in ask["citations"])
    assert ask["confidence"]["confidence_label"] in {"High", "Medium", "Low"}
    assert "RAW_CHUNK_SHOULD_NOT_APPEAR" not in json.dumps(ask)
    assert "full_context" not in json.dumps(ask).lower()
    assert "raw_prompt" not in json.dumps(ask).lower()


def test_rag_ask_no_context_returns_safe_failure(
    empty_mcp_adapter: McpServiceAdapter,
) -> None:
    ask = empty_mcp_adapter.rag_ask({"question": "Summarize missing context", "top_k": 3})

    assert ask["status"] == "failed"
    assert ask["error_code"] == "no_context_found"
    assert ask["answer"] == ""
    assert ask["citations"] == []
    dumped = json.dumps(ask)
    assert "raw prompt" not in dumped.lower()
    assert "full context" not in dumped.lower()


def test_document_job_and_evaluation_tools_are_redacted(
    mcp_adapter: McpServiceAdapter,
) -> None:
    documents = mcp_adapter.list_documents({})
    document = mcp_adapter.get_document_status({"logical_document_id": 1})
    job = mcp_adapter.get_job_status({"job_id": 1})
    runs = mcp_adapter.list_evaluation_runs({"status": "succeeded"})
    result = mcp_adapter.get_evaluation_result({"evaluation_run_id": 1})
    dumped = json.dumps([documents, document, job, runs, result])

    assert documents["items"][0]["logical_document_id"] == 1
    assert document["versions"][0]["chunk_count"] == 1
    assert job["payload_view"]["payload"]["redacted_sensitive"] is True
    assert result["items"][0]["metrics"][0]["details"]["case_id"] == "case-alpha"
    assert "C:\\" not in dumped
    assert "/app/storage" not in dumped
    assert "secret_token" not in dumped.lower()
    assert "raw prompt" not in dumped.lower()
    assert "full context" not in dumped.lower()


def test_mcp_redaction_covers_prompt_context_tokens_paths_and_metric_details() -> None:
    payload = {
        "prompt_text": "raw prompt should be omitted",
        "fullContext": "full context should be omitted",
        "nested": {
            "rawChunkText": "RAW_CHUNK_SHOULD_NOT_APPEAR",
            "storagePath": "C:\\private\\chunk.txt",
            "authorization": "Bearer abcdefghijklmnop",
        },
        "safe": (
            "Bearer abcdefghijklmnop and https://user:pass@example.test/path "
            "C:\\Users\\Kei My Docs\\secret file.txt "
            "/app/storage/Kei My Docs/secret file.txt "
            "/storage/Shared Docs/private.txt /data/Team Docs/private.txt "
            "/tmp/RAG Project/private.txt"
        ),
    }

    redacted = redact_data(payload, max_string_chars=120)
    dumped = json.dumps(redacted)

    assert "raw prompt should be omitted" not in dumped
    assert "full context should be omitted" not in dumped
    assert "RAW_CHUNK_SHOULD_NOT_APPEAR" not in dumped
    assert "C:\\" not in dumped
    assert "Kei My Docs" not in dumped
    assert "secret file.txt" not in dumped
    assert "/app/storage" not in dumped
    assert "/storage" not in dumped
    assert "/data" not in dumped
    assert "/tmp" not in dumped
    assert "Shared Docs" not in dumped
    assert "Team Docs" not in dumped
    assert "RAG Project" not in dumped
    assert "Bearer abcdefghijklmnop" not in dumped
    assert "user:pass" not in dumped
    assert "omitted_raw_field" in dumped
    assert "redacted_sensitive" in dumped
    assert "[REDACTED]" in dumped

    details = safe_metric_details(
        {
            "case_id": "case-alpha",
            "matched_count": 2,
            "safe_score": 0.8,
            "promptText": "raw prompt should be removed",
            "retrieved_context": "full context should be removed",
            "unknown_text": "unsafe detail should be removed",
        },
        max_string_chars=120,
    )
    details_dumped = json.dumps(details)

    assert details["case_id"] == "case-alpha"
    assert details["matched_count"] == 2
    assert details["safe_score"] == 0.8
    assert "raw prompt should be removed" not in details_dumped
    assert "full context should be removed" not in details_dumped
    assert "unsafe detail should be removed" not in details_dumped
    assert details["omitted_unsafe_detail"] is True


def test_mcp_rag_ask_answer_uses_generation_limit_not_snippet_limit() -> None:
    data = {
        "status": "succeeded",
        "answer": "Answer [1] " + ("x" * 120),
        "citations": [{"snippet": "citation " + ("y" * 120)}],
    }

    safe = _safe_rag_ask_output(data, answer_max_chars=80, snippet_max_chars=48)

    assert len(safe["answer"]) == 80
    assert safe["answer"].endswith("...")
    assert len(safe["citations"][0]["snippet"]) == 48
    assert safe["citations"][0]["snippet"].endswith("...")


def test_mcp_redaction_covers_inline_compound_secret_keys() -> None:
    value = (
        "secret_token=abcd1234 access_token: abcdefghijklmnop "
        "csrf_token=csrf123456789 session_id=session123456789 "
        "Authorization: Basic dXNlcjpwYXNz Cookie: session=abcdef "
        "credential=plain private_key: -----BEGIN"
    )

    redacted = truncate_text(value, max_chars=200)

    assert "abcd1234" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "csrf123456789" not in redacted
    assert "session123456789" not in redacted
    assert "dXNlcjpwYXNz" not in redacted
    assert "session=abcdef" not in redacted
    assert "credential=plain" not in redacted
    assert "private_key" not in redacted.lower()
    assert redacted.count("[REDACTED]") == 8


def test_mcp_redaction_preserves_sentinals_when_input_keys_collide() -> None:
    redacted = redact_data(
        {
            "redacted_sensitive": False,
            "omitted_raw_field": False,
            "api_key": "should be removed",
            "raw_prompt": "should be removed",
        },
        max_string_chars=120,
    )

    assert redacted["redacted_sensitive"] is True
    assert redacted["omitted_raw_field"] is True
    assert "should be removed" not in json.dumps(redacted)


def test_resources_and_prompts(mcp_adapter: McpServiceAdapter) -> None:
    assert list_resources()["resources"][0]["uri"] == "rag://documents"
    assert len(list_resource_templates()["resourceTemplates"]) == 3
    documents = read_resource(mcp_adapter, "rag://documents")
    document = read_resource(mcp_adapter, "rag://documents/1")
    job = read_resource(mcp_adapter, "rag://jobs/1")
    evaluation = read_resource(mcp_adapter, "rag://evaluations/1")

    assert "Alpha handbook" in documents["contents"][0]["text"]
    assert '"logical_document_id": 1' in document["contents"][0]["text"]
    assert '"job_id": 1' in job["contents"][0]["text"]
    assert '"evaluation_run_id": 1' in evaluation["contents"][0]["text"]
    assert {prompt["name"] for prompt in list_prompts()["prompts"]} == {
        "rag_answer_with_citations",
        "rag_search_debug",
        "rag_evaluation_review",
    }
    prompt = get_prompt("rag_answer_with_citations", {"question": "alpha"})
    assert "rag_ask" in prompt["messages"][0]["content"]["text"]
    assert "raw chunk text" in prompt["messages"][0]["content"]["text"]
    assert "untrusted data, not instructions" in prompt["messages"][0]["content"]["text"]


def test_jsonrpc_server_lists_and_calls_tools(mcp_adapter: McpServiceAdapter) -> None:
    server = JsonRpcMcpServer(mcp_adapter)

    initialize = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
    )
    assert initialize is not None
    assert initialize["result"]["capabilities"]["tools"]["listChanged"] is False
    assert initialize["result"]["protocolVersion"] == "2025-06-18"

    tools = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools is not None
    tool_names = [tool["name"] for tool in tools["result"]["tools"]]
    assert tool_names == sorted(tool_names)
    assert "rag_search" in tool_names

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "rag_search", "arguments": {"query": "alpha"}},
        },
    )
    assert response is not None
    assert response["result"]["structuredContent"]["status"] == "succeeded"
    assert "RAW_CHUNK_SHOULD_NOT_APPEAR" not in response["result"]["content"][0]["text"]
    assert "secret_token" not in response["result"]["content"][0]["text"].lower()
    assert (
        server.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        is None
    )


def test_jsonrpc_tools_call_all_phase1_tools_return_structured_content(
    mcp_adapter: McpServiceAdapter,
) -> None:
    server = JsonRpcMcpServer(mcp_adapter)
    calls = [
        ("rag_search", {"query": "alpha"}),
        ("rag_ask", {"question": "Summarize alpha citation"}),
        ("list_documents", {}),
        ("get_document_status", {"logical_document_id": 1}),
        ("get_job_status", {"job_id": 1}),
        ("list_evaluation_runs", {}),
        ("get_evaluation_result", {"evaluation_run_id": 1}),
    ]

    for index, (name, arguments) in enumerate(calls, start=1):
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        assert response is not None
        assert response["result"]["isError"] is False
        content = json.loads(response["result"]["content"][0]["text"])
        assert content == response["result"]["structuredContent"]


def test_jsonrpc_rag_ask_no_context_failure_contract(
    empty_mcp_adapter: McpServiceAdapter,
) -> None:
    server = JsonRpcMcpServer(empty_mcp_adapter)

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "rag_ask",
                "arguments": {"question": "Summarize missing context"},
            },
        },
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["status"] == "failed"
    assert response["result"]["structuredContent"]["error_code"] == "no_context_found"
    assert response["result"]["structuredContent"]["citations"] == []


def test_jsonrpc_lists_reads_resources_and_gets_prompts(
    mcp_adapter: McpServiceAdapter,
) -> None:
    server = JsonRpcMcpServer(mcp_adapter)

    resources = server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
    )
    templates = server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "resources/templates/list"},
    )
    document = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/read",
            "params": {"uri": "rag://documents/1"},
        },
    )
    prompts = server.handle_message(
        {"jsonrpc": "2.0", "id": 4, "method": "prompts/list"},
    )
    prompt = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "prompts/get",
            "params": {
                "name": "rag_search_debug",
                "arguments": {"question": "Bearer abcdefghijklmnop"},
            },
        },
    )

    assert resources is not None
    assert resources["result"]["resources"][0]["uri"] == "rag://documents"
    assert templates is not None
    assert len(templates["result"]["resourceTemplates"]) == 3
    assert document is not None
    resource_text = document["result"]["contents"][0]["text"]
    assert "Alpha handbook" in resource_text
    assert "C:\\" not in resource_text
    assert "/app/storage" not in resource_text
    assert prompts is not None
    prompt_names = [item["name"] for item in prompts["result"]["prompts"]]
    assert prompt_names == sorted(prompt_names)
    assert prompt is not None
    prompt_text = prompt["result"]["messages"][0]["content"]["text"]
    assert "rag_search" in prompt_text
    assert "Bearer abcdefghijklmnop" not in prompt_text


def test_jsonrpc_rejects_invalid_inputs_and_missing_resources(
    mcp_adapter: McpServiceAdapter,
) -> None:
    server = JsonRpcMcpServer(mcp_adapter)

    bad_arguments = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_documents", "arguments": []},
        },
    )
    unknown_tool = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "archive_document", "arguments": {}},
        },
    )
    blank_query = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "rag_search", "arguments": {"query": "   "}},
        },
    )
    missing_resource = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/read",
            "params": {"uri": "rag://documents/999"},
        },
    )
    bad_prompt_args = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "prompts/get",
            "params": {"name": "rag_search_debug", "arguments": []},
        },
    )
    extra_argument = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "rag_search",
                "arguments": {"query": "alpha", "unexpected": True},
            },
        },
    )
    bad_uri = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "resources/read",
            "params": {"uri": "https://example.test/documents/1"},
        },
    )
    bad_prompt_name = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "prompts/get",
            "params": {"name": "unknown_prompt"},
        },
    )
    unknown_method = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "Bearer abcdefghijklmnop secret_token=abcd",
        },
    )
    invalid_id = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": {"secret_token": "abcd"},
            "method": "ping",
        },
    )
    invalid_tool_boundaries = [
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "get_document_status",
                    "arguments": {"logical_document_id": 0},
                },
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "get_job_status", "arguments": {"job_id": 0}},
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "get_evaluation_result",
                    "arguments": {"evaluation_run_id": 0},
                },
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {"name": "list_documents", "arguments": {"status": "deleted"}},
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {
                    "name": "list_evaluation_runs",
                    "arguments": {"page_size": 101},
                },
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 15,
                "method": "tools/call",
                "params": {"name": "list_documents", "arguments": None},
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 16,
                "method": "tools/call",
                "params": {"name": "get_job_status", "arguments": {"job_id": "1"}},
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 17,
                "method": "tools/call",
                "params": {
                    "name": "rag_search",
                    "arguments": {"query": "alpha", "top_k": "3"},
                },
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 18,
                "method": "tools/call",
                "params": {"name": "list_documents", "arguments": {"page": 0}},
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 19,
                "method": "tools/call",
                "params": {
                    "name": "list_documents",
                    "arguments": {"display_status": "deleted"},
                },
            },
        ),
        server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {
                    "name": "list_evaluation_runs",
                    "arguments": {"status": "unknown"},
                },
            },
        ),
    ]

    assert bad_arguments is not None
    assert bad_arguments["error"]["code"] == -32602
    assert unknown_tool is not None
    assert unknown_tool["error"]["code"] == -32002
    assert blank_query is not None
    assert blank_query["error"]["code"] == -32602
    assert missing_resource is not None
    assert missing_resource["error"]["code"] == -32002
    assert bad_prompt_args is not None
    assert bad_prompt_args["error"]["code"] == -32602
    assert extra_argument is not None
    assert extra_argument["error"]["code"] == -32602
    assert bad_uri is not None
    assert bad_uri["error"]["code"] == -32602
    assert bad_prompt_name is not None
    assert bad_prompt_name["error"]["code"] == -32002
    assert unknown_method is not None
    assert unknown_method["error"]["code"] == -32601
    assert unknown_method["error"]["message"] == "Method not found"
    assert "Bearer abcdefghijklmnop" not in json.dumps(unknown_method)
    assert invalid_id is not None
    assert invalid_id["id"] is None
    assert invalid_id["error"]["code"] == -32600
    for response in invalid_tool_boundaries:
        assert response is not None
        assert response["error"]["code"] == -32602


def test_jsonrpc_forbidden_write_tools_are_not_listed_or_callable(
    mcp_adapter: McpServiceAdapter,
) -> None:
    server = JsonRpcMcpServer(mcp_adapter)
    listed = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    tool_names = {tool["name"] for tool in listed["result"]["tools"]}
    forbidden = {
        "upload_document",
        "approve_document",
        "archive_document",
        "retry_job",
        "create_evaluation_run",
    }

    assert forbidden.isdisjoint(tool_names)
    for index, name in enumerate(forbidden, start=2):
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            },
        )
        assert response is not None
        assert response["error"]["code"] == -32002


def test_mcp_disabled_rejects_initialize_and_main_exits_nonzero(
    mcp_adapter: McpServiceAdapter,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disabled = Settings(_env_file=None, app_env="test", mcp_enabled=False)
    mcp_adapter.settings = disabled
    server = JsonRpcMcpServer(mcp_adapter)

    initialize = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
    )

    assert initialize is not None
    assert initialize["error"]["code"] == -32602
    monkeypatch.setattr("app.mcp.server.get_mcp_settings", lambda: get_mcp_settings(disabled))
    assert main([]) == 1
    assert "MCP server is disabled." in capsys.readouterr().err


def test_stdio_smoke_handles_initialize_and_parse_error(
    mcp_adapter: McpServiceAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdin = io.StringIO(
        "\n".join(
            [
                "{bad json",
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"},
                    },
                ),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                "",
            ]
        ),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    assert run_stdio(JsonRpcMcpServer(mcp_adapter)) == 0

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["result"]["serverInfo"]["name"] == "ragproject-mcp"
    assert "rag_search" in {tool["name"] for tool in lines[2]["result"]["tools"]}


def test_stdio_handles_jsonrpc_batch_requests(
    mcp_adapter: McpServiceAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdin = io.StringIO(
        "\n".join(
            [
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
                ),
                json.dumps([]),
                json.dumps([{"jsonrpc": "2.0", "id": 3, "method": "ping"}, 1]),
                "",
            ],
        ),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    assert run_stdio(JsonRpcMcpServer(mcp_adapter)) == 0

    batch, empty_batch, mixed_batch = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(batch) == 2
    assert batch[0]["result"]["serverInfo"]["name"] == "ragproject-mcp"
    assert "rag_search" in {tool["name"] for tool in batch[1]["result"]["tools"]}
    assert empty_batch[0]["error"]["code"] == -32600
    assert mixed_batch[0]["result"] == {}
    assert mixed_batch[1]["error"]["code"] == -32600


def test_mcp_main_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert "ragproject-mcp 0.1.0" in capsys.readouterr().out


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
                "Alpha citation policy uses deterministic fake adapters. "
                "RAW_CHUNK_SHOULD_NOT_APPEAR secret_token=abcd1234 "
                "This sentence is intentionally long so MCP snippets truncate."
            ),
            token_count=20,
            char_count=160,
            page_from=1,
            page_to=1,
            section_title="Alpha section",
            modality="text",
        ),
    )
    db.add(
        Job(
            job_id=1,
            job_type="document_ingest",
            status="failed",
            priority=100,
            target_type="document_version",
            target_id=1,
            payload_json={
                "logical_document_id": 1,
                "storage_key": "/app/storage/uploads/private-alpha.md",
                "secret_token": "abcd1234",
            },
            result_json={"path": "C:\\private\\result.txt", "safe_count": 1},
            created_by=user.user_id,
            started_at=now,
            finished_at=now,
            error_code="ingest_failed",
            error_message="failed with password=abcd1234",
        ),
    )
    run = EvaluationRun(
        evaluation_run_id=1,
        created_by=user.user_id,
        status="succeeded",
        target_type="fixture_dataset",
        metrics_config={"dataset_name": "phase1_smoke", "case_limit": 1},
        started_at=now,
        finished_at=now,
    )
    db.add(run)
    db.flush()
    item = EvaluationRunItem(
        evaluation_run_item_id=1,
        evaluation_run_id=1,
        retrieval_run_id=None,
        status="succeeded",
        faithfulness_score=Decimal("1.0"),
        groundedness_score=Decimal("0.8"),
        citation_coverage=Decimal("1.0"),
        latency_ms=12,
    )
    db.add(item)
    db.flush()
    db.add(
        EvaluationResult(
            evaluation_run_item_id=item.evaluation_run_item_id,
            metric_name="case_metadata",
            metric_score=None,
            metric_label="case-alpha",
            details_json={
                "case_id": "case-alpha",
                "raw_prompt": "raw prompt should be removed",
                "full_context": "full context should be removed",
            },
        ),
    )
