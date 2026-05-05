from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.csrf import (
    csrf_header_value,
    make_pre_auth_state,
    new_csrf_token,
    pre_auth_cookie_value,
    validate_origin_or_referer,
    verify_pre_auth_state,
)
from app.core.errors import CsrfInvalid, CsrfMissing
from app.core.security import hash_token, verify_token_hash
from app.core.sessions import session_cookie_value, session_is_active
from app.db.models import UserSession
from app.repositories import session_repository, user_repository
from app.services.audit_service import audit_from_request

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def issue_csrf_token(request: Request, response: Response, db: Session) -> str:
    raw_session_token = session_cookie_value(request)
    if raw_session_token:
        session = session_repository.get_session_by_raw_token(db, raw_session_token)
        if session and session_is_active(session):
            user = user_repository.get_user_by_id(db, session.user_id)
            if user and user.status == "active":
                raw_csrf_token = new_csrf_token()
                session_repository.update_csrf_state(db, session, hash_token(raw_csrf_token))
                db.commit()
                _expire_pre_auth_cookie(response)
                response.headers["Cache-Control"] = "no-store"
                return raw_csrf_token

    raw_csrf_token = new_csrf_token()
    response.set_cookie(
        get_settings().csrf_cookie_name,
        make_pre_auth_state(raw_csrf_token),
        httponly=True,
        **_cookie_settings(max_age=get_settings().csrf_pre_auth_max_age_seconds),
    )
    response.headers["Cache-Control"] = "no-store"
    return raw_csrf_token


def validate_pre_auth_csrf(request: Request, db: Session) -> None:
    try:
        raw_csrf_token = csrf_header_value(request)
        if not raw_csrf_token:
            raise CsrfMissing()
        validate_origin_or_referer(request)
        verify_pre_auth_state(raw_csrf_token, pre_auth_cookie_value(request))
    except CsrfMissing:
        _audit_csrf_failure(db, request, "csrf_missing")
        raise
    except CsrfInvalid:
        _audit_csrf_failure(db, request, "csrf_invalid")
        raise


def validate_csrf(request: Request, db: Session) -> None:
    if request.method in SAFE_METHODS:
        return
    try:
        raw_csrf_token = csrf_header_value(request)
        if not raw_csrf_token:
            raise CsrfMissing()
        validate_origin_or_referer(request)
        raw_session_token = session_cookie_value(request)
        if not raw_session_token:
            raise CsrfInvalid()
        session = session_repository.get_session_by_raw_token(db, raw_session_token)
        if not session or not session_is_active(session):
            raise CsrfInvalid()
        if verify_token_hash(raw_csrf_token, session.csrf_state_hash):
            return
        raise CsrfInvalid()
    except CsrfMissing:
        _audit_csrf_failure(db, request, "csrf_missing")
        raise
    except CsrfInvalid:
        _audit_csrf_failure(db, request, "csrf_invalid")
        raise


def validate_session_csrf(request: Request, db: Session, session: UserSession) -> None:
    if request.method in SAFE_METHODS:
        return
    try:
        raw_csrf_token = csrf_header_value(request)
        if not raw_csrf_token:
            raise CsrfMissing()
        validate_origin_or_referer(request)
        if not session_is_active(session):
            raise CsrfInvalid()
        if not verify_token_hash(raw_csrf_token, session.csrf_state_hash):
            raise CsrfInvalid()
    except CsrfMissing:
        _audit_csrf_failure(db, request, "csrf_missing")
        raise
    except CsrfInvalid:
        _audit_csrf_failure(db, request, "csrf_invalid")
        raise


def expire_pre_auth_cookie(response: Response) -> None:
    _expire_pre_auth_cookie(response)


def _cookie_settings(max_age: int) -> dict[str, Any]:
    settings = get_settings()
    return {
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite,
        "max_age": max_age,
        "path": "/",
    }


def _expire_pre_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        get_settings().csrf_cookie_name,
        path="/",
        secure=get_settings().session_cookie_secure,
        samesite=get_settings().session_cookie_samesite,
    )


def _audit_csrf_failure(db: Session, request: Request, code: str) -> None:
    try:
        audit_from_request(
            db,
            request,
            action="auth.csrf_rejected",
            actor_user_id=None,
            target_type="auth",
            metadata={"error_code": code, "method": request.method, "path": request.url.path},
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
