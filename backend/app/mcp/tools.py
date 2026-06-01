from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .adapters import McpServiceAdapter
from .errors import McpError, McpInvalidRequest, McpNotFound, McpToolExecutionError


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def build_tool_registry(adapter: McpServiceAdapter) -> dict[str, McpTool]:
    tools = [
        McpTool(
            name="rag_search",
            description="Search active RAG document chunks. Returns truncated snippets only.",
            input_schema=_object_schema(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "strategy": {
                        "type": "string",
                        "enum": ["dense", "sparse", "hybrid", "agentic_router"],
                        "default": "dense",
                    },
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "rerank_top_n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "include_trace_summary": {"type": "boolean"},
                },
                required=["query"],
            ),
            handler=adapter.rag_search,
        ),
        McpTool(
            name="rag_search_hybrid",
            description="Hybrid RAG search wrapper for rag_search(strategy=hybrid).",
            input_schema=_object_schema(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "rerank_top_n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "include_trace_summary": {"type": "boolean"},
                },
                required=["query"],
            ),
            handler=adapter.rag_search_hybrid,
        ),
        McpTool(
            name="rag_search_agentic",
            description="Agentic RAG search wrapper for rag_search(strategy=agentic_router).",
            input_schema=_object_schema(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "rerank_top_n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "include_trace_summary": {"type": "boolean"},
                },
                required=["query"],
            ),
            handler=adapter.rag_search_agentic,
        ),
        McpTool(
            name="rag_ask",
            description=(
                "Answer a question with citations from active RAG documents. "
                "No raw context is returned; retrieval audit records may be persisted."
            ),
            input_schema=_object_schema(
                {
                    "question": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "strategy": {
                        "type": "string",
                        "enum": ["dense", "hybrid", "agentic_router"],
                        "default": "dense",
                    },
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "rerank_top_n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "include_citations": {"type": "boolean", "default": True},
                    "include_confidence": {"type": "boolean", "default": True},
                    "include_trace_summary": {"type": "boolean"},
                },
                required=["question"],
            ),
            handler=adapter.rag_ask,
        ),
        McpTool(
            name="rag_ask_hybrid",
            description="Hybrid RAG ask wrapper for rag_ask(strategy=hybrid).",
            input_schema=_object_schema(
                {
                    "question": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "rerank_top_n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "include_citations": {"type": "boolean", "default": True},
                    "include_confidence": {"type": "boolean", "default": True},
                    "include_trace_summary": {"type": "boolean"},
                },
                required=["question"],
            ),
            handler=adapter.rag_ask_hybrid,
        ),
        McpTool(
            name="rag_ask_agentic",
            description="Agentic RAG ask wrapper for rag_ask(strategy=agentic_router).",
            input_schema=_object_schema(
                {
                    "question": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "rerank_top_n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "include_citations": {"type": "boolean", "default": True},
                    "include_confidence": {"type": "boolean", "default": True},
                    "include_trace_summary": {"type": "boolean"},
                },
                required=["question"],
            ),
            handler=adapter.rag_ask_agentic,
        ),
        McpTool(
            name="rag_ask_auto",
            description="Auto RAG ask wrapper for rag_ask(strategy=llm_tool_orchestrator).",
            input_schema=_object_schema(
                {
                    "question": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "rerank_top_n": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "include_citations": {"type": "boolean", "default": True},
                    "include_confidence": {"type": "boolean", "default": True},
                    "include_trace_summary": {"type": "boolean"},
                },
                required=["question"],
            ),
            handler=adapter.rag_ask_auto,
        ),
        McpTool(
            name="rag_get_retrieval_trace",
            description="Get a safe retrieval trace summary by retrieval_run_id.",
            input_schema=_object_schema(
                {"retrieval_run_id": {"type": "integer", "minimum": 1}},
                required=["retrieval_run_id"],
            ),
            handler=adapter.rag_get_retrieval_trace,
        ),
        McpTool(
            name="rag_compare_strategies",
            description=(
                "Read the latest safe strategy comparison summary. "
                "Does not create or rerun evaluations."
            ),
            input_schema=_object_schema(
                {
                    "evaluation_dataset_id": {"type": "integer", "minimum": 1},
                    "strategies": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["dense", "sparse", "hybrid", "agentic_router"],
                        },
                        "minItems": 1,
                        "maxItems": 4,
                    },
                    "mode": {"type": "string", "enum": ["latest_results"]},
                },
            ),
            handler=adapter.rag_compare_strategies,
        ),
        McpTool(
            name="rag_get_evaluation_summary",
            description="Get a safe evaluation run summary without raw prompts or context.",
            input_schema=_object_schema(
                {"evaluation_run_id": {"type": "integer", "minimum": 1}},
                required=["evaluation_run_id"],
            ),
            handler=adapter.rag_get_evaluation_summary,
        ),
        McpTool(
            name="list_documents",
            description=(
                "List document metadata. Archived documents are returned only when requested."
            ),
            input_schema=_object_schema(
                {
                    "status": {"type": "string", "enum": ["active", "archived"]},
                    "display_status": {
                        "type": "string",
                        "enum": [
                            "active",
                            "pending_review",
                            "processing",
                            "failed",
                            "archived",
                        ],
                    },
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                },
            ),
            handler=adapter.list_documents,
        ),
        McpTool(
            name="get_document_status",
            description="Get safe document status, version summaries, and chunk counts.",
            input_schema=_object_schema(
                {"logical_document_id": {"type": "integer", "minimum": 1}},
                required=["logical_document_id"],
            ),
            handler=adapter.get_document_status,
        ),
        McpTool(
            name="get_job_status",
            description=(
                "Get safe job status and redacted payload/result summaries. Retry is not supported."
            ),
            input_schema=_object_schema(
                {"job_id": {"type": "integer", "minimum": 1}},
                required=["job_id"],
            ),
            handler=adapter.get_job_status,
        ),
        McpTool(
            name="list_evaluation_runs",
            description="List evaluation run summaries. Does not create or rerun evaluations.",
            input_schema=_object_schema(
                {
                    "status": {
                        "type": "string",
                        "enum": [
                            "queued",
                            "running",
                            "succeeded",
                            "failed",
                            "canceled",
                        ],
                    },
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                },
            ),
            handler=adapter.list_evaluation_runs,
        ),
        McpTool(
            name="get_evaluation_result",
            description=(
                "Get safe evaluation metrics and case summaries without prompts or full context."
            ),
            input_schema=_object_schema(
                {"evaluation_run_id": {"type": "integer", "minimum": 1}},
                required=["evaluation_run_id"],
            ),
            handler=adapter.get_evaluation_result,
        ),
    ]
    return {tool.name: tool for tool in tools}


def list_tools(registry: dict[str, McpTool]) -> dict[str, Any]:
    return {"tools": [registry[name].definition() for name in sorted(registry)]}


def call_tool(
    registry: dict[str, McpTool],
    name: str,
    arguments: object,
) -> dict[str, Any]:
    if name not in registry:
        raise McpNotFound("tool not found")
    if isinstance(arguments, dict):
        parsed_arguments = arguments
    else:
        raise McpInvalidRequest("tool arguments must be an object")
    try:
        structured = registry[name].handler(parsed_arguments)
    except McpToolExecutionError as exc:
        return _tool_error(exc)
    except McpError:
        raise
    is_error = (
        name in {"rag_ask", "rag_ask_agentic", "rag_ask_auto", "rag_ask_hybrid"}
        and structured.get("status") == "failed"
    )
    return _tool_success(structured, is_error=is_error)


def _tool_success(structured: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(structured, ensure_ascii=False, indent=2),
            },
        ],
        "structuredContent": structured,
        "isError": is_error,
    }


def _tool_error(error: McpToolExecutionError) -> dict[str, Any]:
    structured = {"status": "failed", "error_code": error.code, "message": str(error)}
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(structured, ensure_ascii=False),
            },
        ],
        "structuredContent": structured,
        "isError": True,
    }


def _object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }
