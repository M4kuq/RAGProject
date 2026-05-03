from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, pagination_params, require_csrf
from app.api.responses import paginate, success_response
from app.db.models import ChatMessage, ChatSession, User
from app.db.session import get_db
from app.schemas.common import PaginationParams

router = APIRouter(dependencies=[Depends(require_csrf)])


class ChatSessionCreate(BaseModel):
    title: str | None = None
    temporary: bool = False


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    client_message_id: str | None = None


@router.post("/sessions")
def create_session(
    payload: ChatSessionCreate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = ChatSession(
        user_id=user.user_id,
        title=payload.title or "New chat",
        temporary_flag=payload.temporary,
        ttl_expires_at=datetime.now(UTC) + timedelta(hours=24) if payload.temporary else None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return success_response(
        {"chat_session_id": session.chat_session_id, "title": session.title},
        request,
    )


@router.get("/sessions")
def list_sessions(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(pagination_params),
) -> dict[str, object]:
    rows = db.scalars(select(ChatSession).where(ChatSession.user_id == user.user_id)).all()
    page_rows, page_meta = paginate(rows, pagination)
    return success_response(
        [
            {
                "chat_session_id": row.chat_session_id,
                "title": row.title,
                "status": row.status,
                "temporary_flag": row.temporary_flag,
            }
            for row in page_rows
        ],
        request,
        pagination=page_meta,
    )


@router.post("/sessions/{chat_session_id}/messages")
def add_message(
    chat_session_id: int,
    payload: MessageCreate,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = db.get(ChatSession, chat_session_id)
    if not session or session.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="not_found")
    message = ChatMessage(
        chat_session_id=chat_session_id,
        role="user",
        content=payload.content,
        client_message_id=payload.client_message_id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return success_response(
        {"chat_message_id": message.chat_message_id, "content": message.content},
        request,
    )
