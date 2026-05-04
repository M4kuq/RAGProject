from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_token
from app.db.models import UserSession


def get_session_by_raw_token(db: Session, raw_session_token: str) -> UserSession | None:
    return db.scalar(
        select(UserSession).where(UserSession.session_token_hash == hash_token(raw_session_token))
    )


def add_session(db: Session, session: UserSession) -> None:
    db.add(session)


def update_csrf_state(db: Session, session: UserSession, csrf_state_hash: str | None) -> None:
    session.csrf_state_hash = csrf_state_hash
    db.add(session)


def revoke_session(db: Session, session: UserSession, revoked_at: datetime) -> None:
    session.revoked_at = revoked_at
    session.csrf_state_hash = None
    db.add(session)
