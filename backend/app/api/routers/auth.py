from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_session, require_csrf, require_pre_auth_csrf
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.sessions import SessionContext
from app.db.session import get_db
from app.schemas.auth import LoginRequest
from app.services import auth_service, csrf_service

router = APIRouter()


@router.get("/csrf")
def csrf(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, object]:
    token = csrf_service.issue_csrf_token(request, response, db)
    return success_response({"csrf_token": token}, request)


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    _: None = Depends(require_pre_auth_csrf),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = auth_service.login(db, request, email=str(payload.email), password=payload.password)
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        result.raw_session_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        max_age=settings.session_cookie_max_age_seconds,
        expires=datetime.now(UTC) + timedelta(seconds=settings.session_cookie_max_age_seconds),
        path="/",
    )
    csrf_service.expire_pre_auth_cookie(response)
    return success_response({"user": result.user.model_dump(mode="json")}, request)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    context: SessionContext = Depends(require_authenticated_session),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    auth_service.logout(db, request, context)
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    csrf_service.expire_pre_auth_cookie(response)
    return success_response({"status": "logged_out"}, request)


@router.get("/me")
def me(
    request: Request,
    context: SessionContext = Depends(require_authenticated_session),
) -> dict[str, object]:
    user = auth_service.user_to_public(context.user, context.role_name)
    return success_response(user.model_dump(mode="json"), request)
