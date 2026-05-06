from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session
from starlette import status as http_status

from app.api.deps import pagination_params, require_authenticated_session, require_csrf
from app.api.responses import get_request_id, success_response
from app.core.sessions import SessionContext
from app.db.session import get_db
from app.schemas.chat import (
    ChatSessionCreateRequest,
    ChatSessionUpdateRequest,
    ChatTagCreateRequest,
)
from app.schemas.common import PaginationParams
from app.services.chat_service import ChatService

router = APIRouter()


def chat_service() -> ChatService:
    return ChatService()


@router.post("/sessions", status_code=http_status.HTTP_201_CREATED)
def create_session(
    payload: ChatSessionCreateRequest,
    request: Request,
    context: SessionContext = Depends(require_authenticated_session),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: ChatService = Depends(chat_service),
) -> dict[str, object]:
    result = service.create_session(
        db,
        user=context.user,
        title=payload.title,
        temporary_flag=payload.temporary_flag,
        request_id=get_request_id(request),
    )
    return success_response(result.model_dump(mode="json"), request)


@router.get("/sessions")
def list_sessions(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    context: SessionContext = Depends(require_authenticated_session),
    pagination: PaginationParams = Depends(pagination_params),
    db: Session = Depends(get_db),
    service: ChatService = Depends(chat_service),
) -> dict[str, object]:
    items, page_meta = service.list_sessions(
        db,
        user=context.user,
        status=status,
        query=q,
        pagination=pagination,
    )
    return success_response([item.model_dump(mode="json") for item in items], request, page_meta)


@router.get("/sessions/{chat_session_id}")
def get_session_detail(
    chat_session_id: int,
    request: Request,
    context: SessionContext = Depends(require_authenticated_session),
    db: Session = Depends(get_db),
    service: ChatService = Depends(chat_service),
) -> dict[str, object]:
    result = service.get_session_detail(
        db,
        user=context.user,
        chat_session_id=chat_session_id,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.patch("/sessions/{chat_session_id}")
def update_session_title(
    chat_session_id: int,
    payload: ChatSessionUpdateRequest,
    request: Request,
    context: SessionContext = Depends(require_authenticated_session),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: ChatService = Depends(chat_service),
) -> dict[str, object]:
    result = service.update_session_title(
        db,
        user=context.user,
        chat_session_id=chat_session_id,
        title=payload.title,
        request_id=get_request_id(request),
    )
    return success_response(result.model_dump(mode="json"), request)


@router.post("/sessions/{chat_session_id}/archive")
def archive_session(
    chat_session_id: int,
    request: Request,
    context: SessionContext = Depends(require_authenticated_session),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: ChatService = Depends(chat_service),
) -> dict[str, object]:
    result = service.archive_session(
        db,
        user=context.user,
        chat_session_id=chat_session_id,
        request_id=get_request_id(request),
    )
    return success_response(result.model_dump(mode="json"), request)


@router.get("/sessions/{chat_session_id}/messages")
def list_messages(
    chat_session_id: int,
    request: Request,
    include_internal_lineage: bool = False,
    context: SessionContext = Depends(require_authenticated_session),
    pagination: PaginationParams = Depends(pagination_params),
    db: Session = Depends(get_db),
    service: ChatService = Depends(chat_service),
) -> dict[str, object]:
    items, page_meta = service.list_messages(
        db,
        user=context.user,
        chat_session_id=chat_session_id,
        pagination=pagination,
        include_internal_lineage=include_internal_lineage,
        role_name=context.role_name,
    )
    if include_internal_lineage:
        data = [item.model_dump(mode="json") for item in items]
    else:
        data = [item.model_dump(mode="json", exclude={"linked_retrieval_run_id"}) for item in items]
    return success_response(data, request, page_meta)


@router.post("/sessions/{chat_session_id}/tags")
def add_tag(
    chat_session_id: int,
    payload: ChatTagCreateRequest,
    request: Request,
    response: Response,
    context: SessionContext = Depends(require_authenticated_session),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: ChatService = Depends(chat_service),
) -> dict[str, object]:
    result, created = service.add_tag(
        db,
        user=context.user,
        chat_session_id=chat_session_id,
        tag_name=payload.tag_name,
        request_id=get_request_id(request),
    )
    response.status_code = http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK
    return success_response(result.model_dump(mode="json"), request)


@router.delete("/sessions/{chat_session_id}/tags/{tag_name}")
def delete_tag(
    chat_session_id: int,
    request: Request,
    tag_name: str,
    context: SessionContext = Depends(require_authenticated_session),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: ChatService = Depends(chat_service),
) -> dict[str, object]:
    result = service.delete_tag(
        db,
        user=context.user,
        chat_session_id=chat_session_id,
        tag_name=tag_name,
        request_id=get_request_id(request),
    )
    return success_response(result.model_dump(mode="json"), request)
