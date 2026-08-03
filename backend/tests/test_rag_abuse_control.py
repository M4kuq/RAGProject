from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import RagAbuseDenialBucket, RagRequestAdmission
from app.repositories import rag_abuse_control_repository as repository
from app.services.rag_abuse_control_service import (
    RagAbuseControlService,
    RagAdmissionDenied,
    RagAdmissionUnavailable,
)

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def admission_db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        yield db
    engine.dispose()


def test_admission_hashes_identifiers_and_releases_lease(admission_db: Session) -> None:
    service = RagAbuseControlService(_settings())

    permit = service.admit(
        admission_db,
        user_id=42,
        request_id="raw-request-id",
        strategy_type="dense",
        now=NOW,
    )

    assert permit.admission_id is not None
    assert permit.charged_units == 1
    admission = admission_db.get(RagRequestAdmission, permit.admission_id)
    assert admission is not None
    assert len(admission.subject_hash) == 64
    assert len(admission.request_hash) == 64
    assert admission.subject_hash != "42"
    assert admission.request_hash != "raw-request-id"
    assert admission.outcome == "running"
    service.release(
        admission_db,
        admission_id=permit.admission_id,
        outcome="succeeded",
        now=NOW + timedelta(seconds=2),
    )
    admission_db.refresh(admission)
    assert admission.outcome == "succeeded"
    assert admission.released_at is not None


def test_user_rate_limit_returns_retry_after_and_aggregates_denials(
    admission_db: Session,
) -> None:
    service = RagAbuseControlService(
        _settings(
            rag_abuse_user_requests_per_minute=1,
            rag_abuse_global_requests_per_minute=10,
        )
    )
    service.admit(
        admission_db,
        user_id=1,
        request_id="request-1",
        strategy_type="dense",
        now=NOW,
    )

    for offset in (1, 2):
        with pytest.raises(RagAdmissionDenied) as captured:
            service.admit(
                admission_db,
                user_id=1,
                request_id=f"request-{offset + 1}",
                strategy_type="dense",
                now=NOW + timedelta(seconds=offset),
            )
        assert captured.value.reason_code == "rag_user_rate_limited"
        assert captured.value.status_code == 429
        assert captured.value.retry_after_seconds == 60 - offset

    bucket = admission_db.scalar(select(RagAbuseDenialBucket))
    assert bucket is not None
    assert bucket.scope == "user"
    assert bucket.denied_count == 2
    assert len(bucket.subject_hash) == 64


def test_global_concurrency_is_shared_and_expired_lease_recovers(
    admission_db: Session,
) -> None:
    service = RagAbuseControlService(
        _settings(
            rag_abuse_user_concurrent_requests=1,
            rag_abuse_global_concurrent_requests=1,
            rag_abuse_user_requests_per_minute=100,
            rag_abuse_global_requests_per_minute=100,
        )
    )
    first = service.admit(
        admission_db,
        user_id=1,
        request_id="request-1",
        strategy_type="dense",
        now=NOW,
    )

    with pytest.raises(RagAdmissionDenied) as captured:
        service.admit(
            admission_db,
            user_id=2,
            request_id="request-2",
            strategy_type="dense",
            now=NOW + timedelta(seconds=1),
        )
    assert captured.value.reason_code == "rag_capacity_concurrency_limited"
    assert captured.value.status_code == 503
    assert captured.value.retry_after_seconds == 899

    recovered = service.admit(
        admission_db,
        user_id=2,
        request_id="request-3",
        strategy_type="dense",
        now=NOW + timedelta(seconds=901),
    )
    assert recovered.admission_id is not None
    stale = admission_db.get(RagRequestAdmission, first.admission_id)
    assert stale is not None
    assert stale.outcome == "running"


