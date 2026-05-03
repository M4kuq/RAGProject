from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_csrf
from app.db.models import ChatMessage, ChatSession, User
from app.db.session import get_db

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
    return {
        "data": {"chat_session_id": session.chat_session_id, "title": session.title},
        "meta": {},
    }


@router.get("/sessions")
def list_sessions(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    rows = db.scalars(select(ChatSession).where(ChatSession.user_id == user.user_id)).all()
    return {
        "data": [
            {
                "chat_session_id": row.chat_session_id,
                "title": row.title,
                "status": row.status,
                "temporary_flag": row.temporary_flag,
            }
            for row in rows
        ],
        "meta": {"pagination": {"page": 1, "page_size": 20, "total": len(rows)}},
    }


@router.post("/sessions/{chat_session_id}/messages")
def add_message(
    chat_session_id: int,
    payload: MessageCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = db.get(ChatSession, chat_session_id)
    if not session or session.user_id != user.user_id:
        return {"error": {"code": "not_found", "message": "chat session not found"}}
    message = ChatMessage(
        chat_session_id=chat_session_id,
        role="user",
        content=payload.content,
        client_message_id=payload.client_message_id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return {
        "data": {"chat_message_id": message.chat_message_id, "content": message.content},
        "meta": {},
    }
