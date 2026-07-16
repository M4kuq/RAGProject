from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.mcp.adapters import McpServiceAdapter
from app.mcp.server import (
    JSONRPC_INVALID_REQUEST,
    JSONRPC_PARSE_ERROR,
    SUPPORTED_PROTOCOL_VERSIONS,
    JsonRpcMcpServer,
    _error_response,
)

ACCEPT_JSON = "application/json"
MCP_PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"


@dataclass(frozen=True)
class McpHttpError(Exception):
    status_code: int
    code: str
    message: str
    jsonrpc_error_code: int | None = None


@dataclass(frozen=True)
class McpHttpResult:
    status_code: int
    body: dict[str, Any] | None


def authorize_bearer_header(authorization: str | None, expected_key: str | None) -> bool:
    if not authorization or not expected_key:
        return False
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return False
    try:
        token_bytes = token.encode("ascii")
        expected_key_bytes = expected_key.encode("ascii")
    except UnicodeEncodeError:
        return False
    return secrets.compare_digest(token_bytes, expected_key_bytes)


def validate_accept_header(accept: str | None) -> None:
    if accept is None:
        return
    media_ranges = {
        item.split(";", 1)[0].strip().lower() for item in accept.split(",") if item.strip()
    }
    if not media_ranges:
        return
    if ACCEPT_JSON in media_ranges or "application/*" in media_ranges or "*/*" in media_ranges:
        return
    raise McpHttpError(406, "not_acceptable", "Not acceptable.")


def validate_protocol_version_header(protocol_version: str | None) -> None:
    if protocol_version is None:
        return
    if protocol_version in SUPPORTED_PROTOCOL_VERSIONS:
        return
    raise McpHttpError(400, "invalid_protocol_version", "Invalid MCP protocol version.")


def validate_origin_header(origin: str | None) -> None:
    if origin is None:
        return
    try:
        parsed = urlparse(origin)
        hostname = parsed.hostname
    except ValueError as exc:
        raise McpHttpError(403, "permission_denied", "Permission denied.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or hostname is None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise McpHttpError(403, "permission_denied", "Permission denied.")
    host = hostname.rstrip(".").lower()
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise McpHttpError(403, "permission_denied", "Permission denied.")


def validate_http_request_headers(
    *,
    accept: str | None,
    authorization: str | None,
    expected_api_key: str | None,
    origin: str | None,
    protocol_version: str | None,
) -> None:
    validate_origin_header(origin)
    if not authorize_bearer_header(authorization, expected_api_key):
        raise McpHttpError(401, "auth_required", "Authentication required.")
    validate_accept_header(accept)
    validate_protocol_version_header(protocol_version)


def handle_http_jsonrpc_message(
    message: object,
    *,
    adapter: McpServiceAdapter,
) -> McpHttpResult:
    if isinstance(message, list):
        raise McpHttpError(
            400,
            "invalid_request",
            "Invalid request.",
            jsonrpc_error_code=JSONRPC_INVALID_REQUEST,
        )
    if not isinstance(message, dict):
        raise McpHttpError(
            400,
            "invalid_request",
            "Invalid request.",
            jsonrpc_error_code=JSONRPC_INVALID_REQUEST,
        )
    response = JsonRpcMcpServer(adapter).handle_message(message)
    if response is None:
        return McpHttpResult(status_code=202, body=None)
    return McpHttpResult(status_code=200, body=response)


def jsonrpc_parse_error() -> dict[str, Any]:
    return _error_response(None, JSONRPC_PARSE_ERROR, "Parse error")


def jsonrpc_invalid_request_error(error: McpHttpError) -> dict[str, Any]:
    if error.jsonrpc_error_code is None:
        return {"error": {"code": error.code, "message": error.message}}
    return _error_response(None, error.jsonrpc_error_code, "Invalid request")
