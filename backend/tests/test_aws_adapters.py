from __future__ import annotations

import io
import json
import logging
from typing import Any, cast

import pytest

from app.core.config import Settings
from app.ingest.embedding import (
    BedrockTitanEmbeddingAdapter,
    EmbeddingAdapterError,
    create_embedding_adapter,
)
from app.rag.generation import (
    AnswerGenerationError,
    BedrockConverseAnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    create_answer_generator,
)
from app.rag.rerank import (
    BedrockRerankerClient,
    RerankCandidate,
    RerankError,
    create_reranker,
)


class _AwsError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


class _Runtime:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def converse(self, **kwargs: object) -> object:
        self.calls.append(("converse", kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def invoke_model(self, **kwargs: object) -> object:
        self.calls.append(("invoke_model", kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _Rerank:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def rerank(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _settings(**updates: object) -> Settings:
    return Settings(_env_file=None, **cast(Any, updates))


def _request() -> GenerationRequest:
    return GenerationRequest(
        message="What is supported?",
        context_items=[
            GenerationContextItem(
                document_chunk_id=7,
                source_label="guide.md",
                text="The service supports Bedrock.",
                local_citation_id=1,
            )
        ],
        max_output_chars=500,
        temperature=0.1,
    )


def test_bedrock_converse_maps_output_and_usage() -> None:
    client = _Runtime(
        [
            {
                "output": {"message": {"content": [{"text": "Bedrock is supported [1]."}]}},
                "usage": {"inputTokens": 12, "outputTokens": 6, "totalTokens": 18},
            }
        ]
    )
    generator = BedrockConverseAnswerGenerator(
        settings=_settings(),
        model_name="anthropic.test",
        max_output_tokens=512,
        client=client,
    )
    result = generator.generate(_request())
    assert result.content == "Bedrock is supported [1]."
    assert result.usage is not None
    assert result.usage.total_tokens == 18
    assert client.calls[0][1]["inferenceConfig"] == {
        "maxTokens": 512,
        "temperature": 0.1,
    }


def test_bedrock_errors_are_categorized_and_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _Runtime([_AwsError("ThrottlingException", "SECRET-CONTEXT")])
    generator = BedrockConverseAnswerGenerator(
        settings=_settings(),
        model_name="anthropic.test",
        max_output_tokens=512,
        client=client,
    )
    with caplog.at_level(logging.WARNING), pytest.raises(AnswerGenerationError) as exc_info:
        generator.generate(_request())
    assert exc_info.value.error_category == "rate_limited"
    assert "SECRET-CONTEXT" not in caplog.text


def test_bedrock_titan_embedding_maps_request_and_vector() -> None:
    vector = [0.1] * 256
    client = _Runtime([{"body": io.BytesIO(json.dumps({"embedding": vector}).encode())}])
    adapter = BedrockTitanEmbeddingAdapter(
        settings=_settings(),
        model_name="amazon.titan-embed-text-v2:0",
        dimension=256,
        client=client,
    )
    assert adapter.embed_texts(["alpha"]) == [vector]
    body = json.loads(cast(str, client.calls[0][1]["body"]))
    assert body == {"inputText": "alpha", "dimensions": 256, "normalize": True}


def test_bedrock_titan_rejects_malformed_vector() -> None:
    client = _Runtime([{"body": io.BytesIO(json.dumps({"embedding": [0.1]}).encode())}])
    adapter = BedrockTitanEmbeddingAdapter(
        settings=_settings(),
        model_name="amazon.titan-embed-text-v2:0",
        dimension=256,
        client=client,
    )
    with pytest.raises(EmbeddingAdapterError) as exc_info:
        adapter.embed_texts(["alpha"])
    assert exc_info.value.error_code == "embedding_dimension_mismatch"


def test_bedrock_rerank_maps_original_indexes() -> None:
    client = _Rerank(
        {
            "results": [
                {"index": 1, "relevanceScore": 0.9},
                {"index": 0, "relevanceScore": 0.4},
            ]
        }
    )
    reranker = BedrockRerankerClient(
        settings=_settings(),
        model_name="amazon.rerank-v1:0",
        client=client,
    )
    candidates = [
        RerankCandidate(document_chunk_id=10, text="a", retrieval_score=0.2),
        RerankCandidate(document_chunk_id=20, text="b", retrieval_score=0.1),
    ]
    results = reranker.rerank(query="query", candidates=candidates)
    assert [item.document_chunk_id for item in results] == [20, 10]
    configuration = cast(dict[str, Any], client.calls[0]["rerankingConfiguration"])
    bedrock_configuration = cast(dict[str, Any], configuration["bedrockRerankingConfiguration"])
    assert bedrock_configuration["modelConfiguration"] == {
        "modelArn": ("arn:aws:bedrock:ap-northeast-1::foundation-model/amazon.rerank-v1:0")
    }


def test_bedrock_rerank_rejects_duplicate_indexes() -> None:
    client = _Rerank(
        {
            "results": [
                {"index": 0, "relevanceScore": 0.9},
                {"index": 0, "relevanceScore": 0.4},
            ]
        }
    )
    reranker = BedrockRerankerClient(
        settings=_settings(),
        model_name="amazon.rerank-v1:0",
        client=client,
    )
    candidates = [
        RerankCandidate(document_chunk_id=10, text="a", retrieval_score=0.2),
        RerankCandidate(document_chunk_id=20, text="b", retrieval_score=0.1),
    ]
    with pytest.raises(RerankError) as exc_info:
        reranker.rerank(query="query", candidates=candidates)
    assert exc_info.value.error_category == "invalid_response"


def test_bedrock_settings_and_factories(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[str] = []

    def fake_client(
        service_name: str,
        settings: Settings,
        *,
        read_timeout_seconds: float | None = None,
    ) -> Any:
        del settings
        clients.append(f"{service_name}:{read_timeout_seconds}")
        if service_name == "bedrock-agent-runtime":
            return _Rerank({"results": []})
        return _Runtime([])

    import app.ingest.embedding as embedding_module
    import app.rag.generation as generation_module
    import app.rag.rerank as rerank_module

    monkeypatch.setattr(embedding_module, "create_aws_client", fake_client)
    monkeypatch.setattr(generation_module, "create_aws_client", fake_client)
    monkeypatch.setattr(rerank_module, "create_aws_client", fake_client)
    settings = _settings(
        generation_provider="bedrock",
        embedding_provider="bedrock",
        rerank_provider="bedrock",
        embedding_vector_dimension=1024,
    )
    assert isinstance(
        create_answer_generator(settings, timeout_seconds=123),
        BedrockConverseAnswerGenerator,
    )
    assert isinstance(create_embedding_adapter(settings), BedrockTitanEmbeddingAdapter)
    assert isinstance(create_reranker(settings), BedrockRerankerClient)
    assert settings.generation_model_name == settings.bedrock_generation_model_id
    assert settings.embedding_model == settings.bedrock_embedding_model_id
    assert settings.reranker_model == settings.bedrock_rerank_model_id
    assert clients == [
        "bedrock-runtime:123",
        "bedrock-runtime:None",
        "bedrock-agent-runtime:None",
    ]


def test_bedrock_embedding_dimension_is_restricted() -> None:
    with pytest.raises(ValueError, match="256, 512, or 1024"):
        _settings(embedding_provider="bedrock", embedding_vector_dimension=768)
