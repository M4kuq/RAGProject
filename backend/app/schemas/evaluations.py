from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RetrievalStrategy
from app.schemas.common import PaginationMeta

EvaluationStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]
EVALUATION_SCHEMA_VERSION: Literal["phase2.evaluation.v1"] = "phase2.evaluation.v1"
DATASET_MANIFEST_SCHEMA_VERSION: Literal["phase2.evaluation_dataset.v1"] = (
    "phase2.evaluation_dataset.v1"
)

_SAFE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_SAFE_FIXTURE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,119}$")
_SAFE_GENERATION_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,127}$")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|credential|token)\s*[:=]|bearer\s+|sk-[A-Za-z0-9]"
)
_GENERATION_MODEL_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|credential|bearer|sk-[A-Za-z0-9_-]{8,})"
)
_FORBIDDEN_KEY_PARTS = (
    "api_key",
    "chunk_text",
    "content_text",
    "credential",
    "full_context",
    "password",
    "pii",
    "prompt",
    "raw_chunk",
    "raw_context",
    "raw_text",
    "secret",
    "token",
)
KNOWN_GENERATION_PROVIDERS = frozenset(
    {"fake", "ollama", "lmstudio", "openai", "anthropic", "gemini"}
)


class EvaluationDatasetStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class EvaluationDatasetSourceType(StrEnum):
    MANUAL = "manual"
    FIXTURE = "fixture"
    FEEDBACK_PROMOTED = "feedback_promoted"
    IMPORTED = "imported"


class EvaluationTriggerType(StrEnum):
    MANUAL = "manual"
    CI = "ci"
    SCHEDULED = "scheduled"
    POST_DEPLOY = "post_deploy"
    ONLINE_SAMPLED_TRACE = "online_sampled_trace"


