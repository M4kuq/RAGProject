from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.rag.generation import (
    AnswerGenerationError,
    AnthropicMessagesAnswerGenerator,
    GeminiAnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    OpenAIResponsesAnswerGenerator,
    TokenUsage,
    create_answer_generator,
)


def test_settings_requires_openai_api_key_when_provider_is_openai() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(app_env="test", generation_provider="openai", openai_api_key="")

    assert "OPENAI_API_KEY is required" in str(exc_info.value)


def test_openai_generator_calls_responses_api_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "output_text": "The retrieved context says alpha is approved [1].",
                "usage": {"input_tokens": 123, "output_tokens": 45, "total_tokens": 168},
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
    generator = OpenAIResponsesAnswerGenerator(
        api_key="test-openai-key",
        base_url="https://api.openai.com/v1",
        model_name="gpt-5.5",
        timeout_seconds=12.0,
    )

    result = generator.generate(_request())

    assert result.content == "The retrieved context says alpha is approved [1]."
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer test-openai-key"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["json"]["model"] == "gpt-5.5"
    assert captured["json"]["store"] is False
    assert captured["json"]["max_output_tokens"] == 125
    assert "citation marker ids exactly as shown" in captured["json"]["instructions"]
    assert "alpha policy text" in captured["json"]["input"]
    assert "test-openai-key" not in str(captured["json"])
    assert captured["timeout"] == 12.0
    assert result.usage == TokenUsage(input_tokens=123, output_tokens=45, total_tokens=168)


def test_openai_generator_extracts_nested_response_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Nested output cites the context [1].",
                            }
                        ],
                    }
                ]
            }

    monkeypatch.setattr("app.rag.generation.httpx.post", lambda *args, **kwargs: Response())
    generator = OpenAIResponsesAnswerGenerator(
        api_key="test-openai-key",
        base_url="https://api.openai.com/v1/",
        model_name="gpt-5.5",
        timeout_seconds=12.0,
    )

    result = generator.generate(_request())

    assert result.content == "Nested output cites the context [1]."
    assert result.usage is None


def test_openai_responses_generator_sends_text_format_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"output_text": '{"entities":[],"relations":[]}'}

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> Response:
        del url, headers, timeout
        captured["json"] = json
        return Response()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = OpenAIResponsesAnswerGenerator(
        api_key="test-openai-key",
        base_url="https://api.openai.com/v1",
        model_name="gpt-5.5",
        timeout_seconds=12.0,
    )

    result = generator.generate(
        GenerationRequest(
            message="Extract graph JSON.",
            context_items=_request().context_items,
            max_output_chars=500,
            task_instructions="Return JSON with entities and relations.",
            response_format={"type": "json_object"},
        )
    )

    assert captured["json"]["text"] == {"format": {"type": "json_object"}}
    assert result.content == '{"entities":[],"relations":[]}'


def test_openai_responses_generator_converts_chat_json_schema_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"output_text": '{"entities":[],"relations":[]}'}

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> Response:
        del url, headers, timeout
        captured["json"] = json
        return Response()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = OpenAIResponsesAnswerGenerator(
        api_key="test-openai-key",
        base_url="https://api.openai.com/v1",
        model_name="gpt-5.5",
        timeout_seconds=12.0,
    )

    result = generator.generate(
        GenerationRequest(
            message="Extract graph JSON.",
            context_items=_request().context_items,
            max_output_chars=500,
            task_instructions="Return JSON with entities and relations.",
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "graph_extraction_chunk",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "entities": {"type": "array", "items": {"type": "object"}},
                            "relations": {"type": "array", "items": {"type": "object"}},
                        },
                        "required": ["entities", "relations"],
                        "additionalProperties": False,
                    },
                },
            },
        )
    )

    assert captured["json"]["text"]["format"]["type"] == "json_schema"
    assert captured["json"]["text"]["format"]["name"] == "graph_extraction_chunk"
    assert captured["json"]["text"]["format"]["strict"] is True
    assert "json_schema" not in captured["json"]["text"]["format"]
    assert result.content == '{"entities":[],"relations":[]}'


