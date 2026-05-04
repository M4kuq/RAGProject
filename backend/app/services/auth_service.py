from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.csrf import new_csrf_token
from app.core.errors import AuthenticationFailed, RateLimitExceeded
from app.core.permissions import KNOWN_ROLES
from app.core.security import (
    hash_identifier,
    hash_token,
    login_rate_limiter,
    normalize_email,
    verify_password_or_dummy,
)
from app.core.sessions import (
    SessionContext,
    client_ip,
    new_session,
    new_session_token,
    now_utc,
)
from app.db.models import User
from app.repositories import session_repository, user_repository
from app.schemas.users import UserPublic
from app.services.audit_service import audit_from_request


@dataclass(frozen=True)
class LoginResult:
    user: UserPublic
    raw_session_token: str


def login(db: Session, request: Request, *, email: str, password: str) -> LoginResult:
    normalized_email = normalize_email(email)
    ip_address = client_ip(request)
    if not login_rate_limiter.check_allowed(normalized_email, ip_address):
        _audit_login_failure(db, request, normalized_email, "rate_limit_exceeded")
        db.commit()
        raise RateLimitExceeded()

    user = user_repository.get_user_by_email(db, normalized_email)
    role_name = user_repository.get_role_name(db, user) if user else None
    password_ok = verify_password_or_dummy(password, user.password_hash if user else None)
    credentials_ok = (
        user is not None and user.status == "active" and role_name in KNOWN_ROLES and password_ok
    )
    if not credentials_ok:
        login_rate_limiter.record_failure(normalized_email, ip_address)
        _audit_login_failure(db, request, normalized_email, "authentication_failed")
        db.commit()
        raise AuthenticationFailed()

    assert user is not None
    assert role_name is not None
    login_rate_limiter.reset(normalized_email, ip_address)
    raw_session_token = new_session_token()
    session = new_session(
        user_id=user.user_id,
        raw_session_token=raw_session_token,
        csrf_state_hash=hash_token(new_csrf_token()),
        user_agent=request.headers.get("User-Agent"),
        ip_address=ip_address,
    )
    session_repository.add_session(db, session)
    user.last_login_at = now_utc()
    audit_from_request(
        db,
        request,
        action="auth.login_success",
        actor_user_id=user.user_id,
        target_type="user",
        target_id=user.user_id,
        metadata={"result": "success"},
    )
    db.commit()
    db.refresh(user)
    return LoginResult(user=user_to_public(user, role_name), raw_session_token=raw_session_token)


def logout(db: Session, request: Request, context: SessionContext) -> None:
    session_repository.revoke_session(db, context.session, now_utc())
    audit_from_request(
        db,
        request,
        action="auth.logout_success",
        actor_user_id=context.user.user_id,
        target_type="user_session",
        target_id=None,
        metadata={"session_revoked": True},
    )
    db.commit()


def user_to_public(user: User, role_name: str) -> UserPublic:
    return UserPublic(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        role=role_name,
    )


def _audit_login_failure(
    db: Session, request: Request, normalized_email: str, error_code: str
) -> None:
    audit_from_request(
        db,
        request,
        action="auth.login_failure",
        actor_user_id=None,
        target_type="auth",
        metadata={
            "result": "failure",
            "error_code": error_code,
            "credential_identifier": hash_identifier(normalized_email),
        },
    )
