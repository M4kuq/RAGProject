from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_admin, require_csrf
from app.api.responses import get_request_id, success_response
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.db.models import User
from app.db.session import get_db
from app.schemas.rag import RagAskRequest, RagSearchRequest
from app.services.rag_service import (
    RagAskPipelineError,
    RagSearchPipelineError,
    RagService,
    create_rag_service,
)

router = APIRouter(dependencies=[Depends(require_csrf)])


def rag_search_service() -> RagService:
    return create_rag_service(get_settings())


@router.post("/ask")
def ask(
    payload: dict[str, Any],
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    service: RagService = Depends(rag_search_service),
) -> dict[str, object]:
    chat_session_id = _chat_session_id_from_payload(payload)
    if chat_session_id is not None:
        service.chat_service.ensure_session_can_append_messages(
            db,
            user=user,
            chat_session_id=chat_session_id,
        )
    try:
        ask_payload = RagAskRequest.model_validate(payload)
    except ValidationError as exc:
        raise ValidationFailed(
            details=[
                {
                    "field": ".".join(str(part) for part in error.get("loc", ())) or "request",
                    "reason": str(error.get("msg", "Invalid value.")),
                }
                for error in exc.errors()
            ]
        ) from exc
    try:
        result = service.ask(
            db,
            payload=ask_payload,
            user=user,
            request_id=get_request_id(request),
        )
    except RagAskPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.error_code}) from exc
    return {
        "data": result.model_dump(mode="json", exclude={"replayed"}),
        "meta": {"request_id": get_request_id(request), "replayed": result.replayed},
    }


@router.post("/search")
def search(
    payload: RagSearchRequest,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    service: RagService = Depends(rag_search_service),
) -> dict[str, object]:
    try:
        result = service.search(db, payload=payload, request_id=get_request_id(request))
    except RagSearchPipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.error_code}) from exc
    return success_response(result.model_dump(mode="json"), request)


def _chat_session_id_from_payload(payload: dict[str, Any]) -> int | None:
    value = payload.get("chat_session_id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1:
        return None
    return value
