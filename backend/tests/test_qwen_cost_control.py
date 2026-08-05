from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import QwenCircuitBreaker, QwenCostReservation
from app.services.qwen_cost_control_service import (
    QwenCostControlDenied,
    QwenCostControlService,
)


def _factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "qwen_user_daily_input_tokens": 1_000,
        "qwen_user_daily_output_tokens": 1_000,
        "qwen_user_daily_cost_usd": Decimal("10"),
        "qwen_provider_daily_input_tokens": 2_000,
        "qwen_provider_daily_output_tokens": 2_000,
        "qwen_provider_daily_cost_usd": Decimal("20"),
        "qwen_user_daily_escalations": 2,
        "qwen_provider_daily_escalations": 4,
        "qwen_provider_requests_per_minute": 30,
        "qwen_provider_concurrent_requests": 10,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _reserve(
    service: QwenCostControlService,
    factory: sessionmaker[Session],
    *,
    request_id: str,
    user_id: int = 7,
    tier: str = "flash",
    call_index: int = 1,
    input_tokens: int = 100,
    output_tokens: int = 100,
    now: datetime | None = None,
):
    with factory() as db:
        return service.reserve(
            db,
            user_id=user_id,
            request_id=request_id,
            tier=tier,  # type: ignore[arg-type]
            call_index=call_index,
            estimated_input_tokens=input_tokens,
            reserved_output_tokens=output_tokens,
            now=now,
        )


def test_reserve_and_finalize_store_only_hashes_and_pricing_snapshot(tmp_path) -> None:
    factory = _factory(f"sqlite:///{tmp_path / 'ledger.db'}")
    settings = _settings()
    service = QwenCostControlService(settings)
    synthetic_request_id = "".join(("request", "-", "opaque"))
    permit = _reserve(service, factory, request_id=synthetic_request_id)

    with factory() as db:
        actual_cost = service.finalize(
            db,
            reservation_id=permit.reservation_id,
            input_tokens=80,
            output_tokens=20,
        )

    with factory() as db:
        row = db.scalar(select(QwenCostReservation))
        assert row is not None
        assert row.status == "finalized"
        assert row.pricing_version == settings.qwen_pricing_version
        assert row.currency == "USD"
        assert row.actual_input_tokens == 80
        assert row.actual_output_tokens == 20
        assert row.actual_cost == actual_cost
        assert len(row.subject_hash) == 64
        assert len(row.request_hash) == 64
        assert synthetic_request_id not in row.request_hash


def test_ledger_schema_has_no_content_or_sensitive_value_columns() -> None:
    column_names = set(QwenCostReservation.__table__.columns.keys())
    forbidden_fragments = {
        "answer",
        "canary",
        "chunk",
        "context",
        "credential",
        "output_text",
        "pii",
        "prompt",
        "question",
        "raw",
        "secret",
        "token_value",
    }

    assert all(
        fragment not in column_name
        for column_name in column_names
        for fragment in forbidden_fragments
    )


def test_atomic_daily_budget_allows_only_one_competing_reservation(tmp_path) -> None:
    factory = _factory(f"sqlite:///{tmp_path / 'race.db'}")
    settings = _settings(
        qwen_user_daily_input_tokens=100,
        qwen_provider_daily_input_tokens=100,
    )
    service = QwenCostControlService(settings)

    def attempt(index: int) -> str:
        try:
            _reserve(
                service,
                factory,
                request_id=f"race-{index}",
                input_tokens=80,
                output_tokens=1,
            )
        except QwenCostControlDenied as exc:
            return exc.reason_code
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (1, 2)))

    assert outcomes.count("reserved") == 1
    assert outcomes.count("qwen_user_input_budget_exhausted") == 1


def test_postgres_atomic_budget_race_uses_shared_transaction_lock() -> None:
    database_url = get_settings().database_url
    engine = create_engine(database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.skip("PostgreSQL atomic reservation requires a PostgreSQL DATABASE_URL")
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    service = QwenCostControlService(
        _settings(
            qwen_user_daily_input_tokens=100,
            qwen_provider_daily_input_tokens=10_000,
        )
    )
    observed_at = datetime(2099, 8, 5, 4, 0, tzinfo=UTC)
    permits = []

    def attempt(index: int) -> str:
        try:
            permit = _reserve(
                service,
                factory,
                request_id=f"postgres-race-{index}",
                user_id=99_000_007,
                input_tokens=80,
                output_tokens=1,
                now=observed_at,
            )
        except QwenCostControlDenied as exc:
            return exc.reason_code
        permits.append(permit)
        return "reserved"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, (1, 2)))
        assert outcomes.count("reserved") == 1
        assert outcomes.count("qwen_user_input_budget_exhausted") == 1
    finally:
        for permit in permits:
            with factory() as db:
                service.cancel_before_transport(
                    db,
                    reservation_id=permit.reservation_id,
                    reason_code="qwen_test_cleanup",
                    now=observed_at,
                )
        engine.dispose()


