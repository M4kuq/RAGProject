from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import AuditLog


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
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action_type=action,
            target_type=target_type or "system",
            target_id=target_id,
            request_id=request_id or "system",
            metadata_json=metadata or {},
        )
    )