def test_openai_generator_maps_api_error_to_generation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 401

        def json(self) -> dict[str, Any]:
            return {"error": {"message": "not exposed"}}

    monkeypatch.setattr("app.rag.generation.httpx.post", lambda *args, **kwargs: Response())
    generator = OpenAIResponsesAnswerGenerator(
        api_key="test-openai-key",
        base_url="https://api.openai.com/v1",
        model_name="gpt-5.5",
        timeout_seconds=12.0,
    )

    with pytest.raises(AnswerGenerationError) as exc_info:
        generator.generate(_request())
    assert exc_info.value.error_category == "auth"
    assert "not exposed" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("status_code", "expected_category"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "rate_limited"),
        (500, "http_500"),
    ],
)
def test_openai_generator_categorizes_status_errors(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_category: str,
) -> None:
    class Response:
        def __init__(self) -> None:
            self.status_code = status_code

        def json(self) -> dict[str, Any]:
            return {"error": {"message": "secret server detail"}}

    monkeypatch.setattr("app.rag.generation.httpx.post", lambda *args, **kwargs: Response())
    generator = OpenAIResponsesAnswerGenerator(
        api_key="test-openai-key",
        base_url="https://api.openai.com/v1",
        model_name="gpt-5.5",
        timeout_seconds=12.0,
    )

    with pytest.raises(AnswerGenerationError) as exc_info:
        generator.generate(_request())
    assert exc_info.value.error_category == expected_category
    assert "secret server detail" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("exc", "expected_category"),
    [
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("down"), "connection"),
    ],
)
def test_openai_generator_categorizes_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
    exc: httpx.HTTPError,
    expected_category: str,
) -> None:
    def fake_post(*args: Any, **kwargs: Any) -> Any:
        raise exc

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = OpenAIResponsesAnswerGenerator(
        api_key="test-openai-key",
        base_url="https://api.openai.com/v1",
        model_name="gpt-5.5",
        timeout_seconds=12.0,
    )

    with pytest.raises(AnswerGenerationError) as exc_info:
        generator.generate(_request())
    assert exc_info.value.error_category == expected_category


def test_factory_supports_cloud_provider_overrides() -> None:
    settings = Settings(
        _env_file=None,
        generation_provider="fake",
        openai_api_key="test-openai-key",
        anthropic_api_key="test-anthropic-key",
        gemini_api_key="test-gemini-key",
    )

    assert isinstance(
        create_answer_generator(settings, provider="openai", model_name="gpt-5.5"),
        OpenAIResponsesAnswerGenerator,
    )
    assert isinstance(
        create_answer_generator(
            settings,
            provider="anthropic",
            model_name="claude-sonnet-4-20250514",
        ),
        AnthropicMessagesAnswerGenerator,
    )
    assert isinstance(
        create_answer_generator(
            settings,
            provider="gemini",
            model_name="gemini-2.5-flash",
        ),
        GeminiAnswerGenerator,
    )


def test_anthropic_generator_calls_messages_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "content": [{"type": "text", "text": "Claude cites alpha [1]."}],
                "usage": {"input_tokens": 111, "output_tokens": 22},
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
    generator = AnthropicMessagesAnswerGenerator(
        api_key="test-anthropic-key",
        base_url="https://api.anthropic.com",
        api_version="2023-06-01",
        model_name="claude-sonnet-4-20250514",
        timeout_seconds=12.0,
    )

    result = generator.generate(_request())

    assert result.content == "Claude cites alpha [1]."
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "test-anthropic-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["json"]["model"] == "claude-sonnet-4-20250514"
    assert captured["json"]["max_tokens"] == 125
    assert "test-anthropic-key" not in str(captured["json"])
    assert result.usage == TokenUsage(input_tokens=111, output_tokens=22, total_tokens=133)


def test_anthropic_missing_usage_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": "Claude cites alpha [1]."}]}

    monkeypatch.setattr("app.rag.generation.httpx.post", lambda *args, **kwargs: Response())
    generator = AnthropicMessagesAnswerGenerator(
        api_key="test-anthropic-key",
        base_url="https://api.anthropic.com",
        api_version="2023-06-01",
        model_name="claude-sonnet-4-20250514",
        timeout_seconds=12.0,
    )

    result = generator.generate(_request())

    assert result.usage is None


