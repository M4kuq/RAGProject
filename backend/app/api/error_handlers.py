from __future__ import annotations

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.core.errors import normalize_error
from app.schemas.common import ErrorBody, ErrorEnvelope, Meta

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: object | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    payload = ErrorEnvelope(
        error=ErrorBody(code=code, message=message, details=details or {}),
        meta=Meta(request_id=_request_id(request)),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def _validation_details(exc: RequestValidationError) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        details.append(
            {
                "field": loc or "request",
                "reason": str(error.get("msg", "Invalid value.")),
            }
        )
    return details


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code, message, details = normalize_error(exc.status_code, exc.detail)
        return _error_response(
            request,
            status_code=exc.status_code,
            code=code,
            message=message,
            details=details,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code="validation_error",
            message="Invalid request.",
            details=_validation_details(exc),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled API exception",
            extra={"request_id": _request_id(request), "exception_type": type(exc).__name__},
        )
        return _error_response(
            request,
            status_code=500,
            code="internal_server_error",
            message="Internal server error.",
        )
