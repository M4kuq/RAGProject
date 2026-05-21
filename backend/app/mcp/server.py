from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from pathlib import Path
from typing import Any

if __package__ and __package__.startswith("backend."):
    backend_root = Path(__file__).resolve().parents[2]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

from app.mcp.adapters import McpServiceAdapter
from app.mcp.errors import McpError, McpInvalidRequest, McpMethodNotFound, McpNotFound
from app.mcp.prompts import get_prompt, list_prompts
from app.mcp.resources import list_resource_templates, list_resources, read_resource
from app.mcp.settings import get_mcp_settings
from app.mcp.tools import build_tool_registry, call_tool, list_tools

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
MCP_RESOURCE_NOT_FOUND = -32002


class JsonRpcMcpServer:
    def __init__(self, adapter: McpServiceAdapter | None = None) -> None:
        self.adapter = adapter or McpServiceAdapter()
        self.tools = build_tool_registry(self.adapter)
        self.initialized = False

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _error_response(message.get("id"), JSONRPC_INVALID_REQUEST, "Invalid request")
        request_id = message.get("id")
        method = message["method"]
        params = message.get("params", {})
        if params is None:
            params = {}
        if "id" not in message:
            if method == "notifications/initialized":
                self.initialized = True
            return None
        if not isinstance(params, dict):
            return _error_response(request_id, JSONRPC_INVALID_PARAMS, "Invalid params")
        try:
            result = self._dispatch(method, params)
        except McpMethodNotFound as exc:
            return _error_response(
                request_id,
                JSONRPC_METHOD_NOT_FOUND,
                str(exc),
                {"code": exc.code},
            )
        except McpNotFound as exc:
            return _error_response(request_id, MCP_RESOURCE_NOT_FOUND, str(exc), {"code": exc.code})
        except McpInvalidRequest as exc:
            return _error_response(request_id, JSONRPC_INVALID_PARAMS, str(exc), {"code": exc.code})
        except McpError as exc:
            return _error_response(request_id, JSONRPC_INTERNAL_ERROR, str(exc), {"code": exc.code})
        except Exception:
            return _error_response(request_id, JSONRPC_INTERNAL_ERROR, "Internal error")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return list_tools(self.tools)
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise McpInvalidRequest("tool name is required")
            arguments = params.get("arguments", {})
            return self._call_tool_with_timeout(name, arguments)
        if method == "resources/list":
            return list_resources()
        if method == "resources/templates/list":
            return list_resource_templates()
        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str):
                raise McpInvalidRequest("resource uri is required")
            return read_resource(self.adapter, uri)
        if method == "prompts/list":
            return list_prompts()
        if method == "prompts/get":
            name = params.get("name")
            if not isinstance(name, str):
                raise McpInvalidRequest("prompt name is required")
            return get_prompt(name, params.get("arguments"))
        raise McpMethodNotFound(f"Unknown method: {method}")

    def _call_tool_with_timeout(self, name: str, arguments: object) -> dict[str, Any]:
        timeout_seconds = self.adapter.mcp_settings.tool_timeout_seconds
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(call_tool, self.tools, name, arguments)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "status": "failed",
                                "error_code": "tool_timeout",
                                "message": "MCP tool timed out.",
                            },
                            ensure_ascii=False,
                        ),
                    }
                ],
                "structuredContent": {
                    "status": "failed",
                    "error_code": "tool_timeout",
                    "message": "MCP tool timed out.",
                },
                "isError": True,
            }
        finally:
            if future.done():
                executor.shutdown(wait=True)

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        protocol_version = (
            requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        )
        settings = get_mcp_settings(self.adapter.settings)
        if not settings.enabled:
            raise McpInvalidRequest("MCP server is disabled")
        return {
            "protocolVersion": protocol_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {"name": "ragproject-mcp", "version": "0.1.0"},
            "instructions": (
                "Local-only read-mostly RAGProject MCP server. Write tools, remote MCP, "
                "OAuth, sampling, and elicitation are not implemented in Phase1."
            ),
        }


def run_stdio(server: JsonRpcMcpServer | None = None) -> int:
    active_server = server or JsonRpcMcpServer()
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            _write_message(_error_response(None, JSONRPC_PARSE_ERROR, "Parse error"))
            continue
        if not isinstance(message, dict):
            _write_message(_error_response(None, JSONRPC_INVALID_REQUEST, "Invalid request"))
            continue
        response = active_server.handle_message(message)
        if response is not None:
            _write_message(response)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local-only RAGProject MCP stdio server.")
    parser.add_argument("--transport", default="stdio", choices=["stdio"])
    parser.add_argument("--version", action="store_true", help="Print server version and exit.")
    args = parser.parse_args(argv)
    if args.version:
        print("ragproject-mcp 0.1.0")
        return 0
    settings = get_mcp_settings()
    if not settings.enabled:
        print("MCP server is disabled.", file=sys.stderr)
        return 1
    if args.transport != "stdio":
        print("Only stdio transport is implemented in Phase1.", file=sys.stderr)
        return 2
    return run_stdio()


def _write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _error_response(
    request_id: object,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


if __name__ == "__main__":
    raise SystemExit(main())
