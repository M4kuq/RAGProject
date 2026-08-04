from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.db.models import RagAbuseDenialBucket, RagRequestAdmission

_ADVISORY_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"rag-request-admission-v1").digest()[:8],
    "big",
    signed=True,
)


def acquire_admission_lock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _ADVISORY_LOCK_KEY},
        )


def prune_expired_records(
    db: Session,
    *,
    admission_cutoff: datetime,
    denial_cutoff: datetime,
    now: datetime,
) -> None:
    db.execute(
        delete(RagRequestAdmission).where(
            RagRequestAdmission.admitted_at < admission_cutoff,
            (RagRequestAdmission.released_at.is_not(None))
            | (RagRequestAdmission.lease_expires_at <= now),
        )
    )
    db.execute(
        delete(RagAbuseDenialBucket).where(RagAbuseDenialBucket.window_started_at < denial_cutoff)
    )


def request_count_since(
    db: Session,
    *,
    since: datetime,
    subject_hash: str | None = None,
) -> int:
    statement = (
        select(func.count())
        .select_from(RagRequestAdmission)
        .where(RagRequestAdmission.admitted_at >= since)
    )
    if subject_hash is not None:
        statement = statement.where(RagRequestAdmission.subject_hash == subject_hash)
    return int(db.scalar(statement) or 0)


def earliest_request_since(
    db: Session,
    *,
    since: datetime,
    subject_hash: str | None = None,
) -> datetime | None:
    statement = select(func.min(RagRequestAdmission.admitted_at)).where(
        RagRequestAdmission.admitted_at >= since
    )
    if subject_hash is not None:
        statement = statement.where(RagRequestAdmission.subject_hash == subject_hash)
    return db.scalar(statement)


def daily_work_units(
    db: Session,
    *,
    subject_hash: str | None,
    day_started_at: datetime,
) -> int:
    statement = select(func.coalesce(func.sum(RagRequestAdmission.charged_units), 0)).where(
        RagRequestAdmission.admitted_at >= day_started_at
    )
    if subject_hash is not None:
        statement = statement.where(RagRequestAdmission.subject_hash == subject_hash)
    return int(db.scalar(statement) or 0)


def active_lease_count(
    db: Session,
    *,
    now: datetime,
    subject_hash: str | None = None,
) -> int:
    statement = (
        select(func.count())
        .select_from(RagRequestAdmission)
        .where(
            RagRequestAdmission.released_at.is_(None),
            RagRequestAdmission.lease_expires_at > now,
        )
    )
    if subject_hash is not None:
        statement = statement.where(RagRequestAdmission.subject_hash == subject_hash)
    return int(db.scalar(statement) or 0)


def earliest_active_lease_expiry(
    db: Session,
    *,
    now: datetime,
    subject_hash: str | None = None,
) -> datetime | None:
    statement = select(func.min(RagRequestAdmission.lease_expires_at)).where(
        RagRequestAdmission.released_at.is_(None),
        RagRequestAdmission.lease_expires_at > now,
    )
    if subject_hash is not None:
        statement = statement.where(RagRequestAdmission.subject_hash == subject_hash)
    return db.scalar(statement)


def add_admission(
    db: Session,
    *,
    subject_hash: str,
    request_hash: str,
    strategy_type: str,
    charged_units: int,
    admitted_at: datetime,
    lease_expires_at: datetime,
) -> RagRequestAdmission:
    admission = RagRequestAdmission(
        subject_hash=subject_hash,
        request_hash=request_hash,
        strategy_type=strategy_type,
        charged_units=charged_units,
        admitted_at=admitted_at,
        lease_expires_at=lease_expires_at,
    )
    db.add(admission)
    db.flush()
    return admission


def release_admission(
    db: Session,
    *,
    admission_id: uuid.UUID,
    released_at: datetime,
    outcome: str,
) -> None:
    admission = db.get(RagRequestAdmission, admission_id)
    if admission is None or admission.released_at is not None:
        return
    admission.released_at = released_at
    admission.outcome = outcome
    db.flush()


def increment_denial_bucket(
    db: Session,
    *,
    scope: str,
    subject_hash: str,
    window_started_at: datetime,
    reason_code: str,
    seen_at: datetime,
) -> None:
    bucket = db.scalar(
        select(RagAbuseDenialBucket).where(
            RagAbuseDenialBucket.scope == scope,
            RagAbuseDenialBucket.subject_hash == subject_hash,
            RagAbuseDenialBucket.window_started_at == window_started_at,
            RagAbuseDenialBucket.reason_code == reason_code,
        )
    )
    if bucket is None:
        db.add(
            RagAbuseDenialBucket(
                scope=scope,
                subject_hash=subject_hash,
                window_started_at=window_started_at,
                reason_code=reason_code,
                denied_count=1,
                last_seen_at=seen_at,
            )
        )
    else:
        bucket.denied_count += 1
        bucket.last_seen_at = seen_at
    db.flush()
