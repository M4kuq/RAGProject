from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.evaluations import EvaluationMetricName

MANIFEST_SCHEMA_VERSION: Literal["phase2.experiment.v1"] = "phase2.experiment.v1"
EXPERIMENT_RESULT_SCHEMA_VERSION: Literal["phase2.st_experiment_result.v1"] = (
    "phase2.st_experiment_result.v1"
)

ALLOWED_EXPERIMENT_STRATEGIES = {"dense", "sparse", "hybrid", "agentic_router"}
DEFAULT_EXPERIMENT_METRICS = (
    "recall_at_k",
    "mrr",
    "citation_coverage",
    "no_context_rate",
    "p95_latency",
)
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,180}$")
_SECRET_LIKE_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|credential|token|cookie|csrf|bearer|sk-)"
)


class ExperimentMode(StrEnum):
    VALIDATE = "validate"
    DRY_RUN = "dry-run"
    LOCAL = "local"


class DownloadPolicy(StrEnum):
    NEVER = "never"
    IF_CACHED = "if-cached"
    OPT_IN_DOWNLOAD = "opt-in-download"


class ModelKind(StrEnum):
    EMBEDDING = "embedding"
    RERANKER = "reranker"


class ModelProvider(StrEnum):
    SENTENCE_TRANSFORMERS = "sentence_transformers"


class ExperimentModelCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=2, max_length=180)
    provider: ModelProvider = ModelProvider.SENTENCE_TRANSFORMERS
    enabled: bool = True
    required: bool = False
    expected_dimension: int | None = Field(default=None, ge=1, le=10000)
    download_policy: DownloadPolicy | None = None
    notes: str | None = Field(default=None, max_length=240)

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        model_id = value.strip()
        if not _MODEL_ID_RE.match(model_id):
            raise ValueError("model_id must be a public Hugging Face-style model id")
        if ".." in model_id or "\\" in model_id or ":" in model_id or model_id.startswith("/"):
            raise ValueError("model_id must not be a path, URL, or traversal string")
        if _SECRET_LIKE_RE.search(model_id):
            raise ValueError("model_id must not contain secret-like tokens")
        return model_id


class ExperimentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["phase2.experiment.v1"] = MANIFEST_SCHEMA_VERSION
    experiment_name: str = Field(min_length=1, max_length=120)
    dataset: str = Field(min_length=1, max_length=120)
    case_limit: int = Field(default=20, ge=1, le=200)
    strategies: list[str] = Field(default_factory=lambda: ["dense", "hybrid"])
    embedding_models: list[ExperimentModelCandidate] = Field(min_length=1)
    reranker_models: list[ExperimentModelCandidate] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=lambda: list(DEFAULT_EXPERIMENT_METRICS))
    mode: Literal["local_opt_in"] = "local_opt_in"

    @field_validator("experiment_name", "dataset")
    @classmethod
    def validate_safe_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        if _SECRET_LIKE_RE.search(cleaned):
            raise ValueError("value must not contain secret-like tokens")
        return cleaned

    @field_validator("strategies")
    @classmethod
    def validate_strategies(cls, value: list[str]) -> list[str]:
        deduped: list[str] = []
        for raw in value:
            strategy = raw.strip()
            if strategy not in ALLOWED_EXPERIMENT_STRATEGIES:
                raise ValueError(f"unsupported strategy: {strategy}")
            if strategy not in deduped:
                deduped.append(strategy)
        if not deduped:
            raise ValueError("at least one strategy is required")
        return deduped

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: list[str]) -> list[str]:
        allowed = {metric.value for metric in EvaluationMetricName}
        deduped: list[str] = []
        for raw in value:
            metric = raw.strip()
            if metric not in allowed:
                raise ValueError(f"unsupported metric: {metric}")
            if metric not in deduped:
                deduped.append(metric)
        if not deduped:
            raise ValueError("at least one metric is required")
        return deduped

    @model_validator(mode="after")
    def validate_enabled_models(self) -> Self:
        if not any(candidate.enabled for candidate in self.embedding_models):
            raise ValueError("at least one enabled embedding model is required")
        return self
