from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from app.core.config import Settings, get_settings
from app.mcp.adapters import McpServiceAdapter
from app.mcp.http_transport import (
    MCP_PROTOCOL_VERSION_HEADER,
    McpHttpError,
    handle_http_jsonrpc_message,
    jsonrpc_invalid_request_error,
    jsonrpc_parse_error,
    validate_http_request_headers,
)

router = APIRouter()


def get_mcp_adapter(settings: Settings = Depends(get_settings)) -> McpServiceAdapter:
    from app.db.session import SessionLocal

    return McpServiceAdapter(settings=settings, session_factory=SessionLocal)


@router.post("/mcp", response_model=None)
async def mcp_post(
    request: Request,
    settings: Settings = Depends(get_settings),
    adapter: McpServiceAdapter = Depends(get_mcp_adapter),
) -> Response:
    try:
        validate_http_request_headers(
            accept=request.headers.get("accept"),
            authorization=request.headers.get("authorization"),
            expected_api_key=settings.mcp_http_api_key,
            origin=request.headers.get("origin"),
            protocol_version=request.headers.get(MCP_PROTOCOL_VERSION_HEADER),
        )
    except McpHttpError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    try:
        message = await request.json()
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(status_code=400, content=jsonrpc_parse_error())

    try:
        result = await run_in_threadpool(handle_http_jsonrpc_message, message, adapter=adapter)
    except McpHttpError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonrpc_invalid_request_error(exc),
        )

    if result.body is None:
        return Response(status_code=result.status_code)
    return JSONResponse(status_code=result.status_code, content=result.body)
