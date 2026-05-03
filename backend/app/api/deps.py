from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_token
from app.db.models import Role, User, UserSession
from app.db.session import get_db
from app.schemas.common import PaginationParams


def current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    session_token = request.cookies.get(get_settings().session_cookie_name)
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthenticated")
    token_hash = hash_token(session_token)
    session = db.scalar(select(UserSession).where(UserSession.session_token_hash == token_hash))
    if not session or session.revoked_at or session.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthenticated")
    user = db.get(User, session.user_id)
    if not user or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthenticated")
    return user


def require_csrf(
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    session_token = request.cookies.get(get_settings().session_cookie_name)
    if not session_token or not x_csrf_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf_required")
    session = db.scalar(
        select(UserSession).where(UserSession.session_token_hash == hash_token(session_token))
    )
    if not session or session.csrf_state_hash != hash_token(x_csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf_invalid")


def require_admin(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    role = db.get(Role, user.role_id)
    if not role or role.role_name != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    return user


def pagination_params(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)
