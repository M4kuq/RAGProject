from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.job_utils import LeaseLostError
from app.db.models import ChatSession
from app.db.session import SessionLocal
from app.repositories.chat_repository import ChatRepository
from app.repositories.job_repository import JobRepository
from app.services.audit_service import audit
from app.workers.handlers.base import JobExecutionContext, JobHandlerResult


class TemporaryChatCleanupHandler:
    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        chat_repository: ChatRepository | None = None,
        job_repository: JobRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.chat_repository = chat_repository or ChatRepository()
        self.job_repository = job_repository or JobRepository()

    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        chat_session_id = _chat_session_id(context)
        if context.target_type != "chat_session" or chat_session_id is None:
            return JobHandlerResult.failed(
                error_code="validation_error",
                error_message="Job payload is invalid.",
            )
        with self.session_factory() as db:
            try:
                self.job_repository.assert_ownership(
                    db,
                    job_id=context.job_id,
                    worker_instance_id=context.worker_instance_id,
                )
                session = self.chat_repository.get_session_for_update(
                    db, chat_session_id=chat_session_id
                )
                if session is None or not _is_expired_temporary(session):
                    return JobHandlerResult.succeeded(
                        {"result": "no_op", "chat_session_id": chat_session_id}
                    )
                counts = self.chat_repository.count_session_children(
                    db, chat_session_id=chat_session_id
                )
                self.job_repository.assert_ownership(
                    db,
                    job_id=context.job_id,
                    worker_instance_id=context.worker_instance_id,
                )
                self.chat_repository.delete_session(db, session=session)
                audit(
                    db,
                    action="temporary_chat_cleanup",
                    actor_user_id=None,
                    request_id=f"job:{context.job_id}",
                    target_type="chat_session",
                    target_id=chat_session_id,
                    metadata=dict(counts),
                )
                db.commit()
            except LeaseLostError:
                raise
            except Exception:
                db.rollback()
                return JobHandlerResult.failed(
                    error_code="cleanup_failed",
                    error_message="Temporary chat cleanup failed.",
                )
        return JobHandlerResult.succeeded(
            {"result": "deleted", "chat_session_id": chat_session_id, **counts}
        )


def _chat_session_id(context: JobExecutionContext) -> int | None:
    if context.target_id is not None:
        if isinstance(context.target_id, bool) or context.target_id < 1:
            return None
        return int(context.target_id)
    raw = context.payload.get("chat_session_id")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None
    return raw


def _is_expired_temporary(session: ChatSession) -> bool:
    expires_at = session.ttl_expires_at
    if not session.temporary_flag or expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    else:
        expires_at = expires_at.astimezone(UTC)
    return expires_at <= datetime.now(UTC)
