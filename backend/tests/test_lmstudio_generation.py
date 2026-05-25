from __future__ import annotations

import os
from typing import Any

import pytest

from app.core.config import Settings
from app.rag.generation import (
    GenerationContextItem,
    GenerationRequest,
    OpenAICompatibleChatAnswerGenerator,
    create_answer_generator,
)


def test_lmstudio_generator_calls_openai_compatible_chat_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Qwen3.5 cites the local context [1].",
                        }
                    }
                ]
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
    generator = OpenAICompatibleChatAnswerGenerator(
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M",
        timeout_seconds=60.0,
    )

    result = generator.generate(_request())

    assert result.content == "Qwen3.5 cites the local context [1]."
    assert captured["url"] == "http://host.docker.internal:1234/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer lm-studio"
    assert captured["json"]["model"] == "lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["messages"][1]["role"] == "user"
    assert captured["json"]["max_tokens"] == 8192
    assert captured["json"]["stream"] is False


def test_lmstudio_generator_removes_qwen_thinking_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                "<think>private reasoning</think>\n\n"
                                "Final answer: Qdrant is used for Phase1 vector search [1]."
                            ),
                        }
                    }
                ]
            }

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> Response:
        return Response()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = OpenAICompatibleChatAnswerGenerator(
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="qwen3.5-9b",
        timeout_seconds=60.0,
    )

    result = generator.generate(_request())

    assert result.content == "Qdrant is used for Phase1 vector search [1]."


def test_lmstudio_factory_normalizes_huggingface_repo_model_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Qdrant is used for Phase1 vector search [1].",
                        }
                    }
                ]
            }

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: float,
    ) -> Response:
        captured["json"] = json
        return Response()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = create_answer_generator(
        Settings(
            _env_file=None,
            generation_provider="lmstudio",
            generation_model_name="lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M",
            lmstudio_api_key="lm-studio",
            lmstudio_base_url="http://host.docker.internal:1234/v1",
        )
    )

    result = generator.generate(_request())

    assert result.content == "Qdrant is used for Phase1 vector search [1]."
    assert captured["json"]["model"] == "qwen3.5-9b"


def test_lmstudio_generation_with_local_server_when_enabled() -> None:
    if os.getenv("RUN_LMSTUDIO_GENERATION_TEST") != "true":
        pytest.skip("Set RUN_LMSTUDIO_GENERATION_TEST=true to call local LM Studio.")

    generator = OpenAICompatibleChatAnswerGenerator(
        api_key=os.getenv("LMSTUDIO_API_KEY", "lm-studio"),
        base_url=os.getenv("LMSTUDIO_BASE_URL", "http://host.docker.internal:1234/v1"),
        model_name=os.getenv(
            "GENERATION_MODEL_NAME",
            "lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M",
        ),
        timeout_seconds=float(os.getenv("LMSTUDIO_TIMEOUT_SECONDS", "60")),
    )

    result = generator.generate(_request())

    assert result.content
    assert "[1]" in result.content


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
