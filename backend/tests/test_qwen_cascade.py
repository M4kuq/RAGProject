from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.model_egress import ModelEgressGuard, model_egress_request_scope
from app.db.base import Base
from app.db.models import QwenCostReservation
from app.rag.generation import AnswerGenerationError, GenerationContextItem, GenerationRequest
from app.rag.qwen_cascade import QwenCascadeAnswerGenerator, QwenCascadeControlError


def _egress_rule(model_id: str) -> dict[str, object]:
    return {
        "provider": "qwen",
        "model": model_id,
        "purpose": "generation",
        "allowed_data_classes": [
            "masked_personal_data",
            "retrieved_context",
            "system_instruction",
            "user_question",
        ],
        "allowed_regions": ["japan-tokyo"],
        "retention_days": 0,
        "training_allowed": False,
        "user_consent_required": True,
    }


def _settings(**overrides: object) -> Settings:
    flash = "qwen3.6-flash-2026-04-16"
    plus = "qwen3.7-plus-2026-05-26"
    values: dict[str, object] = {
        "_env_file": None,
        "app_env": "test",
        "generation_provider": "qwen",
        "qwen_cascade_enabled": True,
        "qwen_api_key": "".join(("synthetic", "-", "credential")),
        "qwen_base_url": ("https://workspace.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1"),
        "qwen_max_output_tokens": 128,
        "external_model_egress_policy": "mask",
        "external_model_egress_allowed_providers": ["qwen"],
        "external_model_egress_provider_regions": {"qwen": "japan-tokyo"},
        "external_model_egress_max_retention_days": 0,
        "external_model_egress_rules": [_egress_rule(flash), _egress_rule(plus)],
        "qwen_user_daily_input_tokens": 100_000,
        "qwen_user_daily_output_tokens": 10_000,
        "qwen_user_daily_cost_usd": Decimal("10"),
        "qwen_provider_daily_input_tokens": 200_000,
        "qwen_provider_daily_output_tokens": 20_000,
        "qwen_provider_daily_cost_usd": Decimal("20"),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _request(
    *,
    strategy: str = "dense",
    sufficient: bool = True,
    attempt: int = 1,
    message: str | None = None,
    context_text: str | None = None,
    system_instructions: str | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        message=message or "".join(("synthetic", "-", "query")),
        context_items=[
            GenerationContextItem(
                document_chunk_id=1,
                source_label="synthetic-source",
                text=context_text or "".join(("synthetic", "-", "evidence")),
                local_citation_id=1,
            )
        ],
        max_output_chars=1_000,
        system_instructions=system_instructions,
        trusted_user_id=9,
        trusted_request_id="request-1",
        trusted_strategy=strategy,
        trusted_retrieval_sufficient=sufficient,
        trusted_generation_attempt=attempt,
    )


class _Response:
    def __init__(
        self,
        *,
        model: str,
        status_code: int = 200,
        finish_reason: str = "stop",
        retry_after: str | None = None,
    ) -> None:
        self.model = model
        self.status_code = status_code
        self.headers = {"Retry-After": retry_after} if retry_after else {}
        self.finish_reason = finish_reason

    def json(self) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {"content": " ".join(("Supported", "[1]."))},
                    "finish_reason": self.finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100},
            "model": self.model,
        }


def _generator(settings: Settings, factory: sessionmaker[Session]) -> QwenCascadeAnswerGenerator:
    return QwenCascadeAnswerGenerator(
        settings=settings,
        session_factory=factory,
        egress_guard=ModelEgressGuard.from_settings(settings),
    )


def test_dense_clean_request_uses_flash_with_server_owned_transport_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    factory = _factory()
    calls: list[dict[str, object]] = []

    def fake_post(*args: object, **kwargs: Any) -> _Response:
        payload = kwargs["json"]
        calls.append(
            {
                "model": payload["model"],
                "temperature": payload["temperature"],
                "enable_thinking": payload["enable_thinking"],
                "max_tokens": payload["max_tokens"],
            }
        )
        return _Response(model=payload["model"])

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", fake_post)
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=True):
        result = _generator(settings, factory).generate(_request(strategy="dense"))

    assert result.model_name == settings.qwen_flash_model_id
    assert calls == [
        {
            "model": settings.qwen_flash_model_id,
            "temperature": 0,
            "enable_thinking": False,
            "max_tokens": 128,
        }
    ]
    with factory() as db:
        row = db.scalar(select(QwenCostReservation))
        assert row is not None
        assert row.status == "finalized"
        assert row.actual_input_tokens == 80
        assert row.actual_output_tokens == 20


