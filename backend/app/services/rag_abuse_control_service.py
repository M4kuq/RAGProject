from __future__ import annotations

import hashlib
import hmac
import math
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.repositories import rag_abuse_control_repository as repository

_PROCESS_ADMISSION_LOCK = threading.RLock()
_GLOBAL_SUBJECT = "global-capacity"
_MAX_WORK_UNITS = 8
_STRATEGY_WORK_UNITS = {
    "dense": 1,
    "hybrid": 2,
    "graph": 4,
    "graph_postgres": 4,
    "graph_neo4j": 4,
    "agentic_router": 6,
    "llm_tool_orchestrator": 8,
    "langchain_agentic": 8,
    "langgraph_agentic": 8,
}
_TERMINAL_OUTCOMES = {"succeeded", "failed", "replayed"}


@dataclass(frozen=True)
class RagAdmissionPermit:
    admission_id: uuid.UUID | None
    charged_units: int


class RagAdmissionDenied(Exception):
    def __init__(
        self,
        *,
        reason_code: str,
        status_code: int,
        retry_after_seconds: int,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class RagAdmissionUnavailable(Exception):
    reason_code = "rag_admission_unavailable"
    status_code = 503
    retry_after_seconds = 5


class RagAbuseControlService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def admit(
        self,
        db: Session,
        *,
        user_id: int,
        request_id: str,
        strategy_type: str,
        now: datetime | None = None,
    ) -> RagAdmissionPermit:
        if not self.settings.rag_abuse_control_enabled:
            return RagAdmissionPermit(admission_id=None, charged_units=0)
        observed_at = _as_utc(now or datetime.now(UTC))
        subject_hash = self._opaque_hash("subject", str(user_id))
        request_hash = self._opaque_hash("request", request_id)
        charged_units = _work_units(strategy_type)
        lock = (
            _PROCESS_ADMISSION_LOCK if db.get_bind().dialect.name != "postgresql" else _NullLock()
        )
        try:
            with lock:
                repository.acquire_admission_lock(db)
                retention_cutoff = observed_at - timedelta(
                    days=self.settings.rag_abuse_audit_retention_days
                )
                repository.prune_expired_records(
                    db,
                    admission_cutoff=retention_cutoff,
                    denial_cutoff=retention_cutoff,
                    now=observed_at,
                )
                denial = self._evaluate(
                    db,
                    subject_hash=subject_hash,
                    charged_units=charged_units,
                    now=observed_at,
                )
                if denial is not None:
                    scope, reason_code, status_code, retry_after = denial
                    repository.increment_denial_bucket(
                        db,
                        scope=scope,
                        subject_hash=(
                            subject_hash
                            if scope == "user"
                            else self._opaque_hash("capacity", _GLOBAL_SUBJECT)
                        ),
                        window_started_at=observed_at.replace(second=0, microsecond=0),
                        reason_code=reason_code,
                        seen_at=observed_at,
                    )
                    db.commit()
                    raise RagAdmissionDenied(
                        reason_code=reason_code,
                        status_code=status_code,
                        retry_after_seconds=retry_after,
                    )
                admission = repository.add_admission(
                    db,
                    subject_hash=subject_hash,
                    request_hash=request_hash,
                    strategy_type=strategy_type,
                    charged_units=charged_units,
                    admitted_at=observed_at,
                    lease_expires_at=observed_at
                    + timedelta(seconds=self.settings.rag_abuse_lease_seconds),
                )
                admission_id = admission.admission_id
                db.commit()
                return RagAdmissionPermit(
                    admission_id=admission_id,
                    charged_units=charged_units,
                )
        except RagAdmissionDenied:
            raise
        except SQLAlchemyError as exc:
            db.rollback()
            raise RagAdmissionUnavailable() from exc

    def release(
        self,
        db: Session,
        *,
        admission_id: uuid.UUID | None,
        outcome: str,
        now: datetime | None = None,
    ) -> None:
        if admission_id is None:
            return
        if outcome not in _TERMINAL_OUTCOMES:
            raise ValueError("RAG admission outcome must be terminal")
        try:
            repository.release_admission(
                db,
                admission_id=admission_id,
                released_at=_as_utc(now or datetime.now(UTC)),
                outcome=outcome,
            )
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise RagAdmissionUnavailable() from exc

    def _evaluate(
        self,
        db: Session,
        *,
        subject_hash: str,
        charged_units: int,
        now: datetime,
    ) -> tuple[str, str, int, int] | None:
        minute_started_at = now - timedelta(minutes=1)
        user_request_count = repository.request_count_since(
            db, since=minute_started_at, subject_hash=subject_hash
        )
        if user_request_count >= self.settings.rag_abuse_user_requests_per_minute:
            earliest = repository.earliest_request_since(
                db, since=minute_started_at, subject_hash=subject_hash
            )
            return (
                "user",
                "rag_user_rate_limited",
                429,
                _retry_after(earliest, now, timedelta(minutes=1)),
            )
        global_request_count = repository.request_count_since(db, since=minute_started_at)
        if global_request_count >= self.settings.rag_abuse_global_requests_per_minute:
            earliest = repository.earliest_request_since(db, since=minute_started_at)
            return (
                "global",
                "rag_capacity_rate_limited",
                503,
                _retry_after(earliest, now, timedelta(minutes=1)),
            )

        day_started_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
        used_units = repository.daily_work_units(
            db,
            subject_hash=subject_hash,
            day_started_at=day_started_at,
        )
        if used_units + charged_units > self.settings.rag_abuse_user_daily_work_units:
            return (
                "user",
                "rag_user_daily_budget_exhausted",
                429,
                max(1, math.ceil((day_started_at + timedelta(days=1) - now).total_seconds())),
            )
        global_used_units = repository.daily_work_units(
            db,
            subject_hash=None,
            day_started_at=day_started_at,
        )
        if global_used_units + charged_units > self.settings.rag_abuse_global_daily_work_units:
            return (
                "global",
                "rag_capacity_daily_budget_exhausted",
                503,
                max(1, math.ceil((day_started_at + timedelta(days=1) - now).total_seconds())),
            )

        user_active = repository.active_lease_count(db, now=now, subject_hash=subject_hash)
        if user_active >= self.settings.rag_abuse_user_concurrent_requests:
            earliest = repository.earliest_active_lease_expiry(
                db, now=now, subject_hash=subject_hash
            )
            return (
                "user",
                "rag_user_concurrency_limited",
                429,
                _retry_after(earliest, now),
            )
        global_active = repository.active_lease_count(db, now=now)
        if global_active >= self.settings.rag_abuse_global_concurrent_requests:
            earliest = repository.earliest_active_lease_expiry(db, now=now)
            return (
                "global",
                "rag_capacity_concurrency_limited",
                503,
                _retry_after(earliest, now),
            )
        return None

    def _opaque_hash(self, namespace: str, value: str) -> str:
        return hmac.new(
            self.settings.session_secret.encode("utf-8"),
            f"rag-abuse-v1:{namespace}:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()


class _NullLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


def _work_units(strategy_type: str) -> int:
    return _STRATEGY_WORK_UNITS.get(strategy_type, _MAX_WORK_UNITS)


def _retry_after(
    earliest: datetime | None,
    now: datetime,
    window: timedelta | None = None,
) -> int:
    if earliest is None:
        return 1
    available_at = _as_utc(earliest) + (window or timedelta())
    return max(1, math.ceil((available_at - now).total_seconds()))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
