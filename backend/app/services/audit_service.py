from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.api.responses import get_request_id
from app.core.sessions import client_ip, user_agent
from app.repositories.audit_repository import add_audit_log

_FORBIDDEN_METADATA_KEY_PARTS = (
    "password",
    "session_token",
    "csrf",
    "token",
    "secret",
    "prompt",
    "content",
    "document_text",
    "payload",
)
_MAX_METADATA_STRING_LENGTH = 512


def safe_metadata(metadata: dict[str, object] | None) -> dict[str, object]:
    if not metadata:
        return {}
    return _redact_metadata(metadata)


def _redact_metadata(value: object, key: str | None = None) -> Any:
    if key and _is_forbidden_metadata_key(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_metadata(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_metadata(item) for item in value]
    if isinstance(value, str) and len(value) > _MAX_METADATA_STRING_LENGTH:
        return f"{value[:_MAX_METADATA_STRING_LENGTH]}...[truncated]"
    return value


def _is_forbidden_metadata_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _FORBIDDEN_METADATA_KEY_PARTS)


def audit_from_request(
    db: Session,
    request: Request,
    *,
    action: str,
    actor_user_id: int | None,
    target_type: str,
    target_id: int | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    add_audit_log(
        db,
        action_type=action,
        actor_user_id=actor_user_id,
        request_id=get_request_id(request) or "system",
        target_type=target_type,
        target_id=target_id,
        metadata_json=safe_metadata(metadata),
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )


def audit(
    db: Session,
    *,
    action: str,
    actor_user_id: int | None,
    request_id: str | None,
    target_type: str | None = None,
    target_id: int | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    add_audit_log(
        db,
        action_type=action,
        actor_user_id=actor_user_id,
        request_id=request_id or "system",
        target_type=target_type or "system",
        target_id=target_id,
        metadata_json=safe_metadata(metadata),
    )
