from __future__ import annotations

import json as json_module
import os
from typing import Any

import pytest

from app.core.config import Settings
from app.rag.generation import (
    GenerationContextItem,
    GenerationRequest,
    OpenAICompatibleChatAnswerGenerator,
    TokenUsage,
    create_answer_generator,
)


def test_lmstudio_generator_calls_native_chat_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "output": [
                    {
                        "type": "message",
                        "content": "Qwen3.5 cites the local context [1].",
                    }
                ],
                "stats": {"input_tokens": 12, "total_output_tokens": 8},
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
    assert result.usage == TokenUsage(input_tokens=12, output_tokens=8, total_tokens=20)
    assert captured["url"] == "http://host.docker.internal:1234/api/v1/chat"
    assert captured["headers"]["Authorization"] == "Bearer lm-studio"
    assert captured["json"]["model"] == "lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M"
    assert captured["json"]["input"].startswith("/no_think\n")
    assert "/no_think" in captured["json"]["system_prompt"]
    assert captured["json"]["max_output_tokens"] == 8192
    assert "reasoning" not in captured["json"]
    assert captured["json"]["stream"] is False
    assert captured["json"]["store"] is False


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


def test_lmstudio_task_request_preserves_raw_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_json = (
        '{"entities":[],"relations":[{"source":"Graph Index","target":"Hybrid RAG",'
        '"relation_type":"supports","evidence":"Graph Index supports Hybrid RAG.",'
        '"confidence":0.92}]}'
    )
    captured: dict[str, Any] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": raw_json,
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
        del headers, timeout
        captured["url"] = url
        captured["json"] = json
        return Response()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = OpenAICompatibleChatAnswerGenerator(
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="qwen3.5-9b",
        timeout_seconds=60.0,
    )

    result = generator.generate(
        GenerationRequest(
            message="Extract graph JSON.",
            context_items=[
                GenerationContextItem(
                    document_chunk_id=10,
                    source_label="graph.md",
                    text="Graph Index supports Hybrid RAG.",
                    local_citation_id=1,
                )
            ],
            max_output_chars=500,
            system_instructions="Return JSON only.",
            task_instructions="Return JSON with entities and relations.",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    )

    payload = captured["json"]
    assert isinstance(payload, dict)
    assert captured["url"] == "http://host.docker.internal:1234/v1/chat/completions"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == 128
    assert payload["stream"] is False
    assert "reasoning" not in payload
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert messages[0] == {"role": "system", "content": "Return JSON only."}
    user_content = messages[1]["content"]
    assert "Task:" in user_content
    assert "Return the final answer only." not in user_content
    assert result.content == raw_json
    assert json_module.loads(result.content)["relations"][0]["confidence"] == 0.92


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
    assert captured["json"]["model"] == "qwen/qwen3.5-9b"


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
