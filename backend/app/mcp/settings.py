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
    if settings.snippet_max_chars < 20:
        raise ValueError("MCP_SNIPPET_MAX_CHARS must be at least 20")
    if settings.tool_timeout_seconds < 1:
        raise ValueError("MCP_TOOL_TIMEOUT_SECONDS must be positive")