class EvaluationRunRequestStrategy(StrEnum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"
    GRAPH = "graph"
    GRAPH_POSTGRES = "graph_postgres"
    GRAPH_NEO4J = "graph_neo4j"
    AGENTIC_ROUTER = "agentic_router"
    LLM_TOOL_ORCHESTRATOR = "llm_tool_orchestrator"
    LANGCHAIN_AGENTIC = "langchain_agentic"
    LANGGRAPH_AGENTIC = "langgraph_agentic"


GENERATION_COMPARISON_STRATEGIES = frozenset(
    {
        EvaluationRunRequestStrategy.LLM_TOOL_ORCHESTRATOR,
        EvaluationRunRequestStrategy.LANGCHAIN_AGENTIC,
        EvaluationRunRequestStrategy.LANGGRAPH_AGENTIC,
    }
)


class EvaluationCacheMode(StrEnum):
    DEFAULT = "default"
    DISABLED = "disabled"
    COLD = "cold"
    WARM = "warm"


class EvaluationMetricName(StrEnum):
    RECALL_AT_K = "recall_at_k"
    MRR = "mrr"
    CITATION_COVERAGE = "citation_coverage"
    CITATION_PRESENCE = "citation_presence"
    CITATION_CORRECTNESS = "citation_correctness"
    GROUNDEDNESS = "groundedness"
    FAITHFULNESS = "faithfulness"
    ANSWER_COMPLETENESS = "answer_completeness"
    NO_CONTEXT_RATE = "no_context_rate"
    P95_LATENCY = "p95_latency"
    STRATEGY_SELECTION_ACCURACY = "strategy_selection_accuracy"
    FALLBACK_RATE = "fallback_rate"
    BUDGET_EXHAUSTED_RATE = "budget_exhausted_rate"
    SUFFICIENCY_SCORE_AVG = "sufficiency_score_avg"
    RETRIEVAL_CALL_COUNT_AVG = "retrieval_call_count_avg"
    CONTEXT_PRECISION = "context_precision"
    GRAPH_PATH_RELEVANCE = "graph_path_relevance"
    GRAPH_CITATION_COVERAGE = "graph_citation_coverage"
    MULTI_HOP_ANSWERABILITY = "multi_hop_answerability"
    CACHE_HIT_RATE = "cache_hit_rate"
    CACHE_SAVED_LATENCY = "cache_saved_latency"
    ENTITY_RELATION_QUALITY_SUMMARY = "entity_relation_quality_summary"


DEFAULT_EVALUATION_RUN_REQUEST_STRATEGY = EvaluationRunRequestStrategy.DENSE
PR25_ALLOWED_STRATEGIES = set(EvaluationRunRequestStrategy)

DEFAULT_EVALUATION_METRICS: tuple[EvaluationMetricName, ...] = (
    EvaluationMetricName.RECALL_AT_K,
    EvaluationMetricName.MRR,
    EvaluationMetricName.CITATION_COVERAGE,
    EvaluationMetricName.CITATION_PRESENCE,
    EvaluationMetricName.CITATION_CORRECTNESS,
    EvaluationMetricName.GROUNDEDNESS,
    EvaluationMetricName.FAITHFULNESS,
    EvaluationMetricName.ANSWER_COMPLETENESS,
    EvaluationMetricName.NO_CONTEXT_RATE,
    EvaluationMetricName.P95_LATENCY,
    EvaluationMetricName.STRATEGY_SELECTION_ACCURACY,
    EvaluationMetricName.FALLBACK_RATE,
    EvaluationMetricName.BUDGET_EXHAUSTED_RATE,
    EvaluationMetricName.SUFFICIENCY_SCORE_AVG,
    EvaluationMetricName.RETRIEVAL_CALL_COUNT_AVG,
    EvaluationMetricName.GRAPH_PATH_RELEVANCE,
    EvaluationMetricName.GRAPH_CITATION_COVERAGE,
    EvaluationMetricName.MULTI_HOP_ANSWERABILITY,
    EvaluationMetricName.CACHE_HIT_RATE,
    EvaluationMetricName.CACHE_SAVED_LATENCY,
    EvaluationMetricName.ENTITY_RELATION_QUALITY_SUMMARY,
)


class MetricSpec(BaseModel):
    schema_version: Literal["phase2.evaluation.v1"] = EVALUATION_SCHEMA_VERSION
    metric_name: EvaluationMetricName
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    higher_is_better: bool = True
    value_unit: Literal["ratio", "ms", "count"] = "ratio"
    min_value: float | None = None
    max_value: float | None = None


class MetricValue(BaseModel):
    schema_version: Literal["phase2.evaluation.v1"] = EVALUATION_SCHEMA_VERSION
    metric_name: EvaluationMetricName | str
    metric_value: float | None = None
    metric_label: str | None = Field(default=None, max_length=100)
    metric_detail_json: dict[str, Any] | None = None
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY

    @field_validator("metric_detail_json")
    @classmethod
    def validate_metric_detail(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        _assert_safe_json(value)
        return value


class MetricSummary(BaseModel):
    schema_version: Literal["phase2.evaluation.v1"] = EVALUATION_SCHEMA_VERSION
    metrics: dict[str, float] = Field(default_factory=dict)


class StrategyMetricSummary(BaseModel):
    schema_version: Literal["phase2.evaluation.v1"] = EVALUATION_SCHEMA_VERSION
    strategy_type: RetrievalStrategy
    metric_summary: dict[str, float] = Field(default_factory=dict)
    case_count: int = Field(default=0, ge=0)
    succeeded_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)


class StrategyComparisonMetric(BaseModel):
    schema_version: Literal["phase2.evaluation.v1"] = EVALUATION_SCHEMA_VERSION
    strategy_type: str
    metric_name: EvaluationMetricName | str
    average: float | None = None
    p50: float | None = None
    p95: float | None = None
    count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    not_applicable_count: int = Field(default=0, ge=0)
    comparison_label: str | None = Field(default=None, max_length=120)
    retrieval_strategy: RetrievalStrategy | None = None
    graph_store_provider: str | None = Field(default=None, max_length=50)
    cache_mode: EvaluationCacheMode | None = None


class EvaluationCaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=8000)
    expected_answer: str | None = Field(default=None, max_length=8000)
    expected_keywords: list[str] = Field(default_factory=list, max_length=50)
    expected_document_ids: list[int] = Field(default_factory=list, max_length=100)
    expected_chunk_ids: list[int] = Field(default_factory=list, max_length=100)
    required_citation: bool = True
    tags: list[str] = Field(default_factory=list, max_length=50)
    metadata_json: dict[str, Any] | None = None
    status: EvaluationDatasetStatus = EvaluationDatasetStatus.ACTIVE

    @field_validator("case_key")
    @classmethod
    def validate_case_key(cls, value: str) -> str:
        return _safe_key(value, field_name="case_key")

    @field_validator("question", "expected_answer")
    @classmethod
    def validate_safe_text(cls, value: str | None) -> str | None:
        return _safe_text(value)

    @field_validator("expected_keywords", "tags")
    @classmethod
    def validate_safe_string_list(cls, value: list[str]) -> list[str]:
        return [_safe_text(item, max_length=100) or "" for item in value]

    @field_validator("expected_document_ids", "expected_chunk_ids")
    @classmethod
    def validate_positive_ids(cls, value: list[int]) -> list[int]:
        if any(item < 1 for item in value):
            raise ValueError("ids must be positive integers")
        return value

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        _assert_safe_json(value)
        return value

    @model_validator(mode="after")
    def validate_expected_signal(self) -> EvaluationCaseSpec:
        if not self.expected_keywords and not self.expected_answer:
            raise ValueError("expected_keywords or expected_answer is required")
        return self


class EvaluationDatasetManifestInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    version: str = Field(default="v1", min_length=1, max_length=50)
    source_type: EvaluationDatasetSourceType = EvaluationDatasetSourceType.IMPORTED
    status: EvaluationDatasetStatus = EvaluationDatasetStatus.ACTIVE
    metadata_json: dict[str, Any] | None = None

    @field_validator("dataset_name")
    @classmethod
    def validate_dataset_name(cls, value: str) -> str:
        return _safe_key(value, field_name="dataset_name")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        return _safe_text(value, max_length=1000)

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        _assert_safe_json(value)
        return value


class EvaluationDatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["phase2.evaluation_dataset.v1"] = DATASET_MANIFEST_SCHEMA_VERSION
    dataset: EvaluationDatasetManifestInfo
    cases: list[EvaluationCaseSpec] = Field(min_length=1, max_length=500)
    metric_specs: list[MetricSpec] = Field(default_factory=list)


class EvaluationDatasetCreateRequest(EvaluationDatasetManifestInfo):
    source_type: EvaluationDatasetSourceType = EvaluationDatasetSourceType.MANUAL


class EvaluationDatasetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=1000)
    version: str | None = Field(default=None, min_length=1, max_length=50)
    metadata_json: dict[str, Any] | None = None

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        return _safe_text(value, max_length=1000)

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        _assert_safe_json(value)
        return value


class EvaluationCaseCreateRequest(EvaluationCaseSpec):
    pass


class EvaluationCaseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(default=None, min_length=1, max_length=8000)
    expected_answer: str | None = Field(default=None, max_length=8000)
    expected_keywords: list[str] | None = Field(default=None, max_length=50)
    expected_document_ids: list[int] | None = Field(default=None, max_length=100)
    expected_chunk_ids: list[int] | None = Field(default=None, max_length=100)
    required_citation: bool | None = None
    tags: list[str] | None = Field(default=None, max_length=50)
    metadata_json: dict[str, Any] | None = None

    @field_validator("question", "expected_answer")
    @classmethod
    def validate_safe_text(cls, value: str | None) -> str | None:
        return _safe_text(value)

    @field_validator("expected_keywords", "tags")
    @classmethod
    def validate_safe_string_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [_safe_text(item, max_length=100) or "" for item in value]

    @field_validator("expected_document_ids", "expected_chunk_ids")
    @classmethod
    def validate_positive_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(item < 1 for item in value):
            raise ValueError("ids must be positive integers")
        return value

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        _assert_safe_json(value)
        return value


class EvaluationDatasetResponse(BaseModel):
    evaluation_dataset_id: int
    dataset_name: str
    description: str | None = None
    version: str
    source_type: EvaluationDatasetSourceType
    status: EvaluationDatasetStatus
    metadata_json: dict[str, Any] | None = None
    case_count: int = 0
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime


class EvaluationCaseResponse(BaseModel):
    evaluation_case_id: int
    evaluation_dataset_id: int
    case_key: str
    question: str
    expected_answer: str | None = None
    expected_keywords: list[str] = Field(default_factory=list)
    expected_document_ids: list[int] = Field(default_factory=list)
    expected_chunk_ids: list[int] = Field(default_factory=list)
    required_citation: bool
    tags: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] | None = None
    status: EvaluationDatasetStatus
    created_at: datetime
    updated_at: datetime


class EvaluationDatasetImportResponse(BaseModel):
    evaluation_dataset_id: int
    dataset_name: str
    case_count: int
    imported_case_count: int
    result_code: Literal["created", "updated"]


class PagedEvaluationDatasets(BaseModel):
    items: list[EvaluationDatasetResponse]
    pagination: PaginationMeta | None = None


class PagedEvaluationCases(BaseModel):
    items: list[EvaluationCaseResponse]
    pagination: PaginationMeta | None = None


class EvaluationRunCreateRequest(BaseModel):
    dataset_name: str = Field(default="phase1_smoke", min_length=1, max_length=120)
    evaluation_dataset_id: int | None = Field(default=None, ge=1)
    case_limit: int | None = Field(default=10, ge=1, le=50)
    strategy_type: EvaluationRunRequestStrategy = DEFAULT_EVALUATION_RUN_REQUEST_STRATEGY
    strategies: list[EvaluationRunRequestStrategy] | None = Field(
        default=None, min_length=1, max_length=10
    )
    metrics: list[EvaluationMetricName] = Field(
        default_factory=lambda: list(DEFAULT_EVALUATION_METRICS),
        min_length=1,
        max_length=20,
    )
    cache_modes: list[EvaluationCacheMode] | None = Field(
        default=None,
        min_length=1,
        max_length=4,
    )
    top_k: int | None = Field(default=None, ge=1, le=20)
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)
    generation_provider: str | None = Field(default=None, min_length=1, max_length=50)
    generation_model: str | None = Field(default=None, min_length=1, max_length=128)
    trigger_type: EvaluationTriggerType = EvaluationTriggerType.MANUAL

    @field_validator("dataset_name")
    @classmethod
    def validate_dataset_name(cls, value: str) -> str:
        return _safe_key(value, field_name="dataset_name")

    @field_validator("generation_provider")
    @classmethod
    def validate_generation_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        provider = _safe_key(value, field_name="generation_provider")
        if provider not in KNOWN_GENERATION_PROVIDERS:
            raise ValueError("generation_provider is not supported")
        return provider

    @field_validator("generation_model")
    @classmethod
    def validate_generation_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        model = _safe_text(value, max_length=128)
        if model is None:
            return None
        if not _SAFE_GENERATION_MODEL_RE.fullmatch(model):
            raise ValueError(
                "generation_model must use letters, digits, dot, dash, underscore, "
                "slash, colon, at or plus"
            )
        if _GENERATION_MODEL_SECRET_RE.search(model):
            raise ValueError("generation_model must not contain secret-like text")
        if model.lower() in {"redacted", "unknown"}:
            raise ValueError("generation_model must not use a reserved label")
        return model

    @model_validator(mode="after")
    def validate_request(self) -> EvaluationRunCreateRequest:
        if self.evaluation_dataset_id is None and not _SAFE_FIXTURE_KEY_RE.fullmatch(
            self.dataset_name
        ):
            raise ValueError(
                "dataset_name must use lowercase letters, digits, underscore or hyphen"
            )
        selected_strategies = self.strategies or [self.strategy_type]
        deduped: list[EvaluationRunRequestStrategy] = []
        for strategy in selected_strategies:
            if strategy not in PR25_ALLOWED_STRATEGIES:
                raise ValueError("strategy is not enabled for PR-25 evaluation runner")
            if strategy not in deduped:
                deduped.append(strategy)
        if self.generation_provider is not None and self.generation_model is None:
            raise ValueError("generation_model is required when generation_provider is set")
        if (self.generation_provider is not None or self.generation_model is not None) and not any(
            strategy in GENERATION_COMPARISON_STRATEGIES for strategy in deduped
        ):
            raise ValueError(
                "generation selection requires an answer-generating evaluation strategy"
            )
        self.strategies = deduped
        self.strategy_type = deduped[0]
        self.metrics = list(dict.fromkeys(self.metrics))
        if self.cache_modes:
            deduped_cache_modes = list(dict.fromkeys(self.cache_modes))
            if (
                EvaluationCacheMode.WARM in deduped_cache_modes
                and EvaluationCacheMode.COLD not in deduped_cache_modes
            ):
                deduped_cache_modes.append(EvaluationCacheMode.COLD)
            self.cache_modes = deduped_cache_modes
        return self


class EvaluationRunCreateResponse(BaseModel):
    evaluation_run_id: int
    job_id: int
    status: Literal["queued"]
    strategies: list[str] = Field(default_factory=list)


class EvaluationMetricResult(BaseModel):
    metric_name: str
    metric_score: float | None = None
    metric_value: float | None = None
    metric_label: str | None = None
    details: dict[str, object] | None = None
    metric_detail_json: dict[str, object] | None = None
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY


