from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class McpSettings:
    enabled: bool
    transport: str
    local_only: bool
    actor_mode: str
    snippet_max_chars: int
    tool_timeout_seconds: int
    allow_write_tools: bool
    enable_advanced_rag_tools: bool
    allowed_strategies: tuple[str, ...]
    include_trace_summary_default: bool
    max_answer_chars: int
    allow_evaluation_run_create: bool


def get_mcp_settings(settings: Settings | None = None) -> McpSettings:
    source = settings or Settings(_env_file=None)
    mcp_settings = McpSettings(
        enabled=source.mcp_enabled,
        transport=source.mcp_transport,
        local_only=source.mcp_local_only,
        actor_mode=source.mcp_actor_mode,
        snippet_max_chars=source.mcp_snippet_max_chars,
        tool_timeout_seconds=source.mcp_tool_timeout_seconds,
        allow_write_tools=source.mcp_allow_write_tools,
        enable_advanced_rag_tools=source.mcp_enable_advanced_rag_tools,
        allowed_strategies=tuple(source.mcp_allowed_strategies),
        include_trace_summary_default=source.mcp_include_trace_summary_default,
        max_answer_chars=source.mcp_max_answer_chars,
        allow_evaluation_run_create=source.mcp_allow_evaluation_run_create,
    )
    validate_mcp_settings(mcp_settings)
    return mcp_settings


def validate_mcp_settings(settings: McpSettings) -> None:
    if settings.transport != "stdio":
        raise ValueError("remote MCP transports are not implemented in Phase1")
    if not settings.local_only:
        raise ValueError("MCP must remain local-only in Phase1")
    if settings.actor_mode != "mcp_local":
        raise ValueError("MCP_ACTOR_MODE must be mcp_local")
    if settings.allow_write_tools:
        raise ValueError("MCP write tools are disabled in Phase1")
    if settings.allow_evaluation_run_create:
        raise ValueError("MCP evaluation run creation is disabled in PR-38")
    allowed = {
        "dense",
        "sparse",
        "hybrid",
        "graph_postgres",
        "graph_neo4j",
        "agentic_router",
        "llm_tool_orchestrator",
        "langchain_agentic",
        "langgraph_agentic",
    }
    if not settings.allowed_strategies or any(
        strategy not in allowed for strategy in settings.allowed_strategies
    ):
        raise ValueError("MCP_ALLOWED_STRATEGIES contains unsupported strategies")
    if settings.snippet_max_chars < 20:
        raise ValueError("MCP_SNIPPET_MAX_CHARS must be at least 20")
    if settings.tool_timeout_seconds < 1:
        raise ValueError("MCP_TOOL_TIMEOUT_SECONDS must be positive")
    if settings.max_answer_chars < 20:
        raise ValueError("MCP_MAX_ANSWER_CHARS must be at least 20")
