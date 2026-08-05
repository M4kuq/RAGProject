from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db.models import QwenCircuitBreaker, QwenCostReservation

_ADVISORY_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"qwen-cost-control-v1").digest()[:8],
    "big",
    signed=True,
)


def acquire_cost_control_lock(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '1000ms'"))
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _ADVISORY_LOCK_KEY},
        )


def prune_terminal_reservations(db: Session, *, cutoff: datetime) -> None:
    db.execute(
        delete(QwenCostReservation).where(
            QwenCostReservation.reserved_at < cutoff,
            QwenCostReservation.status.in_(("finalized", "failed", "cancelled")),
        )
    )


def expire_stale_reservations(db: Session, *, now: datetime) -> None:
    for reservation in db.scalars(
        select(QwenCostReservation).where(
            QwenCostReservation.status == "reserved",
            QwenCostReservation.lease_expires_at <= now,
        )
    ):
        reservation.status = "failed"
        reservation.reason_code = "qwen_reservation_expired"
        reservation.finalized_at = now
    db.flush()


def list_daily_reservations(db: Session, *, day_started_at: datetime) -> list[QwenCostReservation]:
    return list(
        db.scalars(
            select(QwenCostReservation).where(
                QwenCostReservation.reserved_at >= day_started_at,
                QwenCostReservation.status != "cancelled",
            )
        )
    )


def request_escalation_count(db: Session, *, request_hash: str) -> int:
    return len(
        list(
            db.scalars(
                select(QwenCostReservation.reservation_id).where(
                    QwenCostReservation.request_hash == request_hash,
                    QwenCostReservation.is_escalation.is_(True),
                    QwenCostReservation.status != "cancelled",
                )
            )
        )
    )


def request_count_since(db: Session, *, since: datetime) -> int:
    return len(
        list(
            db.scalars(
                select(QwenCostReservation.reservation_id).where(
                    QwenCostReservation.reserved_at >= since,
                    QwenCostReservation.status != "cancelled",
                )
            )
        )
    )


def active_reservations(db: Session, *, now: datetime) -> list[QwenCostReservation]:
    return list(
        db.scalars(
            select(QwenCostReservation).where(
                QwenCostReservation.status == "reserved",
                QwenCostReservation.lease_expires_at > now,
            )
        )
    )


def add_reservation(db: Session, reservation: QwenCostReservation) -> QwenCostReservation:
    db.add(reservation)
    db.flush()
    return reservation


def get_reservation(db: Session, reservation_id: uuid.UUID) -> QwenCostReservation | None:
    return db.get(QwenCostReservation, reservation_id)


def get_circuit(db: Session) -> QwenCircuitBreaker:
    circuit = db.get(QwenCircuitBreaker, "qwen")
    if circuit is None:
        circuit = QwenCircuitBreaker(provider="qwen")
        db.add(circuit)
        db.flush()
    return circuit
