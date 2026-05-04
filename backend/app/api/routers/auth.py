from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.security import hash_token, new_token, verify_password
from app.db.models import Role, User, UserSession
from app.db.session import get_db
from app.services.audit_service import audit

router = APIRouter()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _user_payload(db: Session, user: User) -> dict[str, object]:
    role = db.get(Role, user.role_id)
    return {
        "user_id": user.user_id,
        "email": user.email,
        "display_name": user.display_name,
        "role": role.role_name if role else "viewer",
    }


def _cookie_settings() -> dict[str, Any]:
    settings = get_settings()
    return {
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite,
        "max_age": settings.session_cookie_max_age_seconds,
    }


@router.get("/csrf")
def csrf(request: Request, response: Response) -> dict[str, object]:
    settings = get_settings()
    token = new_token()
    response.set_cookie(settings.csrf_cookie_name, token, httponly=False, **_cookie_settings())
    response.headers["Cache-Control"] = "no-store"
    return success_response({"csrf_token": token}, request)


@router.post("/login")
def login(
    payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, object]:
    settings = get_settings()
    email = payload.email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    raw_session = new_token()
    csrf_token = new_token()
    session = UserSession(
        session_id=uuid4(),
        user_id=user.user_id,
        session_token_hash=hash_token(raw_session),
        csrf_state_hash=hash_token(csrf_token),
        expires_at=datetime.now(UTC) + timedelta(hours=8),
    )
    db.add(session)
    user.last_login_at = datetime.now(UTC)
    audit(
        db,
        action="auth.login",
        actor_user_id=user.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    response.set_cookie(
        settings.session_cookie_name, raw_session, httponly=True, **_cookie_settings()
    )
    response.set_cookie(settings.csrf_cookie_name, csrf_token, httponly=False, **_cookie_settings())
    return success_response({"user": _user_payload(db, user), "csrf_token": csrf_token}, request)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    response.delete_cookie(
        settings.csrf_cookie_name,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    for session in db.scalars(select(UserSession).where(UserSession.user_id == user.user_id)).all():
        session.revoked_at = datetime.now(UTC)
    audit(
        db,
        action="auth.logout",
        actor_user_id=user.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    return success_response({"result_code": "logged_out"}, request)


@router.get("/me")
def me(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return success_response({"user": _user_payload(db, user)}, request)
