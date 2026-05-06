from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.responses import pagination_meta
from app.core.config import get_settings
from app.core.errors import (
    ArchivedSessionReadonly,
    PermissionDenied,
    ResourceNotFound,
    TemporarySessionExpired,
    TemporarySessionNotArchivable,
    ValidationFailed,
)
from app.core.security import hash_identifier
from app.db.models import ChatMessage, ChatSession, ChatTag, User
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat import (
    ChatArchiveResponse,
    ChatMessageItem,
    ChatMode,
    ChatSessionDetail,
    ChatSessionItem,
    ChatTagItem,
    ChatTagMutationResponse,
    normalize_tag_name,
    normalize_title,
)
from app.schemas.common import PaginationMeta, PaginationParams
from app.services.audit_service import audit

SessionStatus = Literal["active", "archived"]


class ChatService:
    def __init__(self, repository: ChatRepository | None = None) -> None:
        self.repository = repository or ChatRepository()

    def create_session(
        self,
        db: Session,
        *,
        user: User,
        title: str | None,
        temporary_flag: bool,
        request_id: str | None = None,
    ) -> ChatSessionDetail:
        normalized_title = self._normalize_title(title)
        now = self._now()
        ttl_minutes = self._temporary_chat_ttl_minutes(db) if temporary_flag else None
        ttl_expires_at = now + timedelta(minutes=ttl_minutes) if ttl_minutes is not None else None
        session = self.repository.create_session(
            db,
            user_id=user.user_id,
            title=normalized_title,
            temporary_flag=temporary_flag,
            ttl_expires_at=ttl_expires_at,
        )
        if temporary_flag:
            audit(
                db,
                action="chat.temporary_session_created",
                actor_user_id=user.user_id,
                request_id=request_id,
                target_type="chat_session",
                target_id=session.chat_session_id,
                metadata={"temporary_flag": True, "ttl_minutes": ttl_minutes},
            )
        db.commit()
        db.refresh(session)
        return self._session_detail(session, tags=[])

    def list_sessions(
        self,
        db: Session,
        *,
        user: User,
        status: str | None,
        query: str | None,
        pagination: PaginationParams,
    ) -> tuple[list[ChatSessionItem], PaginationMeta]:
        normalized_status = self._normalize_status(status)
        normalized_query = query.strip() if query and query.strip() else None
        if normalized_query and len(normalized_query) > 255:
            raise ValidationFailed(details=[{"field": "q", "reason": "q is too long."}])
        rows, total = self.repository.list_sessions_for_user(
            db,
            user_id=user.user_id,
            status=normalized_status,
            query=normalized_query,
            pagination=pagination,
        )
        return [self._session_item(row) for row in rows], pagination_meta(pagination, total)

    def get_session_detail(
        self,
        db: Session,
        *,
        user: User,
        chat_session_id: int,
    ) -> ChatSessionDetail:
        session = self._get_owned_session(db, user=user, chat_session_id=chat_session_id)
        return self._session_detail(
            session,
            tags=self.repository.list_tags(db, chat_session_id=session.chat_session_id),
        )

    def update_session_title(
        self,
        db: Session,
        *,
        user: User,
        chat_session_id: int,
        title: str,
        request_id: str | None = None,
    ) -> ChatSessionDetail:
        session = self._get_owned_session(
            db, user=user, chat_session_id=chat_session_id, for_update=True
        )
        self._ensure_session_writable(session)
        normalized_title = self._normalize_title(title)
        now = self._now()
        self.repository.update_session_title(
            db,
            session=session,
            title=normalized_title,
            updated_at=now,
        )
        audit(
            db,
            action="chat.title_updated",
            actor_user_id=user.user_id,
            request_id=request_id,
            target_type="chat_session",
            target_id=session.chat_session_id,
            metadata={"field": "title"},
        )
        db.commit()
        db.refresh(session)
        return self._session_detail(
            session,
            tags=self.repository.list_tags(db, chat_session_id=session.chat_session_id),
        )

    def archive_session(
        self,
        db: Session,
        *,
        user: User,
        chat_session_id: int,
        request_id: str | None = None,
    ) -> ChatArchiveResponse:
        session = self._get_owned_session(
            db, user=user, chat_session_id=chat_session_id, for_update=True
        )
        if session.temporary_flag:
            raise TemporarySessionNotArchivable()
        if session.status == "archived":
            result_code: Literal["archived", "already_archived"] = "already_archived"
        else:
            self.repository.archive_session(db, session=session, archived_at=self._now())
            audit(
                db,
                action="chat.archived",
                actor_user_id=user.user_id,
                request_id=request_id,
                target_type="chat_session",
                target_id=session.chat_session_id,
                metadata={"result": "archived"},
            )
            result_code = "archived"
        db.commit()
        return ChatArchiveResponse(
            chat_session_id=session.chat_session_id,
            status="archived",
            result_code=result_code,
        )

    def list_messages(
        self,
        db: Session,
        *,
        user: User,
        chat_session_id: int,
        pagination: PaginationParams,
        include_internal_lineage: bool = False,
        role_name: str = "viewer",
    ) -> tuple[list[ChatMessageItem], PaginationMeta]:
        if include_internal_lineage and role_name != "admin":
            raise PermissionDenied()
        self._get_owned_session(db, user=user, chat_session_id=chat_session_id)
        rows, total = self.repository.list_messages(
            db, chat_session_id=chat_session_id, pagination=pagination
        )
        return [self._message_item(row) for row in rows], pagination_meta(pagination, total)

    def ensure_session_can_append_messages(
        self,
        db: Session,
        *,
        user: User,
        chat_session_id: int,
    ) -> ChatSession:
        session = self._get_owned_session(
            db, user=user, chat_session_id=chat_session_id, for_update=True
        )
        self._ensure_session_writable(session)
        return session

    def add_tag(
        self,
        db: Session,
        *,
        user: User,
        chat_session_id: int,
        tag_name: str,
        request_id: str | None = None,
    ) -> tuple[ChatTagMutationResponse, bool]:
        session = self._get_owned_session(
            db, user=user, chat_session_id=chat_session_id, for_update=True
        )
        self._ensure_session_writable(session)
        normalized_tag = self._normalize_tag(tag_name)
        existing = self.repository.get_tag(
            db, chat_session_id=session.chat_session_id, tag_name=normalized_tag
        )
        if existing:
            db.commit()
            return (
                ChatTagMutationResponse(
                    chat_session_id=session.chat_session_id,
                    tag_name=normalized_tag,
                    result_code="already_exists",
                ),
                False,
            )
        try:
            with db.begin_nested():
                tag = self.repository.create_tag(
                    db,
                    chat_session_id=session.chat_session_id,
                    tag_name=normalized_tag,
                )
        except IntegrityError as exc:
            if self.repository.get_tag(
                db,
                chat_session_id=session.chat_session_id,
                tag_name=normalized_tag,
            ):
                db.commit()
                return (
                    ChatTagMutationResponse(
                        chat_session_id=session.chat_session_id,
                        tag_name=normalized_tag,
                        result_code="already_exists",
                    ),
                    False,
                )
            raise exc
        else:
            self.repository.touch_session(db, session=session, updated_at=self._now())
            audit(
                db,
                action="chat.tag_added",
                actor_user_id=user.user_id,
                request_id=request_id,
                target_type="chat_tag",
                target_id=tag.chat_tag_id,
                metadata=self._tag_audit_metadata(normalized_tag),
            )
            db.commit()
            return (
                ChatTagMutationResponse(
                    chat_session_id=session.chat_session_id,
                    tag_name=normalized_tag,
                    result_code="created",
                ),
                True,
            )

    def delete_tag(
        self,
        db: Session,
        *,
        user: User,
        chat_session_id: int,
        tag_name: str,
        request_id: str | None = None,
    ) -> ChatTagMutationResponse:
        session = self._get_owned_session(
            db, user=user, chat_session_id=chat_session_id, for_update=True
        )
        self._ensure_session_writable(session)
        normalized_tag = self._normalize_tag(tag_name)
        deleted_tag_id = self.repository.delete_tag(
            db, chat_session_id=session.chat_session_id, tag_name=normalized_tag
        )
        if deleted_tag_id is not None:
            self.repository.touch_session(db, session=session, updated_at=self._now())
            audit(
                db,
                action="chat.tag_deleted",
                actor_user_id=user.user_id,
                request_id=request_id,
                target_type="chat_tag",
                target_id=deleted_tag_id,
                metadata=self._tag_audit_metadata(normalized_tag),
            )
        db.commit()
        return ChatTagMutationResponse(
            chat_session_id=session.chat_session_id,
            tag_name=normalized_tag,
            result_code="deleted" if deleted_tag_id is not None else "not_found_no_op",
        )

    def _get_owned_session(
        self,
        db: Session,
        *,
        user: User,
        chat_session_id: int,
        for_update: bool = False,
    ) -> ChatSession:
        session = self.repository.get_session_for_user(
            db,
            user_id=user.user_id,
            chat_session_id=chat_session_id,
            for_update=for_update,
        )
        if session is None:
            raise ResourceNotFound()
        return session

    def _ensure_session_writable(self, session: ChatSession) -> None:
        if session.status == "archived":
            raise ArchivedSessionReadonly()
        if self.is_temporary_expired(session):
            raise TemporarySessionExpired()

    def is_temporary_expired(self, session: ChatSession) -> bool:
        expires_at = session.ttl_expires_at
        if not session.temporary_flag or expires_at is None:
            return False
        return self._aware_utc(expires_at) <= self._now()

    def display_status(self, session: ChatSession) -> ChatMode:
        if session.status == "archived":
            return "archived"
        if self.is_temporary_expired(session):
            return "temporary_expired"
        if session.temporary_flag:
            return "temporary"
        return "active"

    def _session_item(self, session: ChatSession) -> ChatSessionItem:
        mode = self.display_status(session)
        return ChatSessionItem(
            chat_session_id=session.chat_session_id,
            title=session.title,
            status=session.status,  # type: ignore[arg-type]
            display_status=mode,
            mode=mode,
            temporary_flag=session.temporary_flag,
            ttl_expires_at=self._optional_aware_utc(session.ttl_expires_at),
            created_at=self._aware_utc(session.created_at),
            updated_at=self._aware_utc(session.updated_at),
        )

    def _session_detail(self, session: ChatSession, *, tags: list[ChatTag]) -> ChatSessionDetail:
        return ChatSessionDetail(
            **self._session_item(session).model_dump(),
            tags=[self._tag_item(tag) for tag in tags],
        )

    def _message_item(self, message: ChatMessage) -> ChatMessageItem:
        return ChatMessageItem(
            chat_message_id=message.chat_message_id,
            chat_session_id=message.chat_session_id,
            role=message.role,  # type: ignore[arg-type]
            content=message.content,
            client_message_id=message.client_message_id,
            linked_retrieval_run_id=message.linked_retrieval_run_id,
            edited_flag=message.edited_flag,
            created_at=self._aware_utc(message.created_at),
            updated_at=self._aware_utc(message.updated_at),
        )

    def _tag_item(self, tag: ChatTag) -> ChatTagItem:
        return ChatTagItem(
            chat_session_id=tag.chat_session_id,
            tag_name=tag.tag_name,
            created_at=self._optional_aware_utc(tag.created_at),
        )

    def _tag_audit_metadata(self, tag_name: str) -> dict[str, object]:
        return {
            "tag_name_hash": hash_identifier(tag_name),
            "tag_name_length": len(tag_name),
        }

    def _temporary_chat_ttl_minutes(self, db: Session) -> int:
        configured = self.repository.get_temporary_chat_ttl_minutes(db)
        ttl = configured if configured is not None else get_settings().temp_chat_ttl_minutes
        return ttl if ttl > 0 else 120

    def _normalize_status(self, status: str | None) -> SessionStatus:
        if status is None:
            return "active"
        if status not in {"active", "archived"}:
            raise ValidationFailed(details=[{"field": "status", "reason": "Invalid status."}])
        return status  # type: ignore[return-value]

    def _normalize_title(self, title: str | None) -> str:
        try:
            return normalize_title(title)
        except ValueError as exc:
            raise ValidationFailed(details=[{"field": "title", "reason": str(exc)}]) from exc

    def _normalize_tag(self, tag_name: str) -> str:
        try:
            return normalize_tag_name(tag_name)
        except ValueError as exc:
            raise ValidationFailed(details=[{"field": "tag_name", "reason": str(exc)}]) from exc

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _aware_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _optional_aware_utc(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return self._aware_utc(value)
