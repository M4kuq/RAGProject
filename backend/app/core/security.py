from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field

from passlib.context import CryptContext
from passlib.exc import UnknownHashError

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_DUMMY_PASSWORD_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeO6Xq1yHYGg4KzucLSlbJjY1dxT9fF6Cy"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return pwd_context.verify(password, password_hash)
    except (TypeError, ValueError, UnknownHashError):
        return False


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    return verify_password(password, password_hash or _DUMMY_PASSWORD_HASH)


def new_token(prefix: str = "", nbytes: int | None = None) -> str:
    size = nbytes or get_settings().session_token_bytes
    return f"{prefix}{secrets.token_urlsafe(size)}"


def hash_token(token: str) -> str:
    secret = get_settings().session_secret.encode("utf-8")
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_token_hash(token: str, token_hash: str | None) -> bool:
    if not token_hash:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_identifier(value: str) -> str:
    return hash_token(value.strip().lower())


@dataclass
class LoginRateLimitEntry:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0
    last_seen: float = 0.0


class LoginRateLimiter:
    def __init__(self) -> None:
        self._entries: dict[str, LoginRateLimitEntry] = defaultdict(LoginRateLimitEntry)

    def _key(self, normalized_email: str, client_ip: str | None) -> str:
        return hash_token(f"{normalized_email}:{client_ip or 'unknown'}")

    def check_allowed(self, normalized_email: str, client_ip: str | None) -> bool:
        settings = get_settings()
        now = time.monotonic()
        entry = self._entries[self._key(normalized_email, client_ip)]
        entry.last_seen = now
        if entry.locked_until > now:
            return False
        cutoff = now - settings.login_rate_limit_window_seconds
        entry.failures = [failure for failure in entry.failures if failure >= cutoff]
        return True

    def record_failure(self, normalized_email: str, client_ip: str | None) -> None:
        settings = get_settings()
        now = time.monotonic()
        self._prune(now)
        entry = self._entries[self._key(normalized_email, client_ip)]
        entry.last_seen = now
        cutoff = now - settings.login_rate_limit_window_seconds
        entry.failures = [failure for failure in entry.failures if failure >= cutoff]
        entry.failures.append(now)
        if len(entry.failures) >= settings.login_rate_limit_max_attempts:
            entry.locked_until = now + settings.login_rate_limit_lock_seconds

    def reset(self, normalized_email: str, client_ip: str | None) -> None:
        self._entries.pop(self._key(normalized_email, client_ip), None)

    def reset_all(self) -> None:
        self._entries.clear()

    def _prune(self, now: float) -> None:
        settings = get_settings()
        cutoff = now - max(
            settings.login_rate_limit_window_seconds,
            settings.login_rate_limit_lock_seconds,
        )
        stale_keys = [
            key
            for key, entry in self._entries.items()
            if entry.last_seen < cutoff and entry.locked_until < now
        ]
        for key in stale_keys:
            self._entries.pop(key, None)
        overflow = len(self._entries) - settings.login_rate_limit_max_keys
        if overflow <= 0:
            return
        oldest_keys = sorted(
            self._entries,
            key=lambda key: self._entries[key].last_seen,
        )[:overflow]
        for key in oldest_keys:
            self._entries.pop(key, None)


login_rate_limiter = LoginRateLimiter()
