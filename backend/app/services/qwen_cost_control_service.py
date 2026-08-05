from __future__ import annotations

import hashlib
import hmac
import math
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import QwenCircuitBreaker, QwenCostReservation
from app.repositories import qwen_cost_control_repository as repository

_PROCESS_COST_LOCK = threading.RLock()
_MILLION = Decimal(1_000_000)
_COST_QUANTUM = Decimal("0.000000001")


@dataclass(frozen=True)
class QwenReservationPermit:
    reservation_id: uuid.UUID
    reserved_cost: Decimal


class QwenCostControlDenied(Exception):
    def __init__(self, *, reason_code: str, status_code: int, retry_after_seconds: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class QwenCostControlUnavailable(Exception):
    reason_code = "qwen_cost_control_unavailable"
    status_code = 503
    retry_after_seconds = 5


class QwenCostControlService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def reserve(
        self,
        db: Session,
        *,
        user_id: int,
        request_id: str,
        tier: Literal["flash", "plus"],
        call_index: int,
        estimated_input_tokens: int,
        reserved_output_tokens: int,
        now: datetime | None = None,
    ) -> QwenReservationPermit:
        observed_at = _as_utc(now or datetime.now(UTC))
        subject_hash = self._opaque_hash("subject", str(user_id))
        request_hash = self._opaque_hash("request", request_id)
        input_tokens = max(0, estimated_input_tokens)
        output_tokens = max(1, reserved_output_tokens)
        input_price, output_price = self._prices(tier)
        reserved_cost = _calculate_cost(input_tokens, output_tokens, input_price, output_price)
        try:
            with _cost_lock(db):
                repository.acquire_cost_control_lock(db)
                repository.expire_stale_reservations(db, now=observed_at)
                repository.prune_terminal_reservations(
                    db,
                    cutoff=observed_at - timedelta(days=self.settings.qwen_ledger_retention_days),
                )
                self._admit_circuit(db, now=observed_at)
                self._evaluate_capacity(db, now=observed_at)
                self._evaluate_budgets(
                    db,
                    subject_hash=subject_hash,
                    request_hash=request_hash,
                    tier=tier,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost=reserved_cost,
                    now=observed_at,
                )
                reservation = repository.add_reservation(
                    db,
                    QwenCostReservation(
                        subject_hash=subject_hash,
                        request_hash=request_hash,
                        provider="qwen",
                        model_id=(
                            self.settings.qwen_plus_model_id
                            if tier == "plus"
                            else self.settings.qwen_flash_model_id
                        ),
                        tier=tier,
                        call_index=call_index,
                        pricing_version=self.settings.qwen_pricing_version,
                        currency=self.settings.qwen_currency,
                        input_price_per_million=input_price,
                        output_price_per_million=output_price,
                        reserved_input_tokens=input_tokens,
                        reserved_output_tokens=output_tokens,
                        reserved_cost=reserved_cost,
                        is_escalation=tier == "plus",
                        status="reserved",
                        reason_code="qwen_budget_reserved",
                        reserved_at=observed_at,
                        lease_expires_at=observed_at
                        + timedelta(seconds=self.settings.qwen_reservation_lease_seconds),
                    ),
                )
                reservation_id = reservation.reservation_id
                db.commit()
                return QwenReservationPermit(
                    reservation_id=reservation_id,
                    reserved_cost=reserved_cost,
                )
        except QwenCostControlDenied:
            db.rollback()
            raise
        except SQLAlchemyError as exc:
            db.rollback()
            raise QwenCostControlUnavailable() from exc

    def finalize(
        self,
        db: Session,
        *,
        reservation_id: uuid.UUID,
        input_tokens: int,
        output_tokens: int,
        now: datetime | None = None,
    ) -> Decimal:
        observed_at = _as_utc(now or datetime.now(UTC))
        try:
            with _cost_lock(db):
                repository.acquire_cost_control_lock(db)
                reservation = repository.get_reservation(db, reservation_id)
                if reservation is None or reservation.status != "reserved":
                    raise QwenCostControlUnavailable()
                actual_input = max(0, input_tokens)
                actual_output = max(0, output_tokens)
                actual_cost = _calculate_cost(
                    actual_input,
                    actual_output,
                    reservation.input_price_per_million,
                    reservation.output_price_per_million,
                )
                # Provider usage may exceed the conservative estimate. Keep the
                # exact actual charge; the next atomic admission sees it.
                reservation.actual_input_tokens = actual_input
                reservation.actual_output_tokens = actual_output
                reservation.actual_cost = actual_cost
                reservation.status = "finalized"
                reservation.reason_code = "qwen_usage_finalized"
                reservation.finalized_at = observed_at
                self._close_circuit(repository.get_circuit(db), now=observed_at)
                db.commit()
                return actual_cost
        except QwenCostControlUnavailable:
            db.rollback()
            raise
        except SQLAlchemyError as exc:
            db.rollback()
            raise QwenCostControlUnavailable() from exc

    def cancel_before_transport(
        self,
        db: Session,
        *,
        reservation_id: uuid.UUID,
        reason_code: str,
        now: datetime | None = None,
    ) -> None:
        self._terminalize(
            db,
            reservation_id=reservation_id,
            status="cancelled",
            reason_code=reason_code,
            provider_failure=False,
            now=now,
        )

    def fail_after_transport(
        self,
        db: Session,
        *,
        reservation_id: uuid.UUID,
        reason_code: str,
        provider_failure: bool,
        now: datetime | None = None,
    ) -> None:
        self._terminalize(
            db,
            reservation_id=reservation_id,
            status="failed",
            reason_code=reason_code,
            provider_failure=provider_failure,
            now=now,
        )

    def _terminalize(
        self,
        db: Session,
        *,
        reservation_id: uuid.UUID,
        status: Literal["failed", "cancelled"],
        reason_code: str,
        provider_failure: bool,
        now: datetime | None,
    ) -> None:
        observed_at = _as_utc(now or datetime.now(UTC))
        try:
            with _cost_lock(db):
                repository.acquire_cost_control_lock(db)
                reservation = repository.get_reservation(db, reservation_id)
                if reservation is None or reservation.status != "reserved":
                    db.rollback()
                    return
                reservation.status = status
                reservation.reason_code = _safe_reason(reason_code)
                reservation.finalized_at = observed_at
                if provider_failure:
                    self._record_circuit_failure(repository.get_circuit(db), now=observed_at)
                db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            raise QwenCostControlUnavailable() from exc

    def _evaluate_capacity(self, db: Session, *, now: datetime) -> None:
        minute_started_at = now - timedelta(minutes=1)
        if (
            repository.request_count_since(db, since=minute_started_at)
            >= self.settings.qwen_provider_requests_per_minute
        ):
            self._deny("qwen_provider_rate_limited", 429, 60)
        active = repository.active_reservations(db, now=now)
        if len(active) >= self.settings.qwen_provider_concurrent_requests:
            retry_after = min(
                max(1, math.ceil((_as_utc(item.lease_expires_at) - now).total_seconds()))
                for item in active
            )
            self._deny("qwen_provider_concurrency_limited", 503, retry_after)

    def _evaluate_budgets(
        self,
        db: Session,
        *,
        subject_hash: str,
        request_hash: str,
        tier: Literal["flash", "plus"],
        input_tokens: int,
        output_tokens: int,
        cost: Decimal,
        now: datetime,
    ) -> None:
        day_started_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
        retry_after = max(1, math.ceil((day_started_at + timedelta(days=1) - now).total_seconds()))
        daily = repository.list_daily_reservations(db, day_started_at=day_started_at)
        user_daily = [item for item in daily if item.subject_hash == subject_hash]
        self._check_totals(
            user_daily,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            input_limit=self.settings.qwen_user_daily_input_tokens,
            output_limit=self.settings.qwen_user_daily_output_tokens,
            cost_limit=self.settings.qwen_user_daily_cost_usd,
            prefix="qwen_user",
            retry_after=retry_after,
        )
        self._check_totals(
            daily,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            input_limit=self.settings.qwen_provider_daily_input_tokens,
            output_limit=self.settings.qwen_provider_daily_output_tokens,
            cost_limit=self.settings.qwen_provider_daily_cost_usd,
            prefix="qwen_provider",
            retry_after=retry_after,
        )
        if tier == "plus":
            if repository.request_escalation_count(db, request_hash=request_hash) >= 1:
                self._deny("qwen_request_escalation_limit", 409, 1)
            user_escalations = sum(item.is_escalation for item in user_daily)
            provider_escalations = sum(item.is_escalation for item in daily)
            if user_escalations >= self.settings.qwen_user_daily_escalations:
                self._deny("qwen_user_escalation_budget_exhausted", 429, retry_after)
            if provider_escalations >= self.settings.qwen_provider_daily_escalations:
                self._deny("qwen_provider_escalation_budget_exhausted", 503, retry_after)

    def _check_totals(
        self,
        reservations: list[QwenCostReservation],
        *,
        input_tokens: int,
        output_tokens: int,
        cost: Decimal,
        input_limit: int,
        output_limit: int,
        cost_limit: Decimal,
        prefix: str,
        retry_after: int,
    ) -> None:
        used_input = sum(_charged_input(item) for item in reservations)
        used_output = sum(_charged_output(item) for item in reservations)
        used_cost = sum((_charged_cost(item) for item in reservations), start=Decimal(0))
        status_code = 429 if prefix == "qwen_user" else 503
        if used_input + input_tokens > input_limit:
            self._deny(f"{prefix}_input_budget_exhausted", status_code, retry_after)
        if used_output + output_tokens > output_limit:
            self._deny(f"{prefix}_output_budget_exhausted", status_code, retry_after)
        if used_cost + cost > cost_limit:
            self._deny(f"{prefix}_cost_budget_exhausted", status_code, retry_after)

    def _admit_circuit(self, db: Session, *, now: datetime) -> None:
        circuit = repository.get_circuit(db)
        if circuit.state == "closed":
            return
        if circuit.state == "open" and circuit.opened_until is not None:
            opened_until = _as_utc(circuit.opened_until)
            if opened_until > now:
                self._deny(
                    "qwen_circuit_open",
                    503,
                    max(1, math.ceil((opened_until - now).total_seconds())),
                )
        probe_expiry = (
            _as_utc(circuit.probe_lease_expires_at)
            if circuit.probe_lease_expires_at is not None
            else None
        )
        if circuit.state == "half_open" and probe_expiry is not None and probe_expiry > now:
            self._deny(
                "qwen_circuit_half_open",
                503,
                max(1, math.ceil((probe_expiry - now).total_seconds())),
            )
        circuit.state = "half_open"
        circuit.probe_lease_expires_at = now + timedelta(
            seconds=self.settings.qwen_reservation_lease_seconds
        )
        circuit.updated_at = now

    def _record_circuit_failure(self, circuit: QwenCircuitBreaker, *, now: datetime) -> None:
        circuit.consecutive_failures += 1
        circuit.last_reason_code = "qwen_provider_failure"
        circuit.updated_at = now
        circuit.probe_lease_expires_at = None
        if (
            circuit.state == "half_open"
            or circuit.consecutive_failures >= self.settings.qwen_circuit_failure_threshold
        ):
            circuit.state = "open"
            circuit.opened_until = now + timedelta(
                seconds=self.settings.qwen_circuit_cooldown_seconds
            )

    @staticmethod
    def _close_circuit(circuit: QwenCircuitBreaker, *, now: datetime) -> None:
        circuit.state = "closed"
        circuit.consecutive_failures = 0
        circuit.opened_until = None
        circuit.probe_lease_expires_at = None
        circuit.last_reason_code = None
        circuit.updated_at = now

    def _prices(self, tier: Literal["flash", "plus"]) -> tuple[Decimal, Decimal]:
        if tier == "plus":
            return (
                self.settings.qwen_plus_input_usd_per_million,
                self.settings.qwen_plus_output_usd_per_million,
            )
        return (
            self.settings.qwen_flash_input_usd_per_million,
            self.settings.qwen_flash_output_usd_per_million,
        )

    def _opaque_hash(self, namespace: str, value: str) -> str:
        return hmac.new(
            self.settings.session_secret.encode("utf-8"),
            f"qwen-cost-v1:{namespace}:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _deny(reason_code: str, status_code: int, retry_after: int) -> None:
        raise QwenCostControlDenied(
            reason_code=reason_code,
            status_code=status_code,
            retry_after_seconds=max(1, retry_after),
        )


@contextmanager
def _cost_lock(db: Session) -> Iterator[None]:
    if db.get_bind().dialect.name == "postgresql":
        yield
        return
    with _PROCESS_COST_LOCK:
        yield


def _charged_input(reservation: QwenCostReservation) -> int:
    return (
        reservation.actual_input_tokens
        if reservation.status == "finalized" and reservation.actual_input_tokens is not None
        else reservation.reserved_input_tokens
    )


def _charged_output(reservation: QwenCostReservation) -> int:
    return (
        reservation.actual_output_tokens
        if reservation.status == "finalized" and reservation.actual_output_tokens is not None
        else reservation.reserved_output_tokens
    )


def _charged_cost(reservation: QwenCostReservation) -> Decimal:
    return (
        reservation.actual_cost
        if reservation.status == "finalized" and reservation.actual_cost is not None
        else reservation.reserved_cost
    )


def _calculate_cost(
    input_tokens: int,
    output_tokens: int,
    input_price: Decimal,
    output_price: Decimal,
) -> Decimal:
    return (
        (Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price) / _MILLION
    ).quantize(_COST_QUANTUM, rounding=ROUND_UP)


def _safe_reason(value: str) -> str:
    normalized = "".join(
        character for character in value.lower() if character.isalnum() or character == "_"
    )
    return normalized[:100] or "qwen_provider_failure"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
