from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def add_audit_log(
    db: Session,
    *,
    action_type: str,
    actor_user_id: int | None,
    request_id: str,
    target_type: str,
    target_id: int | None = None,
    metadata_json: dict[str, object] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
            metadata_json=metadata_json or {},
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )
