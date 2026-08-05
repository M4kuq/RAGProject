from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.model_egress import ModelEgressGuard
from app.rag.generation import (
    AnswerGenerationError,
    GenerationRequest,
    GenerationResult,
    _extract_chat_completion_output_text,
    _extract_chat_completion_usage,
    _generation_output_text,
    _openai_input,
    _protect_generation_request,
    _system_instructions,
)
from app.rag.model_cascade_guard import authorize_model_tier
from app.services.qwen_cost_control_service import (
    QwenCostControlDenied,
    QwenCostControlService,
    QwenCostControlUnavailable,
)

logger = logging.getLogger(__name__)

_INSUFFICIENT_EVIDENCE = "検索された文書には、この質問に答えるための十分な根拠がありません。"
_DIFFICULT_STRATEGIES = frozenset(
    {
        "graph",
        "graph_postgres",
        "graph_neo4j",
        "agentic_router",
        "llm_tool_orchestrator",
        "langchain_agentic",
        "langgraph_agentic",
    }
)
_FLASH_ESCALATABLE_FAILURES = frozenset(
    {"qwen_invalid_response", "qwen_output_truncated", "qwen_usage_missing"}
)


class QwenCascadeControlError(AnswerGenerationError):
    def __init__(self, *, reason_code: str, status_code: int, retry_after_seconds: int) -> None:
        super().__init__(error_code=reason_code, error_category=reason_code)
        self.reason_code = reason_code
        self.status_code = status_code
        self.retry_after_seconds = max(1, retry_after_seconds)


class _QwenTransportFailure(AnswerGenerationError):
    def __init__(self, *, reason_code: str, retry_after_seconds: int = 1) -> None:
        super().__init__(error_code=reason_code, error_category=reason_code)
        self.reason_code = reason_code
        self.retry_after_seconds = max(1, retry_after_seconds)


@dataclass(frozen=True)
class _QwenTransportOutcome:
    result: GenerationResult
    failure_reason: str | None = None


