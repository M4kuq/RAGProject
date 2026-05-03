from __future__ import annotations

import re
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def resolve_request_id(value: str | None) -> str:
    if value is None:
        return generate_request_id()
    candidate = value
    if candidate != candidate.strip():
        return generate_request_id()
    if not candidate or not _REQUEST_ID_PATTERN.fullmatch(candidate):
        return generate_request_id()
    return candidate


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
