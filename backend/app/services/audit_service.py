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
    target_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
            metadata_=metadata or {},
        )
    )
