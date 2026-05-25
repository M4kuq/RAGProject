from __future__ import annotations

from app.core.config import Settings
from app.ingest.embedding import (
    FakeEmbeddingAdapter,
    LMStudioEmbeddingAdapter,
    create_embedding_adapter,
)
from app.rag.rerank import FakeRerankerClient, NoopRerankerClient, RerankCandidate


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def test_fake_embedding_keeps_shared_terms_retrievable() -> None:
    adapter = FakeEmbeddingAdapter(dimension=32)
    query, matching, unrelated = adapter.embed_texts(
        [
            "Phase1 vector database",
            "Phase1 uses the Qdrant vector database for retrieval.",
            "Chain of Thought prompting improves reasoning examples.",
        ]
    )

    assert _dot(query, matching) > _dot(query, unrelated)


def test_fake_embedding_expands_japanese_phase1_stack_terms() -> None:
    adapter = FakeEmbeddingAdapter(dimension=32)
    query, phase1_stack, operations_policy, unrelated = adapter.embed_texts(
        [
            "Phase1 の技術スタックを簡潔に説明してください。",
            "RAGProject Phase1 validates a local Docker Compose RAG stack with "
            "PostgreSQL for relational state and Qdrant vector database retrieval.",
            "Phase1 operation policy keeps demo data local for Admin and Viewer users.",
            "MetaGPT organized LLM agents around software-team roles.",
        ]
    )

    assert _dot(query, phase1_stack) > _dot(query, operations_policy)
    assert _dot(query, phase1_stack) > _dot(query, unrelated)


def test_fake_reranker_expands_japanese_phase1_stack_terms() -> None:
    reranker = FakeRerankerClient()

    results = reranker.rerank(
        query="Phase1 の技術スタックを簡潔に説明してください。",
        candidates=[
            RerankCandidate(
                document_chunk_id=1,
                text="Phase1 operation policy keeps demo data local.",
                retrieval_score=0.99,
            ),
            RerankCandidate(
                document_chunk_id=2,
                text=(
                    "RAGProject Phase1 uses Docker Compose, PostgreSQL, Qdrant, "
                    "RAG retrieval, citations, confidence, MCP, backend, and frontend."
                ),
                retrieval_score=0.8,
            ),
        ],
    )

    assert results[0].document_chunk_id == 2


def test_noop_reranker_preserves_retrieval_order_without_fake_scoring() -> None:
    reranker = NoopRerankerClient()

    results = reranker.rerank(
        query="Phase1 の技術スタック",
        candidates=[
            RerankCandidate(document_chunk_id=10, text="first", retrieval_score=0.7),
            RerankCandidate(document_chunk_id=20, text="second", retrieval_score=0.9),
        ],
    )

    assert [result.document_chunk_id for result in results] == [10, 20]
    assert [result.rerank_order for result in results] == [1, 2]
    assert [result.rerank_score for result in results] == [0.7, 0.9]


def test_lmstudio_embedding_adapter_uses_openai_compatible_embeddings_api(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                    {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                ]
            }

    def fake_post(url: str, **kwargs: object) -> DummyResponse:
        captured["url"] = url
        captured.update(kwargs)
        return DummyResponse()

    monkeypatch.setattr("app.ingest.embedding.httpx.post", fake_post)
    adapter = LMStudioEmbeddingAdapter(
        api_key="lm-studio",
        base_url="http://host.docker.internal:1234/v1",
        model_name="text-embedding-nomic-embed-text-v1.5",
        dimension=3,
        timeout_seconds=180,
    )

    vectors = adapter.embed_texts(["alpha", "beta"])

    assert vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert captured["url"] == "http://host.docker.internal:1234/v1/embeddings"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["model"] == "text-embedding-nomic-embed-text-v1.5"
    assert payload["input"] == ["alpha", "beta"]


def test_create_embedding_adapter_supports_lmstudio() -> None:
    settings = Settings(
        embedding_provider="lmstudio",
        embedding_model="text-embedding-nomic-embed-text-v1.5",
        embedding_vector_dimension=768,
        lmstudio_base_url="http://host.docker.internal:1234/v1/",
    )

    adapter = create_embedding_adapter(settings)

    assert isinstance(adapter, LMStudioEmbeddingAdapter)
    assert adapter.dimension == 768