class EvaluationRunItemResponse(BaseModel):
    evaluation_run_item_id: int
    evaluation_case_id: int | None = None
    retrieval_run_id: int | None = None
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    status: EvaluationStatus
    faithfulness_score: float | None = None
    groundedness_score: float | None = None
    citation_coverage: float | None = None
    context_precision: float | None = None
    latency_ms: int | None = None
    generation_provider: str | None = None
    generation_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    generation_latency_ms: int | None = None
    latency_breakdown_json: dict[str, object] | None = None
    metric_summary_json: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    case_id: str | None = None
    case_key: str | None = None
    metrics: list[EvaluationMetricResult] = Field(default_factory=list)


class EvaluationRunSummary(BaseModel):
    evaluation_run_id: int
    job_id: int | None = None
    evaluation_dataset_id: int | None = None
    dataset_name: str
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    strategies: list[str] = Field(default_factory=lambda: [DEFAULT_RETRIEVAL_STRATEGY.value])
    metric_names: list[str] = Field(default_factory=list)
    trigger_type: EvaluationTriggerType = EvaluationTriggerType.MANUAL
    status: EvaluationStatus
    case_count: int
    succeeded_count: int
    failed_count: int
    metric_summary: dict[str, float]
    strategy_comparison: list[StrategyComparisonMetric] = Field(default_factory=list)
    strategy_metrics_summary_json: dict[str, object] | None = None
    total_estimated_cost_usd: float | None = None
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_tokens: int | None = None
    avg_generation_latency_ms: float | None = None
    generation_providers: list[str] = Field(default_factory=list)
    generation_models: list[str] = Field(default_factory=list)
    requested_generation_provider: str | None = None
    requested_generation_model: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EvaluationFailureSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvaluationFailureCandidate(BaseModel):
    schema_version: Literal["phase2.evaluation.v1"] = EVALUATION_SCHEMA_VERSION
    evaluation_run_id: int
    evaluation_run_item_id: int
    evaluation_case_id: int | None = None
    case_key: str | None = None
    question_hash: str
    strategy_type: RetrievalStrategy
    failure_type: str = Field(min_length=1, max_length=80)
    severity: EvaluationFailureSeverity
    failure_reason_codes: list[str] = Field(default_factory=list, max_length=20)
    metric_snapshot: dict[str, object] = Field(default_factory=dict)
    recommended_tags: list[str] = Field(default_factory=list, max_length=20)
    promotion_key: str = Field(min_length=1, max_length=64)


class EvaluationFailureCandidatesResponse(BaseModel):
    evaluation_run_id: int
    candidates: list[EvaluationFailureCandidate] = Field(default_factory=list)