def test_gemini_generator_calls_generate_content_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Gemini cites alpha [1]."}],
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 91,
                    "candidatesTokenCount": 19,
                    "thoughtsTokenCount": 7,
                    "totalTokenCount": 117,
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
    generator = GeminiAnswerGenerator(
        api_key="test-gemini-key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-2.5-flash",
        timeout_seconds=12.0,
    )

    result = generator.generate(_request())

    assert result.content == "Gemini cites alpha [1]."
    assert (
        captured["url"]
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "test-gemini-key"
    assert captured["json"]["generationConfig"]["maxOutputTokens"] == 125
    assert "test-gemini-key" not in str(captured["json"])
    assert result.usage == TokenUsage(input_tokens=91, output_tokens=26, total_tokens=117)


def test_gemini_missing_usage_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Gemini cites alpha [1]."}],
                        }
                    }
                ]
            }

    monkeypatch.setattr("app.rag.generation.httpx.post", lambda *args, **kwargs: Response())
    generator = GeminiAnswerGenerator(
        api_key="test-gemini-key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-2.5-flash",
        timeout_seconds=12.0,
    )

    result = generator.generate(_request())

    assert result.usage is None


def test_provider_generators_honor_explicit_max_output_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payloads: list[dict[str, Any]] = []

    class OpenAIResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"output_text": "OpenAI cites alpha [1]."}

    class AnthropicResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": "Anthropic cites alpha [1]."}]}

    class GeminiResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"candidates": [{"content": {"parts": [{"text": "Gemini cites alpha [1]."}]}}]}

    responses = iter((OpenAIResponse(), AnthropicResponse(), GeminiResponse()))

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> object:
        _ = (url, headers, timeout)
        captured_payloads.append(json)
        return next(responses)

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    request = _request()

    OpenAIResponsesAnswerGenerator(
        api_key="test-openai-key",
        base_url="https://api.openai.com/v1",
        model_name="gpt-5.5",
        timeout_seconds=12.0,
        max_output_tokens=321,
    ).generate(request)
    AnthropicMessagesAnswerGenerator(
        api_key="test-anthropic-key",
        base_url="https://api.anthropic.com",
        api_version="2023-06-01",
        model_name="claude-sonnet-4-20250514",
        timeout_seconds=12.0,
        max_output_tokens=322,
    ).generate(request)
    GeminiAnswerGenerator(
        api_key="test-gemini-key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-2.5-flash",
        timeout_seconds=12.0,
        max_output_tokens=323,
    ).generate(request)

    assert captured_payloads[0]["max_output_tokens"] == 321
    assert captured_payloads[1]["max_tokens"] == 322
    assert captured_payloads[2]["generationConfig"]["maxOutputTokens"] == 323


def test_openai_generation_with_real_api_key_when_enabled() -> None:
    if os.getenv("RUN_OPENAI_GENERATION_TEST") != "true":
        pytest.skip("Set RUN_OPENAI_GENERATION_TEST=true to call the real OpenAI API.")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        pytest.fail("OPENAI_API_KEY is required when RUN_OPENAI_GENERATION_TEST=true.")

    generator = OpenAIResponsesAnswerGenerator(
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model_name=os.getenv("GENERATION_MODEL_NAME", "gpt-5.5"),
        timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30")),
    )

    result = generator.generate(_request())

    assert result.content
    assert "[1]" in result.content
    assert "OPENAI_API_KEY" not in result.content
    assert api_key not in result.content


def _request() -> GenerationRequest:
    return GenerationRequest(
        message="What does the alpha policy say?",
        context_items=[
            GenerationContextItem(
                document_chunk_id=10,
                source_label="alpha-policy.md",
                text="alpha policy text: alpha is approved for the Phase1 RAG demo.",
                local_citation_id=1,
                page_from=1,
                page_to=1,
            )
        ],
        max_output_chars=500,
    )
