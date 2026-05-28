from __future__ import annotations

from dataclasses import dataclass

from app.experiments.schemas import DownloadPolicy, ModelKind, ModelProvider

MODEL_REGISTRY_VERSION = "phase2.model_registry.v1"


@dataclass(frozen=True)
class RegisteredModel:
    model_id: str
    provider: ModelProvider
    model_type: ModelKind
    expected_dimension: int | None
    recommended_use: str
    language_support: str
    download_policy: DownloadPolicy
    default_enabled: bool
    notes: str


MODEL_REGISTRY: dict[str, RegisteredModel] = {
    "sentence-transformers/all-MiniLM-L6-v2": RegisteredModel(
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        provider=ModelProvider.SENTENCE_TRANSFORMERS,
        model_type=ModelKind.EMBEDDING,
        expected_dimension=384,
        recommended_use="small local retrieval smoke and quick comparison baseline",
        language_support="English and code-like queries",
        download_policy=DownloadPolicy.IF_CACHED,
        default_enabled=True,
        notes="Small model used by the optional PR-31 local smoke cache path.",
    ),
    "BAAI/bge-small-en-v1.5": RegisteredModel(
        model_id="BAAI/bge-small-en-v1.5",
        provider=ModelProvider.SENTENCE_TRANSFORMERS,
        model_type=ModelKind.EMBEDDING,
        expected_dimension=384,
        recommended_use="English keyword and semantic retrieval comparison",
        language_support="English",
        download_policy=DownloadPolicy.IF_CACHED,
        default_enabled=False,
        notes="Optional local comparison candidate.",
    ),
    "BAAI/bge-m3": RegisteredModel(
        model_id="BAAI/bge-m3",
        provider=ModelProvider.SENTENCE_TRANSFORMERS,
        model_type=ModelKind.EMBEDDING,
        expected_dimension=1024,
        recommended_use="multilingual retrieval comparison",
        language_support="Multilingual",
        download_policy=DownloadPolicy.IF_CACHED,
        default_enabled=False,
        notes="Heavier optional model; never required by normal CI.",
    ),
    "cross-encoder/ms-marco-MiniLM-L6-v2": RegisteredModel(
        model_id="cross-encoder/ms-marco-MiniLM-L6-v2",
        provider=ModelProvider.SENTENCE_TRANSFORMERS,
        model_type=ModelKind.RERANKER,
        expected_dimension=None,
        recommended_use="small local reranker comparison baseline",
        language_support="English",
        download_policy=DownloadPolicy.IF_CACHED,
        default_enabled=True,
        notes="Optional local reranker candidate.",
    ),
    "BAAI/bge-reranker-base": RegisteredModel(
        model_id="BAAI/bge-reranker-base",
        provider=ModelProvider.SENTENCE_TRANSFORMERS,
        model_type=ModelKind.RERANKER,
        expected_dimension=None,
        recommended_use="reranker quality comparison",
        language_support="English and multilingual-ish retrieval tasks",
        download_policy=DownloadPolicy.IF_CACHED,
        default_enabled=False,
        notes="Heavier optional reranker candidate.",
    ),
    "BAAI/bge-reranker-v2-m3": RegisteredModel(
        model_id="BAAI/bge-reranker-v2-m3",
        provider=ModelProvider.SENTENCE_TRANSFORMERS,
        model_type=ModelKind.RERANKER,
        expected_dimension=None,
        recommended_use="multilingual reranker comparison",
        language_support="Multilingual",
        download_policy=DownloadPolicy.IF_CACHED,
        default_enabled=False,
        notes="Heavier optional model; never required by normal CI.",
    ),
}


def lookup_model(model_id: str, model_type: ModelKind) -> RegisteredModel | None:
    model = MODEL_REGISTRY.get(model_id)
    if model is None or model.model_type != model_type:
        return None
    return model


def registry_as_artifact() -> list[dict[str, object]]:
    return [
        {
            "model_id": model.model_id,
            "provider": model.provider.value,
            "model_type": model.model_type.value,
            "expected_dimension": model.expected_dimension,
            "recommended_use": model.recommended_use,
            "language_support": model.language_support,
            "download_policy": model.download_policy.value,
            "default_enabled": model.default_enabled,
        }
        for model in sorted(MODEL_REGISTRY.values(), key=lambda item: item.model_id)
    ]
