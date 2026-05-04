from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Request

from app.core.config import get_settings
from app.core.security import hash_token, new_token
from app.db.models import User, UserSession


@dataclass(frozen=True)
class SessionContext:
    user: User
    session: UserSession
    role_name: str
    raw_session_token: str


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def session_is_active(session: UserSession, *, now: datetime | None = None) -> bool:
    current = now or now_utc()
    if session.revoked_at is not None:
        return False
    return ensure_aware(session.expires_at) > current


def new_session_token() -> str:
    settings = get_settings()
    return new_token("sess_", settings.session_token_bytes)


def new_session(
    *,
    user_id: int,
    raw_session_token: str,
    csrf_state_hash: str | None,
    user_agent: str | None,
    ip_address: str | None,
) -> UserSession:
    settings = get_settings()
    return UserSession(
        user_id=user_id,
        session_token_hash=hash_token(raw_session_token),
        csrf_state_hash=csrf_state_hash,
        user_agent=truncate_user_agent(user_agent),
        ip_address=ip_address,
        expires_at=now_utc() + timedelta(seconds=settings.session_cookie_max_age_seconds),
    )


def session_cookie_value(request: Request) -> str | None:
    return request.cookies.get(get_settings().session_cookie_name)


def client_ip(request: Request) -> str | None:
    peer_ip = request.client.host[:45] if request.client else None
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for and peer_ip in get_settings().trusted_proxy_ips:
        return forwarded_for.split(",", 1)[0].strip()[:45] or None
    return peer_ip


def user_agent(request: Request) -> str | None:
    return truncate_user_agent(request.headers.get("User-Agent"))


def truncate_user_agent(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:512]


def session_id_value(session: UserSession) -> UUID:
    return session.session_id