def test_one_escalation_per_request_is_atomic(tmp_path) -> None:
    factory = _factory(f"sqlite:///{tmp_path / 'escalation.db'}")
    service = QwenCostControlService(
        _settings(rag_injection_policy="block_user_quarantine_context")
    )
    _reserve(service, factory, request_id="same-request", tier="plus", call_index=2)

    with pytest.raises(QwenCostControlDenied) as exc_info:
        _reserve(service, factory, request_id="same-request", tier="plus", call_index=4)

    assert exc_info.value.reason_code == "qwen_request_escalation_limit"


def test_shared_rate_and_concurrency_limits_publish_stable_retries(tmp_path) -> None:
    rate_factory = _factory(f"sqlite:///{tmp_path / 'rate.db'}")
    rate_service = QwenCostControlService(_settings(qwen_provider_requests_per_minute=1))
    _reserve(rate_service, rate_factory, request_id="rate-1")
    with pytest.raises(QwenCostControlDenied) as rate_denial:
        _reserve(rate_service, rate_factory, request_id="rate-2")
    assert rate_denial.value.reason_code == "qwen_provider_rate_limited"
    assert rate_denial.value.retry_after_seconds == 60

    concurrency_factory = _factory(f"sqlite:///{tmp_path / 'concurrency.db'}")
    concurrency_service = QwenCostControlService(_settings(qwen_provider_concurrent_requests=1))
    _reserve(concurrency_service, concurrency_factory, request_id="concurrency-1")
    with pytest.raises(QwenCostControlDenied) as concurrency_denial:
        _reserve(concurrency_service, concurrency_factory, request_id="concurrency-2")
    assert concurrency_denial.value.reason_code == "qwen_provider_concurrency_limited"
    assert concurrency_denial.value.retry_after_seconds > 0


def test_provider_failures_open_shared_circuit_and_publish_retry_after(tmp_path) -> None:
    factory = _factory(f"sqlite:///{tmp_path / 'circuit.db'}")
    now = datetime(2026, 8, 5, 4, 0, tzinfo=UTC)
    service = QwenCostControlService(
        _settings(
            qwen_circuit_failure_threshold=2,
            qwen_circuit_cooldown_seconds=30,
            rag_injection_policy="block_user_quarantine_context",
        )
    )
    for index in (1, 2):
        permit = _reserve(
            service,
            factory,
            request_id=f"failure-{index}",
            call_index=index,
            now=now,
        )
        with factory() as db:
            service.fail_after_transport(
                db,
                reservation_id=permit.reservation_id,
                reason_code="qwen_provider_unavailable",
                provider_failure=True,
                now=now,
            )

    with pytest.raises(QwenCostControlDenied) as exc_info:
        _reserve(service, factory, request_id="blocked", call_index=3, now=now)

    assert exc_info.value.reason_code == "qwen_circuit_open"
    assert exc_info.value.retry_after_seconds == 30
    with factory() as db:
        circuit = db.get(QwenCircuitBreaker, "qwen")
        assert circuit is not None
        assert circuit.state == "open"
        assert circuit.opened_until is not None
        assert circuit.opened_until.replace(tzinfo=UTC) == now + timedelta(seconds=30)


def test_cancel_before_transport_releases_budget_without_counting_failure(tmp_path) -> None:
    factory = _factory(f"sqlite:///{tmp_path / 'cancel.db'}")
    service = QwenCostControlService(_settings(qwen_user_daily_input_tokens=100))
    permit = _reserve(
        service,
        factory,
        request_id="cancelled",
        input_tokens=100,
        output_tokens=1,
    )
    with factory() as db:
        service.cancel_before_transport(
            db,
            reservation_id=permit.reservation_id,
            reason_code="qwen_egress_blocked",
        )

    replacement = _reserve(
        service,
        factory,
        request_id=str(uuid.uuid4()),
        input_tokens=100,
        output_tokens=1,
    )
    assert replacement.reservation_id != permit.reservation_id
