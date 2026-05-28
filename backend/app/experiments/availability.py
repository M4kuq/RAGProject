from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol

from app.experiments.model_registry import RegisteredModel
from app.experiments.schemas import (
    DownloadPolicy,
    ExperimentMode,
    ExperimentModelCandidate,
    ModelKind,
    ModelProvider,
)

AvailabilityStatus = Literal["available", "skipped", "blocked"]


@dataclass(frozen=True)
class ModelAvailability:
    model_id: str
    model_type: ModelKind
    provider: ModelProvider
    status: AvailabilityStatus
    reason_codes: tuple[str, ...]
    required: bool
    download_policy: DownloadPolicy
    expected_dimension: int | None
    actual_dimension: int | None = None

    def model_dump(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "model_type": self.model_type.value,
            "provider": self.provider.value,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "required": self.required,
            "download_policy": self.download_policy.value,
            "expected_dimension": self.expected_dimension,
            "actual_dimension": self.actual_dimension,
        }


class SentenceTransformersLoader(Protocol):
    def package_available(self) -> bool: ...

    def probe_embedding(self, model_id: str, *, local_files_only: bool) -> int | None: ...

    def probe_reranker(self, model_id: str, *, local_files_only: bool) -> None: ...


class DefaultSentenceTransformersLoader:
    def package_available(self) -> bool:
        try:
            importlib.import_module("sentence_transformers")
        except Exception:
            return False
        return True

    def probe_embedding(self, model_id: str, *, local_files_only: bool) -> int | None:
        module = importlib.import_module("sentence_transformers")
        model_class = module.SentenceTransformer
        model = _instantiate_model(model_class, model_id, local_files_only=local_files_only)
        dimension_getter = getattr(model, "get_sentence_embedding_dimension", None)
        if callable(dimension_getter):
            dimension = dimension_getter()
            return int(dimension) if dimension is not None else None
        return None

    def probe_reranker(self, model_id: str, *, local_files_only: bool) -> None:
        module = importlib.import_module("sentence_transformers")
        model_class = module.CrossEncoder
        _instantiate_model(model_class, model_id, local_files_only=local_files_only)


def check_model_availability(
    candidate: ExperimentModelCandidate,
    *,
    model_type: ModelKind,
    registry_entry: RegisteredModel | None,
    download_policy: DownloadPolicy,
    mode: ExperimentMode,
    loader: SentenceTransformersLoader | None = None,
) -> ModelAvailability:
    expected_dimension = candidate.expected_dimension
    if registry_entry is not None and expected_dimension is None:
        expected_dimension = registry_entry.expected_dimension
    if not candidate.enabled:
        return _result(
            candidate,
            model_type,
            "skipped",
            ("model_disabled",),
            download_policy,
            expected_dimension,
        )
    if candidate.provider != ModelProvider.SENTENCE_TRANSFORMERS:
        return _missing_result(
            candidate,
            model_type,
            "unsupported_provider",
            download_policy,
            expected_dimension,
        )
    if registry_entry is None:
        return _missing_result(
            candidate,
            model_type,
            "model_not_registered",
            download_policy,
            expected_dimension,
        )
    if download_policy == DownloadPolicy.NEVER:
        return _missing_result(
            candidate,
            model_type,
            "download_disallowed",
            download_policy,
            expected_dimension,
        )
    if mode == ExperimentMode.VALIDATE:
        return _result(
            candidate,
            model_type,
            "skipped",
            ("availability_not_checked_in_validate_mode",),
            download_policy,
            expected_dimension,
        )

    loader = loader or DefaultSentenceTransformersLoader()
    if not loader.package_available():
        return _missing_result(
            candidate,
            model_type,
            "sentence_transformers_unavailable",
            download_policy,
            expected_dimension,
        )
    if mode == ExperimentMode.DRY_RUN and download_policy == DownloadPolicy.OPT_IN_DOWNLOAD:
        return _missing_result(
            candidate,
            model_type,
            "download_not_performed_in_dry_run",
            download_policy,
            expected_dimension,
        )

    local_files_only = download_policy != DownloadPolicy.OPT_IN_DOWNLOAD
    try:
        if local_files_only:
            with offline_huggingface_env():
                actual_dimension = _probe(candidate, model_type, loader, local_files_only=True)
        else:
            actual_dimension = _probe(candidate, model_type, loader, local_files_only=False)
    except Exception:
        return _missing_result(
            candidate,
            model_type,
            "model_cache_unavailable",
            download_policy,
            expected_dimension,
        )
    if (
        model_type == ModelKind.EMBEDDING
        and expected_dimension is not None
        and actual_dimension is not None
        and expected_dimension != actual_dimension
    ):
        return _result(
            candidate,
            model_type,
            "blocked",
            ("embedding_dimension_mismatch",),
            download_policy,
            expected_dimension,
            actual_dimension,
        )
    return _result(
        candidate,
        model_type,
        "available",
        ("available",),
        download_policy,
        expected_dimension,
        actual_dimension,
    )


@contextmanager
def offline_huggingface_env() -> Iterator[None]:
    previous = {
        "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"),
        "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE"),
    }
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _probe(
    candidate: ExperimentModelCandidate,
    model_type: ModelKind,
    loader: SentenceTransformersLoader,
    *,
    local_files_only: bool,
) -> int | None:
    if model_type == ModelKind.EMBEDDING:
        return loader.probe_embedding(candidate.model_id, local_files_only=local_files_only)
    loader.probe_reranker(candidate.model_id, local_files_only=local_files_only)
    return None


def _instantiate_model(model_class: object, model_id: str, *, local_files_only: bool) -> object:
    try:
        return model_class(model_id, local_files_only=local_files_only)  # type: ignore[operator]
    except TypeError:
        if local_files_only:
            with offline_huggingface_env():
                return model_class(model_id)  # type: ignore[operator]
        return model_class(model_id)  # type: ignore[operator]


def _missing_result(
    candidate: ExperimentModelCandidate,
    model_type: ModelKind,
    reason_code: str,
    download_policy: DownloadPolicy,
    expected_dimension: int | None,
) -> ModelAvailability:
    return _result(
        candidate,
        model_type,
        "blocked" if candidate.required else "skipped",
        (reason_code,),
        download_policy,
        expected_dimension,
    )


def _result(
    candidate: ExperimentModelCandidate,
    model_type: ModelKind,
    status: AvailabilityStatus,
    reason_codes: tuple[str, ...],
    download_policy: DownloadPolicy,
    expected_dimension: int | None,
    actual_dimension: int | None = None,
) -> ModelAvailability:
    return ModelAvailability(
        model_id=candidate.model_id,
        model_type=model_type,
        provider=candidate.provider,
        status=status,
        reason_codes=reason_codes,
        required=candidate.required,
        download_policy=download_policy,
        expected_dimension=expected_dimension,
        actual_dimension=actual_dimension,
    )
