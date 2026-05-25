from __future__ import annotations

from app.core.config import Settings
from app.rag.generation import (
    GenerationContextItem,
    GenerationRequest,
    OllamaAnswerGenerator,
    OpenAICompatibleChatAnswerGenerator,
    create_answer_generator,
)

ANSWER_TEXT = (
    "Thinking Process: draft says Final answer: ignore this draft. "
    "Final answer: Phase1 は Qdrant をベクトル検索に使用しています [1]。"
)
REASONING_DRAFT_TEXT = (
    "The user wants a concise explanation. I will synthesize this into Japanese. "
    "Drafting the answer: Phase1 の技術スタックは "
    "FastAPI、React、PostgreSQL、Qdrant で構成されています [1]。 "
    "Refining for conciseness. Check Citation [1]."
)


class DummyResponse:
    status_code = 200

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": ANSWER_TEXT}}]}


class DraftResponse:
    status_code = 200

    def json(self) -> dict[str, object]:
        return {"choices": [{"message": {"content": REASONING_DRAFT_TEXT}}]}


class LegitimateWillResponse:
    status_code = 200

    def json(self) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "I will explain the Phase1 stack using Qdrant for retrieval [1]."
                    }
                }
            ]
        }


def test_lmstudio_generator_uses_openai_compatible_chat_api(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> DummyResponse:
        captured["url"] = url
        captured.update(kwargs)
        return DummyResponse()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = OpenAICompatibleChatAnswerGenerator(
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="qwen3.5-9b",
        timeout_seconds=180,
    )
    result = generator.generate(
        GenerationRequest(
            message="What vector database is used by Phase1?",
            context_items=[
                GenerationContextItem(
                    document_chunk_id=1,
                    source_label="phase1-seed.md",
                    text="Phase1 uses PostgreSQL and Qdrant.",
                    local_citation_id=1,
                )
            ],
            max_output_chars=2000,
        )
    )

    assert captured["url"] == "http://host.docker.internal:1234/v1/chat/completions"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen3.5-9b"
    assert payload["enable_thinking"] is False
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["max_tokens"] == 8192
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert "/no_think" in messages[0]["content"]
    assert messages[1]["content"].startswith("/no_think\n")
    assert "Citation [1]" in messages[1]["content"]
    assert "Return the final answer only." in messages[1]["content"]
    assert "十分な根拠がありません" in messages[1]["content"]
    assert result.content == "Phase1 は Qdrant をベクトル検索に使用しています [1]。"


def test_create_answer_generator_supports_lmstudio() -> None:
    settings = Settings(
        generation_provider="lmstudio",
        generation_model_name="qwen3.5-9b",
        lmstudio_base_url="http://host.docker.internal:1234/v1/",
    )

    generator = create_answer_generator(settings)

    assert isinstance(generator, OpenAICompatibleChatAnswerGenerator)


def test_create_answer_generator_uses_ollama_generation_timeout() -> None:
    settings = Settings(
        generation_provider="ollama",
        generation_model_name="llama3.1",
        qdrant_timeout_seconds=3,
        ollama_timeout_seconds=123,
    )

    generator = create_answer_generator(settings)

    assert isinstance(generator, OllamaAnswerGenerator)
    assert generator.timeout_seconds == 123


def test_lmstudio_generator_extracts_final_answer_from_reasoning_draft(monkeypatch) -> None:
    def fake_post(url: str, **kwargs: object) -> DraftResponse:
        return DraftResponse()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = OpenAICompatibleChatAnswerGenerator(
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="qwen3.5-9b",
        timeout_seconds=180,
    )

    result = generator.generate(
        GenerationRequest(
            message="Phase1 の技術スタックを簡潔に説明してください。",
            context_items=[
                GenerationContextItem(
                    document_chunk_id=1,
                    source_label="phase1-seed.md",
                    text="Phase1 uses FastAPI, React, PostgreSQL, and Qdrant.",
                    local_citation_id=1,
                )
            ],
            max_output_chars=2000,
        )
    )

    assert result.content == (
        "Phase1 の技術スタックは FastAPI、React、PostgreSQL、Qdrant で構成されています [1]。"
    )


def test_lmstudio_generator_keeps_legitimate_i_will_answer(monkeypatch) -> None:
    def fake_post(url: str, **kwargs: object) -> LegitimateWillResponse:
        return LegitimateWillResponse()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = OpenAICompatibleChatAnswerGenerator(
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="qwen3.5-9b",
        timeout_seconds=180,
    )

    result = generator.generate(
        GenerationRequest(
            message="Explain the Phase1 stack.",
            context_items=[
                GenerationContextItem(
                    document_chunk_id=1,
                    source_label="phase1-seed.md",
                    text="Phase1 uses Qdrant for retrieval.",
                    local_citation_id=1,
                )
            ],
            max_output_chars=2000,
        )
    )

    assert result.content == "I will explain the Phase1 stack using Qdrant for retrieval [1]."


def test_lmstudio_generator_drops_incomplete_tail_after_final_answer(monkeypatch) -> None:
    class IncompleteTailResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "Drafting the answer: Phase1 は FastAPI と React を使います [1]。"
                                "ローカルモデルテストには LM"
                            )
                        }
                    }
                ]
            }

    def fake_post(url: str, **kwargs: object) -> IncompleteTailResponse:
        return IncompleteTailResponse()

    monkeypatch.setattr("app.rag.generation.httpx.post", fake_post)
    generator = OpenAICompatibleChatAnswerGenerator(
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="qwen3.5-9b",
        timeout_seconds=180,
    )

    result = generator.generate(
        GenerationRequest(
            message="Phase1 の技術スタックを簡潔に説明してください。",
            context_items=[
                GenerationContextItem(
                    document_chunk_id=1,
                    source_label="phase1-seed.md",
                    text="Phase1 uses FastAPI and React.",
                    local_citation_id=1,
                )
            ],
            max_output_chars=2000,
        )
    )

    assert result.content == "Phase1 は FastAPI と React を使います [1]。"