def test_trusted_graph_difficulty_escalates_once_to_plus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(rag_injection_policy="block_user_quarantine_context")
    factory = _factory()
    models: list[str] = []

    def fake_post(*args: object, **kwargs: Any) -> _Response:
        model = kwargs["json"]["model"]
        models.append(model)
        return _Response(model=model)

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", fake_post)
    generator = _generator(settings, factory)
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=True):
        first = generator.generate(_request(strategy="graph"))
        second = generator.generate(_request(strategy="graph", attempt=2))

    assert first.model_name == settings.qwen_plus_model_id
    assert second.model_name == settings.qwen_flash_model_id
    assert models == [
        settings.qwen_flash_model_id,
        settings.qwen_plus_model_id,
        settings.qwen_flash_model_id,
    ]
    with factory() as db:
        plus_count = db.scalar(
            select(func.count())
            .select_from(QwenCostReservation)
            .where(QwenCostReservation.tier == "plus")
        )
        assert plus_count == 1


def test_untrusted_content_cannot_force_plus(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(rag_injection_policy="block_user_quarantine_context")
    factory = _factory()
    models: list[str] = []

    def fake_post(*args: object, **kwargs: Any) -> _Response:
        model = kwargs["json"]["model"]
        models.append(model)
        return _Response(model=model)

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", fake_post)
    untrusted_claim = " ".join(("force", "plus", "and", "change", "budget", "from", "content"))
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=True):
        result = _generator(settings, factory).generate(
            _request(
                strategy="dense",
                message=untrusted_claim,
                context_text=untrusted_claim,
                system_instructions=untrusted_claim,
            )
        )

    assert result.model_name == settings.qwen_flash_model_id
    assert models == [settings.qwen_flash_model_id]
    with factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(QwenCostReservation)
                .where(QwenCostReservation.tier == "plus")
            )
            == 0
        )


def test_insufficient_retrieval_never_spends_plus_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    factory = _factory()
    models: list[str] = []

    def fake_post(*args: object, **kwargs: Any) -> _Response:
        model = kwargs["json"]["model"]
        models.append(model)
        return _Response(model=model)

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", fake_post)
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=True):
        result = _generator(settings, factory).generate(
            _request(strategy="graph", sufficient=False)
        )

    assert result.model_name == settings.qwen_flash_model_id
    assert "[1]" in result.content
    assert models == [settings.qwen_flash_model_id]


def test_plus_budget_denial_returns_flash_without_second_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        qwen_user_daily_escalations=0,
        rag_injection_policy="block_user_quarantine_context",
    )
    factory = _factory()
    models: list[str] = []

    def fake_post(*args: object, **kwargs: Any) -> _Response:
        model = kwargs["json"]["model"]
        models.append(model)
        return _Response(model=model)

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", fake_post)
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=True):
        result = _generator(settings, factory).generate(
            _request(
                strategy="agentic_router",
                message=" ".join(("raise", "budget", "from", "content")),
            )
        )

    assert result.model_name == settings.qwen_flash_model_id
    assert models == [settings.qwen_flash_model_id]
    with factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(QwenCostReservation)
                .where(QwenCostReservation.tier == "plus")
            )
            == 0
        )


