from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationRequired
from app.core.permissions import ensure_admin, ensure_role, ensure_viewer_or_admin
from app.core.sessions import SessionContext, session_cookie_value, session_is_active
from app.db.models import User
from app.db.session import get_db
from app.repositories import session_repository, user_repository
from app.schemas.common import PaginationParams
from app.services.csrf_service import validate_pre_auth_csrf, validate_session_csrf


def require_authenticated_session(
    request: Request,
    db: Session = Depends(get_db),
) -> SessionContext:
    raw_session_token = session_cookie_value(request)
    if not raw_session_token:
        raise AuthenticationRequired()
    session = session_repository.get_session_by_raw_token(db, raw_session_token)
    if not session or not session_is_active(session):
        raise AuthenticationRequired()
    user = user_repository.get_user_by_id(db, session.user_id)
    if not user or user.status != "active":
        raise AuthenticationRequired()
    role_name = user_repository.get_role_name(db, user)
    if role_name not in {"admin", "viewer"}:
        raise AuthenticationRequired()
    context = SessionContext(
        user=user,
        session=session,
        role_name=role_name,
        raw_session_token=raw_session_token,
    )
    request.state.current_session = context
    return context


def get_current_user(context: SessionContext = Depends(require_authenticated_session)) -> User:
    return context.user


def current_user(context: SessionContext = Depends(require_authenticated_session)) -> User:
    return context.user


def require_authenticated_user(
    context: SessionContext = Depends(require_authenticated_session),
) -> User:
    return context.user


def require_csrf(
    request: Request,
    context: SessionContext = Depends(require_authenticated_session),
    db: Session = Depends(get_db),
) -> None:
    validate_session_csrf(request, db, context.session)


def require_pre_auth_csrf(
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    validate_pre_auth_csrf(request, db)


def require_role(*allowed_roles: str) -> Callable[[SessionContext], User]:
    def dependency(context: SessionContext = Depends(require_authenticated_session)) -> User:
        ensure_role(context.role_name, allowed_roles)
        return context.user

    return dependency


def require_admin(context: SessionContext = Depends(require_authenticated_session)) -> User:
    ensure_admin(context.role_name)
    return context.user


def require_viewer_or_admin(
    context: SessionContext = Depends(require_authenticated_session),
) -> User:
    ensure_viewer_or_admin(context.role_name)
    return context.user


def pagination_params(
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)