class EvaluationFailurePromotionRequest(BaseModel):
    target_dataset_id: int = Field(ge=1)
    failure_types: list[str] | None = Field(default=None, min_length=1, max_length=20)
    promotion_keys: list[str] | None = Field(default=None, min_length=1, max_length=100)
    min_severity: EvaluationFailureSeverity = EvaluationFailureSeverity.MEDIUM
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("failure_types")
    @classmethod
    def validate_failure_types(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        deduped: list[str] = []
        for item in value:
            safe = _safe_key(item, field_name="failure_type")
            if safe not in deduped:
                deduped.append(safe)
        return deduped

    @field_validator("promotion_keys")
    @classmethod
    def validate_promotion_keys(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        deduped: list[str] = []
        for item in value:
            safe = _safe_key(item, field_name="promotion_key")
            if safe not in deduped:
                deduped.append(safe)
        return deduped


class EvaluationFailurePromotionItem(BaseModel):
    promotion_key: str
    failure_type: str
    strategy_type: RetrievalStrategy
    evaluation_run_item_id: int
    evaluation_case_id: int | None = None
    promoted_case_id: int | None = None
    case_key: str | None = None
    result_code: Literal[
        "created",
        "already_exists",
        "source_case_missing",
        "source_case_changed",
    ]


class EvaluationFailurePromotionResponse(BaseModel):
    evaluation_run_id: int
    target_dataset_id: int
    created_count: int
    skipped_count: int
    items: list[EvaluationFailurePromotionItem] = Field(default_factory=list)


class EvaluationRunDetail(EvaluationRunSummary):
    items: list[EvaluationRunItemResponse] = Field(default_factory=list)
    failure_candidates: list[EvaluationFailureCandidate] = Field(default_factory=list)


EvaluationComparisonDirection = Literal[
    "improved",
    "regressed",
    "unchanged",
    "not_applicable",
]
EvaluationCaseTransition = Literal[
    "improved",
    "regressed",
    "unchanged",
    "added",
    "removed",
]


class EvaluationMetricComparison(BaseModel):
    metric_name: EvaluationMetricName | str
    base_score: float | None = None
    candidate_score: float | None = None
    delta: float | None = None
    direction: EvaluationComparisonDirection
    lower_is_better: bool = False


class EvaluationGenerationComparison(BaseModel):
    base_estimated_cost_usd: float | None = None
    candidate_estimated_cost_usd: float | None = None
    cost_delta: float | None = None
    cost_direction: EvaluationComparisonDirection
    cost_lower_is_better: bool = True
    base_total_tokens: int | None = None
    candidate_total_tokens: int | None = None
    tokens_delta: int | None = None
    tokens_direction: EvaluationComparisonDirection
    tokens_lower_is_better: bool = True
    base_avg_generation_latency_ms: float | None = None
    candidate_avg_generation_latency_ms: float | None = None
    latency_delta: float | None = None
    latency_direction: EvaluationComparisonDirection
    latency_lower_is_better: bool = True
    base_providers: list[str] = Field(default_factory=list)
    base_models: list[str] = Field(default_factory=list)
    candidate_providers: list[str] = Field(default_factory=list)
    candidate_models: list[str] = Field(default_factory=list)


class EvaluationCaseComparison(BaseModel):
    case_id: str = Field(min_length=1, max_length=200)
    question_hash: str | None = Field(default=None, min_length=64, max_length=64)
    case_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)
    comparison_label: str | None = Field(default=None, max_length=120)
    base_status: EvaluationStatus | None = None
    candidate_status: EvaluationStatus | None = None
    transition: EvaluationCaseTransition
    metric_deltas: dict[str, float | None] = Field(default_factory=dict)


class EvaluationRunComparisonSummary(BaseModel):
    improved_metric_count: int = Field(default=0, ge=0)
    regressed_metric_count: int = Field(default=0, ge=0)
    unchanged_metric_count: int = Field(default=0, ge=0)
    regressed_case_count: int = Field(default=0, ge=0)
    improved_case_count: int = Field(default=0, ge=0)
    common_case_count: int = Field(default=0, ge=0)
    base_only_case_count: int = Field(default=0, ge=0)
    candidate_only_case_count: int = Field(default=0, ge=0)


class EvaluationRunComparison(BaseModel):
    base_run: EvaluationRunSummary
    candidate_run: EvaluationRunSummary
    generation: EvaluationGenerationComparison
    metrics: list[EvaluationMetricComparison] = Field(default_factory=list)
    cases: list[EvaluationCaseComparison] = Field(default_factory=list)
    summary: EvaluationRunComparisonSummary


class EvaluationStrategyComparisonResponse(BaseModel):
    evaluation_run_id: int
    strategies: list[str]
    metrics: list[StrategyComparisonMetric]


class PagedEvaluationRuns(BaseModel):
    items: list[EvaluationRunSummary]
    pagination: PaginationMeta | None = None


def _safe_key(value: str, *, field_name: str) -> str:
    text = " ".join(value.replace("\x00", " ").split()).lower()
    if not _SAFE_KEY_RE.fullmatch(text):
        raise ValueError(
            f"{field_name} must use lowercase letters, digits, dot, underscore or hyphen"
        )
    if _SECRET_VALUE_RE.search(text):
        raise ValueError(f"{field_name} must not contain secret-like text")
    return text


def _safe_text(value: str | None, *, max_length: int = 8000) -> str | None:
    if value is None:
        return None
    text = " ".join(value.replace("\x00", " ").split())
    if not text:
        raise ValueError("text must not be empty")
    if len(text) > max_length:
        raise ValueError("text is too long")
    if _EMAIL_RE.search(text) or _SECRET_VALUE_RE.search(text):
        raise ValueError("text must not contain PII or secret-like values")
    return text


def _assert_safe_json(value: Any) -> None:
    if value is None:
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                raise ValueError(f"metadata field is not allowed: {key}")
            _assert_safe_json(nested)
        return
    if isinstance(value, list):
        for nested in value:
            _assert_safe_json(nested)
        return
    if isinstance(value, str):
        _safe_text(value, max_length=2000)