class _QwenChatTransport:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_id: str,
        timeout_seconds: float,
        max_output_tokens: int,
        egress_guard: ModelEgressGuard,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.egress_guard = egress_guard

    def protect(self, request: GenerationRequest) -> GenerationRequest:
        return _protect_generation_request(
            request,
            self.egress_guard,
            provider="qwen",
            model=self.model_id,
        )

    def send(self, request: GenerationRequest) -> _QwenTransportOutcome:
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model_id,
                    "messages": [
                        {"role": "system", "content": _system_instructions(request)},
                        {"role": "user", "content": _openai_input(request)},
                    ],
                    "max_tokens": self.max_output_tokens,
                    "temperature": 0,
                    "enable_thinking": False,
                    "stream": False,
                },
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise _QwenTransportFailure(reason_code="qwen_provider_timeout") from exc
        except httpx.HTTPError as exc:
            raise _QwenTransportFailure(reason_code="qwen_provider_connection") from exc
        if response.status_code >= 400:
            reason_code = (
                "qwen_provider_rate_limited"
                if response.status_code == 429
                else "qwen_provider_auth_failed"
                if response.status_code in {401, 403}
                else "qwen_provider_unavailable"
            )
            raise _QwenTransportFailure(
                reason_code=reason_code,
                retry_after_seconds=_retry_after_seconds(response),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise _QwenTransportFailure(reason_code="qwen_invalid_response") from exc
        if not isinstance(payload, dict):
            raise _QwenTransportFailure(reason_code="qwen_invalid_response")
        content = _extract_chat_completion_output_text(payload)
        if not content:
            raise _QwenTransportFailure(reason_code="qwen_invalid_response")
        usage = _extract_chat_completion_usage(payload)
        if usage is None or usage.input_tokens is None or usage.output_tokens is None:
            raise _QwenTransportFailure(reason_code="qwen_usage_missing")
        finish_reason = _finish_reason(payload)
        failure_reason = None
        if finish_reason != "stop":
            failure_reason = (
                "qwen_output_truncated" if finish_reason == "length" else "qwen_invalid_response"
            )
        final_content = _generation_output_text(content, request, cleanup_final_answer=True)
        if not final_content:
            raise _QwenTransportFailure(reason_code="qwen_invalid_response")
        return _QwenTransportOutcome(
            GenerationResult(
                content=final_content,
                usage=usage,
                provider="qwen",
                model_name=self.model_id,
            ),
            failure_reason=failure_reason,
        )


class QwenCascadeAnswerGenerator:
    """Server-controlled Flash-to-Plus cascade with no content-driven routing."""

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session],
        egress_guard: ModelEgressGuard,
    ) -> None:
        if not settings.qwen_api_key:
            raise ValueError("Qwen cascade requires a configured API key")
        self.settings = settings
        self.session_factory = session_factory
        self.cost_control = QwenCostControlService(settings)
        self.flash = _QwenChatTransport(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            model_id=settings.qwen_flash_model_id,
            timeout_seconds=settings.qwen_timeout_seconds,
            max_output_tokens=settings.qwen_max_output_tokens,
            egress_guard=egress_guard,
        )
        self.plus = _QwenChatTransport(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            model_id=settings.qwen_plus_model_id,
            timeout_seconds=settings.qwen_timeout_seconds,
            max_output_tokens=settings.qwen_max_output_tokens,
            egress_guard=egress_guard,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self._validate_trusted_context(request)
        flash_outcome: _QwenTransportOutcome | None = None
        flash_failure: _QwenTransportFailure | None = None
        try:
            flash_outcome = self._call_tier(request, tier="flash")
        except _QwenTransportFailure as exc:
            flash_failure = exc
        except QwenCostControlDenied as exc:
            raise QwenCascadeControlError(
                reason_code=exc.reason_code,
                status_code=exc.status_code,
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc
        except QwenCostControlUnavailable as exc:
            raise QwenCascadeControlError(
                reason_code=exc.reason_code,
                status_code=exc.status_code,
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

        plus_eligible = request.trusted_retrieval_sufficient and (
            request.trusted_strategy in _DIFFICULT_STRATEGIES
            or (
                flash_failure is not None
                and flash_failure.reason_code in _FLASH_ESCALATABLE_FAILURES
            )
            or (
                flash_outcome is not None
                and flash_outcome.failure_reason in _FLASH_ESCALATABLE_FAILURES
            )
        )
        decision = authorize_model_tier(
            requested_tier="plus" if plus_eligible else "flash",
            origin="trusted_policy",
            policy_eligible=plus_eligible,
            escalations_in_request=0,
            max_escalations_per_request=1,
            estimated_cost_units=0,
            remaining_user_cost_units=0,
            remaining_daily_cost_units=0,
        )
        if decision.selected_tier == "plus" and decision.allowed:
            try:
                return self._call_tier(request, tier="plus").result
            except (QwenCostControlDenied, QwenCostControlUnavailable, _QwenTransportFailure):
                logger.warning(
                    "qwen plus fallback to flash or abstain",
                    extra={"reason_code": "qwen_plus_unavailable"},
                )

        if flash_outcome is not None and flash_outcome.failure_reason is None:
            return flash_outcome.result
        if flash_outcome is not None:
            return GenerationResult(
                content=_INSUFFICIENT_EVIDENCE,
                usage=flash_outcome.result.usage,
                provider="qwen",
                model_name=self.settings.qwen_flash_model_id,
            )
        if flash_failure is not None:
            raise QwenCascadeControlError(
                reason_code=flash_failure.reason_code,
                status_code=429
                if flash_failure.reason_code == "qwen_provider_rate_limited"
                else 503,
                retry_after_seconds=flash_failure.retry_after_seconds,
            ) from flash_failure
        return GenerationResult(
            content=_INSUFFICIENT_EVIDENCE,
            provider="qwen",
            model_name=self.settings.qwen_flash_model_id,
        )

    def _call_tier(
        self,
        request: GenerationRequest,
        *,
        tier: Literal["flash", "plus"],
    ) -> _QwenTransportOutcome:
        transport = self.plus if tier == "plus" else self.flash
        protected_request = transport.protect(request)
        protected_input = _system_instructions(protected_request) + _openai_input(protected_request)
        # A UTF-8 byte bound is intentionally more conservative than chars/4.
        # It prevents an atomic reservation from understating tokenizer usage.
        estimated_input = len(protected_input.encode("utf-8"))
        call_index = (request.trusted_generation_attempt - 1) * 2 + (2 if tier == "plus" else 1)
        with self.session_factory() as db:
            permit = self.cost_control.reserve(
                db,
                user_id=request.trusted_user_id or 0,
                request_id=request.trusted_request_id or "missing",
                tier=tier,
                call_index=call_index,
                estimated_input_tokens=estimated_input,
                reserved_output_tokens=self.settings.qwen_max_output_tokens,
            )
        try:
            outcome = transport.send(protected_request)
        except _QwenTransportFailure as exc:
            with self.session_factory() as db:
                self.cost_control.fail_after_transport(
                    db,
                    reservation_id=permit.reservation_id,
                    reason_code=exc.reason_code,
                    provider_failure=True,
                )
            raise
        usage = outcome.result.usage
        assert usage is not None
        assert usage.input_tokens is not None
        assert usage.output_tokens is not None
        with self.session_factory() as db:
            self.cost_control.finalize(
                db,
                reservation_id=permit.reservation_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
        return outcome

    @staticmethod
    def _validate_trusted_context(request: GenerationRequest) -> None:
        if (
            not request.context_items
            or request.trusted_user_id is None
            or not request.trusted_request_id
            or not request.trusted_strategy
            or request.trusted_generation_attempt < 1
        ):
            raise QwenCascadeControlError(
                reason_code="qwen_trusted_routing_context_missing",
                status_code=503,
                retry_after_seconds=1,
            )


def _finish_reason(payload: dict[str, object]) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return None
    value = choices[0].get("finish_reason")
    return value if isinstance(value, str) else None


def _retry_after_seconds(response: httpx.Response) -> int:
    value = response.headers.get("Retry-After", "").strip()
    if value.isdigit():
        return max(1, min(3600, int(value)))
    return 60 if response.status_code == 429 else 5