def test_daily_work_budget_uses_server_owned_strategy_weights(admission_db: Session) -> None:
    service = RagAbuseControlService(
        _settings(
            rag_abuse_user_daily_work_units=2,
            rag_abuse_user_requests_per_minute=100,
            rag_abuse_global_requests_per_minute=100,
        )
    )
    hybrid = service.admit(
        admission_db,
        user_id=1,
        request_id="request-1",
        strategy_type="hybrid",
        now=NOW,
    )
    assert hybrid.charged_units == 2
    service.release(
        admission_db,
        admission_id=hybrid.admission_id,
        outcome="failed",
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(RagAdmissionDenied) as captured:
        service.admit(
            admission_db,
            user_id=1,
            request_id="request-2",
            strategy_type="dense",
            now=NOW + timedelta(seconds=2),
        )
    assert captured.value.reason_code == "rag_user_daily_budget_exhausted"
    assert captured.value.status_code == 429
    assert captured.value.retry_after_seconds == 43_198


def test_unknown_strategy_is_charged_maximum_work_units(admission_db: Session) -> None:
    service = RagAbuseControlService(
        _settings(
            rag_abuse_user_daily_work_units=7,
            rag_abuse_user_requests_per_minute=100,
            rag_abuse_global_requests_per_minute=100,
        )
    )

    with pytest.raises(RagAdmissionDenied) as captured:
        service.admit(
            admission_db,
            user_id=1,
            request_id="request-1",
            strategy_type="untrusted-future-strategy",
            now=NOW,
        )
    assert captured.value.reason_code == "rag_user_daily_budget_exhausted"


def test_global_daily_work_budget_limits_many_accounts(admission_db: Session) -> None:
    service = RagAbuseControlService(
        _settings(
            rag_abuse_user_daily_work_units=2,
            rag_abuse_global_daily_work_units=2,
            rag_abuse_user_requests_per_minute=100,
            rag_abuse_global_requests_per_minute=100,
        )
    )
    first = service.admit(
        admission_db,
        user_id=1,
        request_id="request-1",
        strategy_type="hybrid",
        now=NOW,
    )
    service.release(
        admission_db,
        admission_id=first.admission_id,
        outcome="succeeded",
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(RagAdmissionDenied) as captured:
        service.admit(
            admission_db,
            user_id=2,
            request_id="request-2",
            strategy_type="dense",
            now=NOW + timedelta(seconds=2),
        )
    assert captured.value.reason_code == "rag_capacity_daily_budget_exhausted"
    assert captured.value.status_code == 503


def test_disabled_control_is_database_free(admission_db: Session) -> None:
    service = RagAbuseControlService(_settings(rag_abuse_control_enabled=False))

    permit = service.admit(
        admission_db,
        user_id=1,
        request_id="request-1",
        strategy_type="langgraph_agentic",
        now=NOW,
    )

    assert permit.admission_id is None
    assert permit.charged_units == 0
    assert admission_db.scalar(select(RagRequestAdmission)) is None


def test_database_failure_fails_closed(
    admission_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RagAbuseControlService(_settings())

    def fail_lock(_: Session) -> None:
        raise OperationalError("SELECT", {}, Exception("database unavailable"))

    monkeypatch.setattr(repository, "acquire_admission_lock", fail_lock)

    with pytest.raises(RagAdmissionUnavailable) as captured:
        service.admit(
            admission_db,
            user_id=1,
            request_id="request-1",
            strategy_type="dense",
            now=NOW,
        )
    assert captured.value.reason_code == "rag_admission_unavailable"
    assert captured.value.status_code == 503


def test_abuse_control_settings_require_safe_shared_production_store() -> None:
    with pytest.raises(ValueError, match="requires PostgreSQL"):
        _settings(app_env="production", database_url="sqlite:///unsafe.db")
    with pytest.raises(ValueError, match="must cover every Agentic"):
        _settings(rag_abuse_lease_seconds=599)

    disabled = _settings(
        app_env="production",
        database_url="sqlite:///rollback.db",
        rag_abuse_control_enabled=False,
    )
    assert disabled.rag_abuse_control_enabled is False


def test_postgres_admission_is_atomic_across_sessions() -> None:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.skip("shared admission atomicity requires PostgreSQL")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    service = RagAbuseControlService(
        _settings(
            rag_abuse_user_requests_per_minute=1,
            rag_abuse_global_requests_per_minute=1,
        )
    )
    future = datetime(2099, 1, 1, tzinfo=UTC)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    failures: list[BaseException] = []

    def admit(user_id: int) -> None:
        try:
            barrier.wait(timeout=5)
            with session_factory() as db:
                service.admit(
                    db,
                    user_id=user_id,
                    request_id=f"atomic-{user_id}",
                    strategy_type="dense",
                    now=future,
                )
            outcomes.append("admitted")
        except RagAdmissionDenied as exc:
            outcomes.append(exc.reason_code)
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [threading.Thread(target=admit, args=(user_id,)) for user_id in (9001, 9002)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not failures
        assert all(not thread.is_alive() for thread in threads)
        assert sorted(outcomes) == ["admitted", "rag_capacity_rate_limited"]
    finally:
        with session_factory() as db:
            db.execute(delete(RagRequestAdmission).where(RagRequestAdmission.admitted_at == future))
            db.execute(
                delete(RagAbuseDenialBucket).where(RagAbuseDenialBucket.window_started_at == future)
            )
            db.commit()
        engine.dispose()


def test_postgres_advisory_lock_wait_is_bounded() -> None:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.skip("bounded advisory lock wait requires PostgreSQL")
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    service = RagAbuseControlService(_settings())

    try:
        with session_factory() as lock_holder, session_factory() as contender:
            contender.execute(text("SELECT 1"))
            contender.rollback()
            repository.acquire_admission_lock(lock_holder)
            started_at = time.monotonic()
            with pytest.raises(RagAdmissionUnavailable):
                service.admit(
                    contender,
                    user_id=9003,
                    request_id="bounded-lock",
                    strategy_type="dense",
                    now=datetime(2099, 1, 2, tzinfo=UTC),
                )
            elapsed = time.monotonic() - started_at
            assert elapsed < 3.0
            lock_holder.rollback()
    finally:
        engine.dispose()


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "sqlite://",
        "session_secret": "x" * 32,
        "generation_provider": "fake",
        "rag_abuse_control_enabled": True,
        "rag_abuse_user_requests_per_minute": 20,
        "rag_abuse_global_requests_per_minute": 200,
        "rag_abuse_user_concurrent_requests": 2,
        "rag_abuse_global_concurrent_requests": 20,
        "rag_abuse_user_daily_work_units": 500,
        "rag_abuse_global_daily_work_units": 10_000,
        "rag_abuse_lease_seconds": 900,
        "rag_abuse_audit_retention_days": 8,
    }
    values.update(overrides)
    if values["app_env"] == "production":
        values["session_cookie_secure"] = True
        values["generation_provider"] = "ollama"
    return Settings(**values)
