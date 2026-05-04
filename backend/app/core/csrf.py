from __future__ import annotations

import hmac
import time
from urllib.parse import urlparse

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import CsrfInvalid, CsrfMissing
from app.core.security import hash_token, new_token, verify_token_hash


def new_csrf_token() -> str:
    settings = get_settings()
    return new_token("csrf_", settings.csrf_token_bytes)


def make_pre_auth_state(raw_csrf_token: str, *, issued_at: int | None = None) -> str:
    timestamp = issued_at or int(time.time())
    payload = f"{timestamp}.{hash_token(raw_csrf_token)}"
    signature = hmac.new(
        get_settings().session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        "sha256",
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_pre_auth_state(raw_csrf_token: str, signed_state: str | None) -> bool:
    if not raw_csrf_token:
        raise CsrfMissing()
    if not signed_state:
        raise CsrfInvalid()
    parts = signed_state.split(".")
    if len(parts) != 3:
        raise CsrfInvalid()
    issued_at_raw, expected_hash, signature = parts
    payload = f"{issued_at_raw}.{expected_hash}"
    expected_signature = hmac.new(
        get_settings().session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        "sha256",
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise CsrfInvalid()
    try:
        issued_at = int(issued_at_raw)
    except ValueError as exc:
        raise CsrfInvalid() from exc
    if issued_at + get_settings().csrf_pre_auth_max_age_seconds < int(time.time()):
        raise CsrfInvalid()
    if not verify_token_hash(raw_csrf_token, expected_hash):
        raise CsrfInvalid()
    return True


def validate_origin_or_referer(request: Request) -> None:
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if origin:
        if _origin_allowed(origin, request):
            return
        raise CsrfInvalid()
    if referer:
        parsed = urlparse(referer)
        referer_origin = (
            f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        )
        if referer_origin and _origin_allowed(referer_origin, request):
            return
        raise CsrfInvalid()
    raise CsrfInvalid()


def csrf_header_value(request: Request) -> str | None:
    return request.headers.get(get_settings().csrf_header_name)


def pre_auth_cookie_value(request: Request) -> str | None:
    return request.cookies.get(get_settings().csrf_cookie_name)


def _origin_allowed(origin: str, request: Request) -> bool:
    allowed = set(get_settings().cors_allowed_origins)
    request_origin = f"{request.url.scheme}://{request.url.netloc}"
    allowed.add(request_origin)
    return origin in allowed
