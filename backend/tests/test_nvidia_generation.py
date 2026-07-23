from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.rag.generation import (
    AnswerGenerationError,
    GenerationContextItem,
    GenerationRequest,
    OpenAICompatibleChatAnswerGenerator,
    TokenUsage,
    create_answer_generator,
)
from app.schemas.evaluations import EvaluationRunCreateRequest

NVIDIA_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"


def test_settings_requires_nvidia_api_key_when_provider_is_nvidia() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            app_env="test",
            generation_provider="nvidia",
            nvidia_api_key="",
        )

    assert "NVIDIA_API_KEY is required" in str(exc_info.value)


def test_settings_rejects_nvidia_provider_outside_local_or_test() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            app_env="production",
            generation_provider="nvidia",
            generation_model_name=NVIDIA_MODEL,
            nvidia_api_key="test-nvidia-key",
            session_cookie_secure=True,
            session_secret="x" * 32,
        )

    assert "only available in local environments" in str(exc_info.value)


def test_factory_rejects_nvidia_override_outside_local_or_test() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        generation_provider="fake",
        nvidia_api_key="test-nvidia-key",
        session_cookie_secure=True,
        session_secret="x" * 32,
    )

    with pytest.raises(AnswerGenerationError):
        create_answer_generator(settings, provider="nvidia", model_name=NVIDIA_MODEL)


def test_nvidia_generator_uses_standard_chat_completions_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "choices": [{"message": {"content": "Alpha is approved [1]."}}],
                "usage": {
                    "prompt_tokens": 31,
                    "completion_tokens": 8,
                    "total_tokens": 39,
                },
            }

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> Response:
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    settings = Settings(
        _env_file=None,
        app_env="test",
        generation_provider="nvidia",
        generation_model_name=NVIDIA_MODEL,
        generation_max_output_tokens=1024,
        nvidia_api_key="test-nvidia-key",
        nvidia_base_url="https://integrate.api.nvidia.com/v1/",
        nvidia_timeout_seconds=45,
    )

    generator = create_answer_generator(settings)
    result = generator.generate(_request())

    assert isinstance(generator, OpenAICompatibleChatAnswerGenerator)
    assert generator.disable_thinking is False
    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["headers"] == {
        "Authorization": "Bearer test-nvidia-key",
        "Content-Type": "application/json",
    }
    payload = captured["json"]
    assert payload["model"] == NVIDIA_MODEL
    assert payload["max_tokens"] == 1024
    assert payload["temperature"] == 0.2
    assert payload["stream"] is False
    assert "enable_thinking" not in payload
    assert "chat_template_kwargs" not in payload
    assert "test-nvidia-key" not in str(payload)
    assert captured["timeout"] == 45
    assert result.content == "Alpha is approved [1]."
    assert result.usage == TokenUsage(input_tokens=31, output_tokens=8, total_tokens=39)


@pytest.mark.parametrize(
    ("status_code", "expected_category"),
    [(401, "auth"), (403, "auth"), (429, "rate_limited"), (500, "http_500")],
)
def test_nvidia_generator_categorizes_status_errors(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_category: str,
) -> None:
    class Response:
        def __init__(self) -> None:
            self.status_code = status_code

        def json(self) -> dict[str, Any]:
            return {"error": {"message": "sensitive provider detail"}}

    monkeypatch.setattr("app.rag.generation.httpx.post", lambda *args, **kwargs: Response())
    generator = _generator()

    with pytest.raises(AnswerGenerationError) as exc_info:
        generator.generate(_request())

    assert exc_info.value.error_category == expected_category
    assert "sensitive provider detail" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("error", "expected_category"),
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("offline"), "connection"),
    ],
)
def test_nvidia_generator_categorizes_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: httpx.HTTPError,
    expected_category: str,
) -> None:
    def fake_post(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)

    with pytest.raises(AnswerGenerationError) as exc_info:
        _generator().generate(_request())

    assert exc_info.value.error_category == expected_category


def test_nvidia_generator_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            raise ValueError("invalid json")

    monkeypatch.setattr("app.rag.generation.httpx.post", lambda *args, **kwargs: Response())

    with pytest.raises(AnswerGenerationError):
        _generator().generate(_request())


def test_evaluation_request_accepts_nvidia_generation_selection() -> None:
    request = EvaluationRunCreateRequest(
        dataset_name="phase1_smoke",
        case_limit=1,
        strategies=["llm_tool_orchestrator"],
        generation_provider="nvidia",
        generation_model=NVIDIA_MODEL,
    )

    assert request.generation_provider == "nvidia"
    assert request.generation_model == NVIDIA_MODEL


def test_nvidia_generation_with_real_api_key_when_enabled() -> None:
    if os.getenv("RUN_NVIDIA_GENERATION_TEST") != "true":
        pytest.skip("Set RUN_NVIDIA_GENERATION_TEST=true to call the NVIDIA API.")
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        pytest.fail("NVIDIA_API_KEY is required when RUN_NVIDIA_GENERATION_TEST=true.")

    generator = OpenAICompatibleChatAnswerGenerator(
        api_key=api_key,
        base_url=os.getenv(
            "NVIDIA_BASE_URL",
            "https://integrate.api.nvidia.com/v1",
        ),
        model_name=os.getenv("NVIDIA_MODEL_NAME", NVIDIA_MODEL),
        timeout_seconds=float(os.getenv("NVIDIA_TIMEOUT_SECONDS", "60")),
        max_output_tokens=512,
        disable_thinking=False,
    )

    result = generator.generate(_request())

    assert result.content
    assert "[1]" in result.content
    assert api_key not in result.content


def _generator() -> OpenAICompatibleChatAnswerGenerator:
    return OpenAICompatibleChatAnswerGenerator(
        api_key="test-nvidia-key",
        base_url="https://integrate.api.nvidia.com/v1",
        model_name=NVIDIA_MODEL,
        timeout_seconds=30,
        disable_thinking=False,
    )


def _request() -> GenerationRequest:
    return GenerationRequest(
        message="What does the alpha policy say?",
        context_items=[
            GenerationContextItem(
                document_chunk_id=10,
                source_label="alpha-policy.md",
                text="The alpha policy says alpha is approved.",
                local_citation_id=1,
            )
        ],
        max_output_chars=500,
    )
