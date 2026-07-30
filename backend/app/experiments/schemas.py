from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.evaluations import EvaluationMetricName

MANIFEST_SCHEMA_VERSION: Literal["phase2.experiment.v1"] = "phase2.experiment.v1"
MANIFEST_SCHEMA_VERSION_V2: Literal["phase2.experiment.v2"] = "phase2.experiment.v2"
EXPERIMENT_RESULT_SCHEMA_VERSION: Literal["phase2.st_experiment_result.v1"] = (
    "phase2.st_experiment_result.v1"
)

ALLOWED_EXPERIMENT_STRATEGIES = {
    "dense",
    "sparse",
    "hybrid",
    "agentic_router",
    "graph_postgres",
}
MAX_EXPERIMENT_CASE_LIMIT = 50
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
    LMSTUDIO = "lmstudio"


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


class GenerationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["lmstudio"] = "lmstudio"
    model: Literal["qwen/qwen3.5-9b"] = "qwen/qwen3.5-9b"
    planner_model: Literal["qwen/qwen3.5-9b"] = "qwen/qwen3.5-9b"
    judge_model: Literal["qwen/qwen3.5-9b"] = "qwen/qwen3.5-9b"
    temperature: float = Field(default=0.0, ge=0.0, le=0.0)
    retry_on_insufficient_evidence: bool | None = None
    max_output_chars: int | None = Field(default=None, ge=512, le=20_000)


class ExperimentRetrievalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=2, max_length=40)
    embedding_model: str = Field(min_length=2, max_length=180)
    reranker_model: str | None = Field(default=None, min_length=2, max_length=180)
    strategy: str
    router_mode: Literal["rule_based", "llm"] | None = None
    supplemental: bool = False
    top_k: int = Field(default=10, ge=1, le=20)
    rerank_top_n: int = Field(default=3, ge=1, le=20)
    dense_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    sparse_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    agentic_sufficiency_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    graph_depth: int = Field(default=2, ge=1, le=4)
    graph_router_signal_threshold: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        profile_id = value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,39}", profile_id):
            raise ValueError("profile_id is invalid")
        return profile_id

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, value: str) -> str:
        strategy = value.strip()
        if strategy not in ALLOWED_EXPERIMENT_STRATEGIES:
            raise ValueError(f"unsupported strategy: {strategy}")
        return strategy

    @model_validator(mode="after")
    def validate_weights(self) -> Self:
        if abs((self.dense_weight + self.sparse_weight) - 1.0) > 1e-9:
            raise ValueError("dense and sparse weights must sum to 1")
        return self


class ExperimentTuningGrid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_profiles: int = Field(default=15, ge=1, le=15)
    top_k: list[int] = Field(default_factory=lambda: [10, 20], min_length=1)
    rerank_top_n: list[int] = Field(default_factory=lambda: [3, 5], min_length=1)
    dense_sparse_weights: list[tuple[float, float]] = Field(
        default_factory=lambda: [(0.4, 0.6), (0.5, 0.5), (0.6, 0.4)],
        min_length=1,
    )
    rrf_k: list[int] = Field(default_factory=lambda: [30, 60], min_length=1)
    agentic_sufficiency_threshold: list[float] = Field(
        default_factory=lambda: [0.15, 0.2, 0.3], min_length=1
    )
    graph_depth: list[int] = Field(default_factory=lambda: [2, 3], min_length=1)
    graph_router_signal_threshold: list[float] = Field(
        default_factory=lambda: [0.3, 0.5], min_length=1
    )

    @model_validator(mode="after")
    def validate_grid(self) -> Self:
        if any(value not in {10, 20} for value in self.top_k):
            raise ValueError("top_k values must be 10 or 20")
        if any(value not in {3, 5} for value in self.rerank_top_n):
            raise ValueError("rerank_top_n values must be 3 or 5")
        if any(value not in {30, 60} for value in self.rrf_k):
            raise ValueError("rrf_k values must be 30 or 60")
        for dense, sparse in self.dense_sparse_weights:
            if dense not in {0.4, 0.5, 0.6} or sparse not in {0.4, 0.5, 0.6}:
                raise ValueError("dense/sparse weights are outside the approved grid")
            if abs((dense + sparse) - 1.0) > 1e-9:
                raise ValueError("dense/sparse weights must sum to 1")
        return self


class ExperimentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["phase2.experiment.v1", "phase2.experiment.v2"] = (
        MANIFEST_SCHEMA_VERSION
    )
    experiment_name: str = Field(min_length=1, max_length=120)
    dataset: str = Field(min_length=1, max_length=120)
    case_limit: int = Field(default=20, ge=1, le=MAX_EXPERIMENT_CASE_LIMIT)
    strategies: list[str] = Field(default_factory=lambda: ["dense", "hybrid"])
    embedding_models: list[ExperimentModelCandidate] = Field(min_length=1)
    reranker_models: list[ExperimentModelCandidate] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=lambda: list(DEFAULT_EXPERIMENT_METRICS))
    mode: Literal["local_opt_in"] = "local_opt_in"
    evaluation_backend: Literal["deterministic_db", "runtime_qdrant"] = "deterministic_db"
    evaluation_scope: Literal["retrieval", "end_to_end"] = "retrieval"
    repeats: int = Field(default=1, ge=1, le=3)
    generation_profile: GenerationProfile | None = None
    retrieval_profiles: list[ExperimentRetrievalProfile] = Field(default_factory=list)
    tuning_grid: ExperimentTuningGrid | None = None
    baseline_profile: str | None = Field(default=None, min_length=2, max_length=40)

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
        if self.schema_version == MANIFEST_SCHEMA_VERSION:
            return self
        if self.evaluation_backend != "runtime_qdrant":
            raise ValueError("v2 local accuracy experiments require runtime_qdrant")
        if self.generation_profile is None:
            raise ValueError("v2 requires generation_profile")
        if not self.retrieval_profiles:
            raise ValueError("v2 requires retrieval_profiles")
        if self.tuning_grid is None:
            raise ValueError("v2 requires tuning_grid")
        profile_ids = [profile.profile_id for profile in self.retrieval_profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("retrieval profile ids must be unique")
        if self.baseline_profile not in set(profile_ids):
            raise ValueError("baseline_profile must reference a retrieval profile")
        embedding_ids = {candidate.model_id for candidate in self.embedding_models}
        reranker_ids = {candidate.model_id for candidate in self.reranker_models}
        for profile in self.retrieval_profiles:
            if profile.embedding_model not in embedding_ids:
                raise ValueError("profile embedding_model must reference a candidate")
            if profile.reranker_model is not None and profile.reranker_model not in reranker_ids:
                raise ValueError("profile reranker_model must reference a candidate")
        return self