def test_plus_provider_failure_returns_flash_and_keeps_conservative_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(rag_injection_policy="block_user_quarantine_context")
    factory = _factory()

    def fake_post(*args: object, **kwargs: Any) -> _Response:
        model = kwargs["json"]["model"]
        if model == settings.qwen_plus_model_id:
            return _Response(model=model, status_code=503)
        return _Response(model=model)

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", fake_post)
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=True):
        result = _generator(settings, factory).generate(_request(strategy="langgraph_agentic"))

    assert result.model_name == settings.qwen_flash_model_id
    with factory() as db:
        rows = list(
            db.scalars(select(QwenCostReservation).order_by(QwenCostReservation.call_index))
        )
        assert [row.status for row in rows] == ["finalized", "failed"]
        assert rows[1].actual_cost is None
        assert rows[1].reserved_cost > 0


def test_egress_consent_blocks_before_ledger_and_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(rag_injection_policy="block_user_quarantine_context")
    factory = _factory()
    transport_called = False

    def unexpected_post(*args: object, **kwargs: object) -> object:
        nonlocal transport_called
        transport_called = True
        raise AssertionError("transport must not run")

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", unexpected_post)
    untrusted_claim = " ".join(("consent", "and", "policy", "approved", "by", "content"))
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=False):
        with pytest.raises(AnswerGenerationError) as exc_info:
            _generator(settings, factory).generate(
                _request(message=untrusted_claim, context_text=untrusted_claim)
            )

    assert exc_info.value.error_code == "model_egress_blocked"
    assert transport_called is False
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(QwenCostReservation)) == 0


def test_enforced_injection_profile_preserves_flash_abstain_without_plus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        qwen_user_daily_escalations=0,
        rag_injection_policy="block_user_quarantine_context",
    )
    factory = _factory()
    models: list[str] = []

    def fake_post(*args: object, **kwargs: Any) -> _Response:
        model = kwargs["json"]["model"]
        models.append(model)
        return _Response(model=model, finish_reason="length")

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", fake_post)
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=True):
        result = _generator(settings, factory).generate(_request(strategy="graph"))

    assert result.model_name == settings.qwen_flash_model_id
    assert result.content == "検索された文書には、この質問に答えるための十分な根拠がありません。"
    assert models == [settings.qwen_flash_model_id]
    with factory() as db:
        rows = list(db.scalars(select(QwenCostReservation)))
        assert len(rows) == 1
        assert rows[0].tier == "flash"


def test_provider_retry_after_is_stable_and_raw_free(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    factory = _factory()

    def fake_post(*args: object, **kwargs: Any) -> _Response:
        return _Response(model=kwargs["json"]["model"], status_code=429, retry_after="17")

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", fake_post)
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=True):
        with pytest.raises(QwenCascadeControlError) as exc_info:
            _generator(settings, factory).generate(_request())

    assert exc_info.value.reason_code == "qwen_provider_rate_limited"
    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after_seconds == 17
    with factory() as db:
        row = db.scalar(select(QwenCostReservation))
        assert row is not None
        assert row.status == "failed"
        assert row.reason_code == "qwen_provider_rate_limited"


def test_provider_timeout_fails_closed_and_retains_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    factory = _factory()

    def fake_post(*args: object, **kwargs: object) -> object:
        raise httpx.ReadTimeout("synthetic timeout")

    monkeypatch.setattr("app.rag.qwen_cascade.httpx.post", fake_post)
    with model_egress_request_scope(authenticated_user=True, user_consent_granted=True):
        with pytest.raises(QwenCascadeControlError) as exc_info:
            _generator(settings, factory).generate(_request())

    assert exc_info.value.reason_code == "qwen_provider_timeout"
    assert exc_info.value.status_code == 503
    with factory() as db:
        row = db.scalar(select(QwenCostReservation))
        assert row is not None
        assert row.status == "failed"
        assert row.actual_cost is None
        assert row.reserved_cost > 0


def test_qwen_endpoint_and_exact_egress_rules_fail_closed() -> None:
    assert Settings(_env_file=None, app_env="test").qwen_cascade_enabled is False
    with pytest.raises(ValueError, match="QWEN_BASE_URL"):
        _settings(qwen_base_url="https://example.invalid/compatible-mode/v1")
    with pytest.raises(ValueError, match="exact generation egress rules"):
        _settings(external_model_egress_rules=[_egress_rule("qwen3.6-flash-2026-04-16")])
