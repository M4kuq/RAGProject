from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

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
from app.mcp.adapters import McpServiceAdapter
from app.mcp.prompts import get_prompt, list_prompts
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


def test_tool_registry_exposes_only_phase1_tools(mcp_adapter: McpServiceAdapter) -> None:
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
        {"query": "alpha citation", "top_k": 3, "rerank_top_n": 2}
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
    assert ask["citations"]
    assert ask["confidence"]["confidence_label"] in {"High", "Medium", "Low"}
    assert "RAW_CHUNK_SHOULD_NOT_APPEAR" not in json.dumps(ask)
    assert "full_context" not in json.dumps(ask).lower()
    assert "raw_prompt" not in json.dumps(ask).lower()


def test_document_job_and_evaluation_tools_are_redacted(mcp_adapter: McpServiceAdapter) -> None:
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


def test_jsonrpc_server_lists_and_calls_tools(mcp_adapter: McpServiceAdapter) -> None:
    server = JsonRpcMcpServer(mcp_adapter)

    initialize = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        }
    )
    assert initialize is not None
    assert initialize["result"]["capabilities"]["tools"]["listChanged"] is False

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
        }
    )
    assert response is not None
    assert response["result"]["structuredContent"]["status"] == "succeeded"
    assert "RAW_CHUNK_SHOULD_NOT_APPEAR" not in response["result"]["content"][0]["text"]
    assert "secret_token" not in response["result"]["content"][0]["text"].lower()
    assert server.handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    ) is None


def test_jsonrpc_lists_reads_resources_and_gets_prompts(
    mcp_adapter: McpServiceAdapter,
) -> None:
    server = JsonRpcMcpServer(mcp_adapter)

    resources = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    templates = server.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "resources/templates/list"}
    )
    document = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/read",
            "params": {"uri": "rag://documents/1"},
        }
    )
    prompts = server.handle_message({"jsonrpc": "2.0", "id": 4, "method": "prompts/list"})
    prompt = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "prompts/get",
            "params": {
                "name": "rag_search_debug",
                "arguments": {"question": "Bearer abcdefghijklmnop"},
            },
        }
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
        }
    )
    unknown_tool = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "archive_document", "arguments": {}},
        }
    )
    blank_query = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "rag_search", "arguments": {"query": "   "}},
        }
    )
    missing_resource = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/read",
            "params": {"uri": "rag://documents/999"},
        }
    )
    bad_prompt_args = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "prompts/get",
            "params": {"name": "rag_search_debug", "arguments": []},
        }
    )

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
                    }
                ),
                "",
            ]
        )
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    assert run_stdio(JsonRpcMcpServer(mcp_adapter)) == 0

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["result"]["serverInfo"]["name"] == "ragproject-mcp"


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
        )
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
        )
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
        )
    )
