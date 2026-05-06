from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, ChatSession, ChatTag, SystemSetting
from app.schemas.common import PaginationParams


class ChatRepository:
    def create_session(
        self,
        db: Session,
        *,
        user_id: int,
        title: str,
        temporary_flag: bool,
        ttl_expires_at: datetime | None,
    ) -> ChatSession:
        session = ChatSession(
            user_id=user_id,
            title=title,
            status="active",
            temporary_flag=temporary_flag,
            ttl_expires_at=ttl_expires_at,
        )
        db.add(session)
        db.flush()
        return session

    def get_session_for_user(
        self,
        db: Session,
        *,
        user_id: int,
        chat_session_id: int,
        for_update: bool = False,
    ) -> ChatSession | None:
        statement = select(ChatSession).where(
            ChatSession.chat_session_id == chat_session_id,
            ChatSession.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return db.scalar(statement)

    def list_sessions_for_user(
        self,
        db: Session,
        *,
        user_id: int,
        status: str,
        query: str | None,
        pagination: PaginationParams,
    ) -> tuple[list[ChatSession], int]:
        base = self._session_list_statement(user_id=user_id, status=status, query=query)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(
            base.order_by(ChatSession.updated_at.desc(), ChatSession.chat_session_id.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        ).all()
        return list(rows), total

    def list_messages(
        self,
        db: Session,
        *,
        chat_session_id: int,
        pagination: PaginationParams,
    ) -> tuple[list[ChatMessage], int]:
        base = select(ChatMessage).where(ChatMessage.chat_session_id == chat_session_id)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(
            base.order_by(ChatMessage.created_at.asc(), ChatMessage.chat_message_id.asc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        ).all()
        return list(rows), total

    def list_tags(self, db: Session, *, chat_session_id: int) -> list[ChatTag]:
        return list(
            db.scalars(
                select(ChatTag)
                .where(ChatTag.chat_session_id == chat_session_id)
                .order_by(ChatTag.tag_name.asc())
            ).all()
        )

    def get_tag(self, db: Session, *, chat_session_id: int, tag_name: str) -> ChatTag | None:
        return db.scalar(
            select(ChatTag).where(
                ChatTag.chat_session_id == chat_session_id,
                ChatTag.tag_name == tag_name,
            )
        )

    def create_tag(
        self,
        db: Session,
        *,
        chat_session_id: int,
        tag_name: str,
    ) -> ChatTag:
        tag = ChatTag(chat_session_id=chat_session_id, tag_name=tag_name)
        db.add(tag)
        db.flush()
        return tag

    def delete_tag(self, db: Session, *, chat_session_id: int, tag_name: str) -> int | None:
        tag = self.get_tag(db, chat_session_id=chat_session_id, tag_name=tag_name)
        if tag is None:
            return None
        tag_id = tag.chat_tag_id
        db.delete(tag)
        db.flush()
        return tag_id

    def update_session_title(
        self,
        db: Session,
        *,
        session: ChatSession,
        title: str,
        updated_at: datetime,
    ) -> ChatSession:
        session.title = title
        session.updated_at = updated_at
        db.flush()
        return session

    def archive_session(
        self,
        db: Session,
        *,
        session: ChatSession,
        archived_at: datetime,
    ) -> ChatSession:
        session.status = "archived"
        session.archived_at = archived_at
        session.updated_at = archived_at
        db.flush()
        return session

    def touch_session(
        self,
        db: Session,
        *,
        session: ChatSession,
        updated_at: datetime,
    ) -> None:
        session.updated_at = updated_at
        db.flush()

    def get_temporary_chat_ttl_minutes(self, db: Session) -> int | None:
        setting = db.get(SystemSetting, "chat.temporary_ttl_minutes")
        if not setting:
            return None
        value = setting.setting_value.get("value")
        if isinstance(value, int):
            return value
        return None

    def _session_list_statement(
        self,
        *,
        user_id: int,
        status: str,
        query: str | None,
    ) -> Select[tuple[ChatSession]]:
        statement = select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.temporary_flag.is_(False),
            ChatSession.status == status,
        )
        if query:
            statement = statement.where(ChatSession.title.ilike(f"%{query}%"))
        return statement
