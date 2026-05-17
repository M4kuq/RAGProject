from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_admin, require_csrf
from app.api.responses import get_request_id, success_response
from app.core.config import get_settings
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
    payload: RagAskRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    service: RagService = Depends(rag_search_service),
) -> dict[str, object]:
    try:
        result = service.ask(db, payload=payload, user=user, request_id=get_request_id(request))
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
