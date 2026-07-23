from __future__ import annotations

import hashlib
import inspect
import math
import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Protocol, cast

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.responses import pagination_meta
from app.core.config import Settings, get_settings
from app.core.errors import (
    ConflictError,
    EvaluationCorpusNotReady,
    ResourceNotFound,
    ValidationFailed,
)
from app.core.job_utils import redact_error_message
from app.db.evaluation_models import (
    EvaluationAuxiliaryJudgment,
    EvaluationHumanCalibration,
    EvaluationResult,
)
from app.db.graph_models import GraphRetrievalPath
from app.db.models import EvaluationCase as EvaluationCaseModel
from app.db.models import (
    EvaluationDataset,
    EvaluationRun,
    EvaluationRunItem,
    RetrievalRun,
    RetrievalRunItem,
    User,
)
from app.evaluation.fixtures import (
    EvaluationCase,
    EvaluationFixtureError,
    evaluation_case_snapshot_hash,
    load_evaluation_cases,
)
from app.evaluation.gold_v2 import (
    AuxiliaryJudgeDecision,
    HumanCalibrationRecord,
    calibration_agreement,
    grounded_answer_pass,
)
from app.evaluation.metrics import (
    EvaluationMetricInputs,
    MetricValue,
    calculate_metrics,
    failure_metrics,
)
from app.evaluation.rag_service import RagEvaluationResult, create_evaluation_rag_service
from app.observability.trace_export import TraceExportService
from app.rag.generation import check_lmstudio_model_readiness
from app.rag.graph_citations import (
    GraphPathSourceLocator,
    GraphPathValidator,
    calculate_graph_citation_coverage,
)
from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RetrievalStrategy
from app.repositories.evaluation_repository import EvaluationRepository, EvaluationResultInput
from app.repositories.job_repository import JobRepository
from app.schemas.common import PaginationMeta, PaginationParams
from app.schemas.evaluation_datasets_v2 import (
    DATASET_MANIFEST_V2_SCHEMA_VERSION,
    EvaluationCorpusPrepareResponse,
    EvaluationCorpusReadinessResponse,
    EvaluationDatasetManifestInput,
    EvaluationDatasetValidationResponse,
)
from app.schemas.evaluations import (
    DATASET_MANIFEST_SCHEMA_VERSION,
    DEFAULT_EVALUATION_METRICS,
    EVALUATION_SCHEMA_VERSION,
    KNOWN_GENERATION_PROVIDERS,
    EvaluationAnswerOutcome,
    EvaluationCacheMode,
    EvaluationCaseComparison,
    EvaluationCaseCreateRequest,
    EvaluationCaseResponse,
    EvaluationCaseSpec,
    EvaluationCaseTransition,
    EvaluationCaseUpdateRequest,
    EvaluationComparisonDirection,
    EvaluationDatasetCreateRequest,
    EvaluationDatasetImportResponse,
    EvaluationDatasetManifest,
    EvaluationDatasetManifestInfo,
    EvaluationDatasetResponse,
    EvaluationDatasetUpdateRequest,
    EvaluationFailureCandidate,
    EvaluationFailureCandidatesResponse,
    EvaluationFailurePromotionItem,
    EvaluationFailurePromotionRequest,
    EvaluationFailurePromotionResponse,
    EvaluationFailureSeverity,
    EvaluationGenerationComparison,
    EvaluationGenerationReadinessRequest,
    EvaluationGenerationReadinessResponse,
    EvaluationHumanCalibrationResponse,
    EvaluationHumanCalibrationSummary,
    EvaluationHumanCalibrationTarget,
    EvaluationHumanCalibrationUpsertRequest,
    EvaluationManualDimensionDecision,
    EvaluationMetricCatalog,
    EvaluationMetricCatalogItem,
    EvaluationMetricCategory,
    EvaluationMetricComparison,
    EvaluationMetricMethod,
    EvaluationMetricName,
    EvaluationMetricResult,
    EvaluationQualityStatus,
    EvaluationRunComparison,
    EvaluationRunComparisonSummary,
    EvaluationRunCreateRequest,
    EvaluationRunCreateResponse,
    EvaluationRunDetail,
    EvaluationRunItemResponse,
    EvaluationRunRequestStrategy,
    EvaluationRunSummary,
    EvaluationScope,
    EvaluationStatus,
    EvaluationStrategyComparisonResponse,
    MetricSpec,
    StrategyComparisonMetric,
)
from app.services.audit_service import audit
from app.services.evaluation_corpus_service import EvaluationCorpusService
from app.services.evaluation_dataset_manifest_service import (
    EvaluationDatasetManifestService,
)
from app.services.evaluation_judge_service import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
    JUDGE_RUBRIC_VERSION,
    EvaluationClaimJudgeService,
)
from app.services.rag_service import _safe_generation_label

SCORE_QUANT = Decimal("0.000001")
METRIC_DELTA_EPSILON = 1e-6
LOWER_IS_BETTER_METRICS = frozenset(
    {
        "budget_exhausted_rate",
        "fallback_rate",
        "no_context_rate",
        "p95_latency",
        "retrieval_call_count_avg",
    }
)
RETRIEVAL_RUN_REQUEST_ID_MAX_LENGTH = 100
ASK_ONLY_EVALUATION_STRATEGIES = frozenset(
    {
        RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
        RetrievalStrategy.LANGCHAIN_AGENTIC,
        RetrievalStrategy.LANGGRAPH_AGENTIC,
    }
)
ASK_ONLY_EVALUATION_STRATEGY_VALUES = frozenset(
    strategy.value for strategy in ASK_ONLY_EVALUATION_STRATEGIES
)
ANSWER_GENERATION_DEPENDENT_METRICS = frozenset(
    {
        "faithfulness",
        "claim_faithfulness",
        "answer_completeness",
        "groundedness",
        "citation_coverage",
        "citation_presence",
        "citation_correctness",
    }
)
CACHEABLE_EVALUATION_STRATEGIES = frozenset(
    {
        RetrievalStrategy.DENSE,
        RetrievalStrategy.SPARSE,
        RetrievalStrategy.HYBRID,
        RetrievalStrategy.GRAPH,
    }
)
GRAPH_COMPARISON_LABELS = frozenset({"graph_postgres", "graph_neo4j"})
EVALUATION_TARGET_SCHEMA_VERSION = "phase3.evaluation_target.v1"
GRAPH_QUALITY_FAILURE_THRESHOLD = 1.0
CACHE_MODE_ORDER = {
    EvaluationCacheMode.DEFAULT: 0,
    EvaluationCacheMode.DISABLED: 1,
    EvaluationCacheMode.COLD: 2,
    EvaluationCacheMode.WARM: 3,
}
PROVIDER_SKIP_BASE_METRICS = frozenset(
    {
        "recall_at_k",
        "mrr",
        "faithfulness",
        "claim_faithfulness",
        "answer_completeness",
        "groundedness",
        "citation_coverage",
        "citation_presence",
        "citation_correctness",
        "context_precision",
        "no_context_rate",
    }
)
GRAPH_PROMOTION_STRING_HINT_KEYS = (
    "expected_entity_labels",
    "expected_relation_types",
    "expected_answer_slots",
)
GRAPH_PROMOTION_INT_HINT_KEYS = ("required_hop_count",)
GRAPH_PROMOTION_HINT_FORBIDDEN_PARTS = (
    "api_key",
    "apikey",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)
_REQUESTED_GENERATION_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,127}$")
_REQUESTED_GENERATION_MODEL_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|credential|bearer|sk-[A-Za-z0-9_-]{8,})"
)

STRATEGY_METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        metric_name="recall_at_k",
        display_name="Recall@k",
        description="Fraction of expected references retrieved in the top-k result set.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="mrr",
        display_name="MRR",
        description="Mean reciprocal rank for expected references.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="context_precision",
        display_name="Context precision",
        description=(
            "Fraction of selected retrieval contexts matching configured expected signals."
        ),
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="citation_coverage",
        display_name="Citation coverage",
        description="Backward-compatible alias of citation presence.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="citation_presence",
        display_name="Citation presence",
        description="Fraction of required answers with at least one safe citation.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="citation_correctness",
        display_name="Citation correctness",
        description=(
            "Fraction of citations matching configured gold chunk, document, "
            "keyword, or answer signals."
        ),
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="groundedness",
        display_name="Groundedness",
        description="Groundedness score derived from the local confidence heuristic.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="faithfulness",
        display_name="Expected answer signal match (legacy Faithfulness)",
        description=(
            "Legacy deterministic fraction of explicitly configured expected "
            "keywords found in generated answer text. Retrieved context is not used."
        ),
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="claim_faithfulness",
        display_name="Claim faithfulness",
        description=(
            "Per-answer fraction of factual claims supported by retrieved context, "
            "as assessed by the configured local claim-level judge."
        ),
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="answer_completeness",
        display_name="Answer completeness",
        description="Fraction of safe expected answer slots present in generated answer text.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="no_context_rate",
        display_name="No-context rate",
        description="Fraction of cases where retrieval returned no usable context.",
        higher_is_better=False,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="p95_latency",
        display_name="p95 latency",
        description="95th percentile end-to-end evaluation latency in milliseconds.",
        higher_is_better=False,
        value_unit="ms",
        min_value=0.0,
        max_value=None,
    ),
    MetricSpec(
        metric_name="strategy_selection_accuracy",
        display_name="Strategy selection accuracy",
        description="Fraction of cases where a router selected the expected strategy.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="fallback_rate",
        display_name="Fallback rate",
        description="Fraction of agentic-router cases that used a bounded fallback retrieval.",
        higher_is_better=False,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="budget_exhausted_rate",
        display_name="Budget exhausted rate",
        description="Fraction of agentic-router cases that exhausted the retrieval budget.",
        higher_is_better=False,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="sufficiency_score_avg",
        display_name="Average sufficiency score",
        description="Average bounded context-sufficiency score for agentic-router cases.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="retrieval_call_count_avg",
        display_name="Average retrieval calls",
        description="Average retrieval call count used by agentic-router cases.",
        higher_is_better=False,
        value_unit="count",
        min_value=0.0,
        max_value=None,
    ),
    MetricSpec(
        metric_name="graph_path_relevance",
        display_name="Graph path relevance",
        description="Graph path support for expected safe entity and relation hints.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="graph_citation_coverage",
        display_name="Graph citation coverage",
        description="Fraction of graph paths that resolve back to citable retrieval run items.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="multi_hop_answerability",
        display_name="Multi-hop answerability",
        description="Whether retrieved graph paths cover the required hop depth.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="cache_hit_rate",
        display_name="Cache hit rate",
        description="Fraction of evaluation retrievals served from the retrieval result cache.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="cache_saved_latency",
        display_name="Cache saved latency",
        description="Estimated milliseconds saved versus the matching cold-cache item.",
        higher_is_better=True,
        value_unit="ms",
        min_value=0.0,
        max_value=None,
    ),
    MetricSpec(
        metric_name="entity_relation_quality_summary",
        display_name="Entity/relation quality summary",
        description="Safe aggregate counts for graph entities, relations, and paths.",
        higher_is_better=True,
        value_unit="count",
        min_value=0.0,
        max_value=None,
    ),
)


EVALUATION_METRIC_CATEGORY_BY_NAME: dict[EvaluationMetricName, EvaluationMetricCategory] = {
    EvaluationMetricName.RECALL_AT_K: EvaluationMetricCategory.RETRIEVAL,
    EvaluationMetricName.MRR: EvaluationMetricCategory.RETRIEVAL,
    EvaluationMetricName.CONTEXT_PRECISION: EvaluationMetricCategory.RETRIEVAL,
    EvaluationMetricName.NO_CONTEXT_RATE: EvaluationMetricCategory.RETRIEVAL,
    EvaluationMetricName.GROUNDEDNESS: EvaluationMetricCategory.ANSWER,
    EvaluationMetricName.FAITHFULNESS: EvaluationMetricCategory.ANSWER,
    EvaluationMetricName.CLAIM_FAITHFULNESS: EvaluationMetricCategory.ANSWER,
    EvaluationMetricName.ANSWER_COMPLETENESS: EvaluationMetricCategory.ANSWER,
    EvaluationMetricName.CITATION_PRESENCE: EvaluationMetricCategory.CITATION,
    EvaluationMetricName.CITATION_CORRECTNESS: EvaluationMetricCategory.CITATION,
    EvaluationMetricName.CITATION_COVERAGE: EvaluationMetricCategory.CITATION,
    EvaluationMetricName.STRATEGY_SELECTION_ACCURACY: EvaluationMetricCategory.ROUTING,
    EvaluationMetricName.FALLBACK_RATE: EvaluationMetricCategory.ROUTING,
    EvaluationMetricName.BUDGET_EXHAUSTED_RATE: EvaluationMetricCategory.ROUTING,
    EvaluationMetricName.SUFFICIENCY_SCORE_AVG: EvaluationMetricCategory.ROUTING,
    EvaluationMetricName.RETRIEVAL_CALL_COUNT_AVG: EvaluationMetricCategory.ROUTING,
    EvaluationMetricName.GRAPH_PATH_RELEVANCE: EvaluationMetricCategory.GRAPH,
    EvaluationMetricName.GRAPH_CITATION_COVERAGE: EvaluationMetricCategory.GRAPH,
    EvaluationMetricName.MULTI_HOP_ANSWERABILITY: EvaluationMetricCategory.GRAPH,
    EvaluationMetricName.ENTITY_RELATION_QUALITY_SUMMARY: EvaluationMetricCategory.GRAPH,
    EvaluationMetricName.P95_LATENCY: EvaluationMetricCategory.PERFORMANCE,
    EvaluationMetricName.CACHE_HIT_RATE: EvaluationMetricCategory.PERFORMANCE,
    EvaluationMetricName.CACHE_SAVED_LATENCY: EvaluationMetricCategory.PERFORMANCE,
}
EVALUATION_METRIC_METHOD_BY_NAME: dict[EvaluationMetricName, EvaluationMetricMethod] = {
    EvaluationMetricName.GROUNDEDNESS: EvaluationMetricMethod.PROXY,
    EvaluationMetricName.FAITHFULNESS: EvaluationMetricMethod.PROXY,
    EvaluationMetricName.CLAIM_FAITHFULNESS: EvaluationMetricMethod.LOCAL_JUDGE,
}
EVALUATION_METRIC_ALIAS_BY_NAME: dict[EvaluationMetricName, EvaluationMetricName] = {
    EvaluationMetricName.CITATION_COVERAGE: EvaluationMetricName.CITATION_PRESENCE,
}

EVALUATION_METRIC_DISPLAY_NAME_BY_NAME: dict[EvaluationMetricName, str] = {
    EvaluationMetricName.RECALL_AT_K: "検索再現率",
    EvaluationMetricName.MRR: "平均逆順位",
    EvaluationMetricName.CONTEXT_PRECISION: "文脈適合率",
    EvaluationMetricName.CITATION_COVERAGE: "引用の有無（互換）",
    EvaluationMetricName.CITATION_PRESENCE: "引用の有無",
    EvaluationMetricName.CITATION_CORRECTNESS: "引用正確性",
    EvaluationMetricName.GROUNDEDNESS: "Groundedness\uff08\u691c\u7d22\u4fe1\u983c\u5ea6\uff09",
    EvaluationMetricName.FAITHFULNESS: (
        "\u671f\u5f85\u56de\u7b54\u30b7\u30b0\u30ca\u30eb\u4e00\u81f4\u7387"
        "\uff08\u65e7Faithfulness\uff09"
    ),
    EvaluationMetricName.CLAIM_FAITHFULNESS: (
        "Claim Faithfulness\uff08\u30ed\u30fc\u30ab\u30ebjudge\uff09"
    ),
    EvaluationMetricName.ANSWER_COMPLETENESS: "回答完全性",
    EvaluationMetricName.NO_CONTEXT_RATE: "根拠なし率",
    EvaluationMetricName.P95_LATENCY: "遅いケースの応答時間",
    EvaluationMetricName.STRATEGY_SELECTION_ACCURACY: "経路選択精度",
    EvaluationMetricName.FALLBACK_RATE: "フォールバック率",
    EvaluationMetricName.BUDGET_EXHAUSTED_RATE: "検索予算超過率",
    EvaluationMetricName.SUFFICIENCY_SCORE_AVG: "根拠充足度",
    EvaluationMetricName.RETRIEVAL_CALL_COUNT_AVG: "平均検索回数",
    EvaluationMetricName.GRAPH_PATH_RELEVANCE: "グラフ経路適合率",
    EvaluationMetricName.GRAPH_CITATION_COVERAGE: "グラフ引用対応率",
    EvaluationMetricName.MULTI_HOP_ANSWERABILITY: "複数段階回答可能性",
    EvaluationMetricName.CACHE_HIT_RATE: "キャッシュ利用率",
    EvaluationMetricName.CACHE_SAVED_LATENCY: "キャッシュ短縮時間",
    EvaluationMetricName.ENTITY_RELATION_QUALITY_SUMMARY: "エンティティ・関係品質集計",
}
EVALUATION_METRIC_PLAIN_LANGUAGE_SUMMARY_BY_NAME: dict[EvaluationMetricName, str] = {
    EvaluationMetricName.RECALL_AT_K: "必要な情報を検索結果の上位で見つけられた割合です。",
    EvaluationMetricName.MRR: "必要な情報が検索結果のどのくらい上位に出たかを示します。",
    EvaluationMetricName.CONTEXT_PRECISION: (
        "取得した情報に回答へ関係する内容がどれだけ含まれるかを示します。"
    ),
    EvaluationMetricName.CITATION_COVERAGE: "以前の結果と比較するための互換指標です。",
    EvaluationMetricName.CITATION_PRESENCE: "引用が必要な回答に引用が付いている割合です。",
    EvaluationMetricName.CITATION_CORRECTNESS: "引用先が期待する根拠と一致している割合です。",
    EvaluationMetricName.GROUNDEDNESS: "回答が取得した根拠に支えられている度合いです。",
    EvaluationMetricName.FAITHFULNESS: (
        "\u65e7\u30c7\u30fc\u30bf\u30bb\u30c3\u30c8\u5411\u3051\u306b\u3001\u56de\u7b54\u3078\u660e\u793a\u7684\u306a"
        "\u671f\u5f85\u30ad\u30fc\u30ef\u30fc\u30c9\u304c\u542b\u307e\u308c\u305f\u5272\u5408\u3092\u78ba\u8a8d\u3057\u307e\u3059\u3002"
        "\u30ad\u30fc\u30ef\u30fc\u30c9\u672a\u8a2d\u5b9a\u6642\u306fN/A\u3067\u3059\u3002"
    ),
    EvaluationMetricName.CLAIM_FAITHFULNESS: (
        "\u751f\u6210\u56de\u7b54\u3092\u691c\u8a3c\u53ef\u80fd\u306a\u4e8b\u5b9f\u306e\u307e\u3068\u307e\u308a\u306b\u5206\u3051\u3001"
        "\u691c\u7d22\u3067\u5f97\u305f\u6839\u62e0\u306b\u88cf\u4ed8\u3051\u3089\u308c\u305f\u4e3b\u5f35\u306e\u5272\u5408\u3067\u3059\u3002"
        "\u30ed\u30fc\u30ab\u30ebLLM\u306b\u3088\u308b\u81ea\u52d5\u5224\u5b9a\u306e\u305f\u3081\u66ab\u5b9a\u5024\u3067\u3059\u3002"
    ),
    EvaluationMetricName.ANSWER_COMPLETENESS: "回答に必要な内容がそろっている割合です。",
    EvaluationMetricName.NO_CONTEXT_RATE: "回答に使える情報を取得できなかったケースの割合です。",
    EvaluationMetricName.P95_LATENCY: "ほとんどの評価がこの時間以内に完了する目安です。",
    EvaluationMetricName.STRATEGY_SELECTION_ACCURACY: "質問に合った検索方法を選べた割合です。",
    EvaluationMetricName.FALLBACK_RATE: "通常経路で不足し、代替検索を使ったケースの割合です。",
    EvaluationMetricName.BUDGET_EXHAUSTED_RATE: "検索回数の上限まで使い切ったケースの割合です。",
    EvaluationMetricName.SUFFICIENCY_SCORE_AVG: "取得した情報が回答に十分だったかの平均です。",
    EvaluationMetricName.RETRIEVAL_CALL_COUNT_AVG: "1ケースで検索を呼び出した平均回数です。",
    EvaluationMetricName.GRAPH_PATH_RELEVANCE: "取得したグラフ経路が期待する関係に合う度合いです。",
    EvaluationMetricName.GRAPH_CITATION_COVERAGE: "グラフ経路を引用可能な根拠へ戻せた割合です。",
    EvaluationMetricName.MULTI_HOP_ANSWERABILITY: (
        "複数の関係をたどる質問に必要な経路を取得できた割合です。"
    ),
    EvaluationMetricName.CACHE_HIT_RATE: "過去の検索結果を再利用できた割合です。",
    EvaluationMetricName.CACHE_SAVED_LATENCY: "キャッシュで短縮できた推定時間です。",
    EvaluationMetricName.ENTITY_RELATION_QUALITY_SUMMARY: (
        "グラフのエンティティ・関係・経路を安全な集計値で確認します。"
    ),
}
EVALUATION_METRIC_PRIMARY_SCOPES_BY_NAME: dict[
    EvaluationMetricName, tuple[EvaluationScope, ...]
] = {
    EvaluationMetricName.RECALL_AT_K: ("retrieval",),
    EvaluationMetricName.MRR: ("retrieval",),
    EvaluationMetricName.CONTEXT_PRECISION: ("retrieval",),
    EvaluationMetricName.CLAIM_FAITHFULNESS: ("answer", "end_to_end"),
    EvaluationMetricName.ANSWER_COMPLETENESS: ("answer", "end_to_end"),
    EvaluationMetricName.CITATION_CORRECTNESS: ("answer", "end_to_end"),
}
EVALUATION_METRIC_DIAGNOSTIC_NAMES = frozenset(
    {
        EvaluationMetricName.FAITHFULNESS,
        EvaluationMetricName.CITATION_COVERAGE,
        EvaluationMetricName.ENTITY_RELATION_QUALITY_SUMMARY,
    }
)
EVALUATION_METRIC_DISPLAY_ORDER: tuple[EvaluationMetricName, ...] = (
    EvaluationMetricName.RECALL_AT_K,
    EvaluationMetricName.MRR,
    EvaluationMetricName.CONTEXT_PRECISION,
    EvaluationMetricName.NO_CONTEXT_RATE,
    EvaluationMetricName.CLAIM_FAITHFULNESS,
    EvaluationMetricName.ANSWER_COMPLETENESS,
    EvaluationMetricName.GROUNDEDNESS,
    EvaluationMetricName.FAITHFULNESS,
    EvaluationMetricName.CITATION_CORRECTNESS,
    EvaluationMetricName.CITATION_PRESENCE,
    EvaluationMetricName.CITATION_COVERAGE,
    EvaluationMetricName.STRATEGY_SELECTION_ACCURACY,
    EvaluationMetricName.SUFFICIENCY_SCORE_AVG,
    EvaluationMetricName.FALLBACK_RATE,
    EvaluationMetricName.BUDGET_EXHAUSTED_RATE,
    EvaluationMetricName.RETRIEVAL_CALL_COUNT_AVG,
    EvaluationMetricName.GRAPH_PATH_RELEVANCE,
    EvaluationMetricName.GRAPH_CITATION_COVERAGE,
    EvaluationMetricName.MULTI_HOP_ANSWERABILITY,
    EvaluationMetricName.ENTITY_RELATION_QUALITY_SUMMARY,
    EvaluationMetricName.P95_LATENCY,
    EvaluationMetricName.CACHE_HIT_RATE,
    EvaluationMetricName.CACHE_SAVED_LATENCY,
)


def _metric_applicable_scopes(
    metric_name: EvaluationMetricName,
    category: EvaluationMetricCategory,
) -> tuple[EvaluationScope, ...]:
    if metric_name == EvaluationMetricName.P95_LATENCY:
        return ("retrieval", "answer", "end_to_end")
    if category in {EvaluationMetricCategory.ANSWER, EvaluationMetricCategory.CITATION}:
        return ("answer", "end_to_end")
    return ("retrieval", "end_to_end")


def _build_evaluation_metric_catalog() -> EvaluationMetricCatalog:
    spec_by_name = {spec.metric_name: spec for spec in STRATEGY_METRIC_SPECS}
    expected_names = set(EvaluationMetricName)
    if set(spec_by_name) != expected_names:
        raise RuntimeError("evaluation metric specs must cover every metric")
    if set(EVALUATION_METRIC_CATEGORY_BY_NAME) != expected_names:
        raise RuntimeError("evaluation metric categories must cover every metric")

    display_priority_by_name = {
        metric_name: index for index, metric_name in enumerate(EVALUATION_METRIC_DISPLAY_ORDER)
    }
    if set(EVALUATION_METRIC_DISPLAY_NAME_BY_NAME) != expected_names:
        raise RuntimeError("evaluation metric display names must cover every metric")
    if set(EVALUATION_METRIC_PLAIN_LANGUAGE_SUMMARY_BY_NAME) != expected_names:
        raise RuntimeError("evaluation metric summaries must cover every metric")
    if set(display_priority_by_name) != expected_names:
        raise RuntimeError("evaluation metric display order must cover every metric")
    return EvaluationMetricCatalog(
        metrics=[
            EvaluationMetricCatalogItem(
                metric_name=spec.metric_name,
                category=EVALUATION_METRIC_CATEGORY_BY_NAME[spec.metric_name],
                display_name=EVALUATION_METRIC_DISPLAY_NAME_BY_NAME[spec.metric_name],
                description=spec.description,
                plain_language_summary=EVALUATION_METRIC_PLAIN_LANGUAGE_SUMMARY_BY_NAME[
                    spec.metric_name
                ],
                higher_is_better=spec.higher_is_better,
                value_unit=spec.value_unit,
                alias_of=EVALUATION_METRIC_ALIAS_BY_NAME.get(spec.metric_name),
                importance=(
                    "primary"
                    if spec.metric_name in EVALUATION_METRIC_PRIMARY_SCOPES_BY_NAME
                    else "diagnostic"
                    if spec.metric_name in EVALUATION_METRIC_DIAGNOSTIC_NAMES
                    else "secondary"
                ),
                applicable_scopes=list(
                    _metric_applicable_scopes(
                        spec.metric_name, EVALUATION_METRIC_CATEGORY_BY_NAME[spec.metric_name]
                    )
                ),
                primary_scopes=list(
                    EVALUATION_METRIC_PRIMARY_SCOPES_BY_NAME.get(spec.metric_name, ())
                ),
                display_priority=display_priority_by_name[spec.metric_name],
                method=EVALUATION_METRIC_METHOD_BY_NAME.get(
                    spec.metric_name, EvaluationMetricMethod.DETERMINISTIC
                ),
            )
            for spec in STRATEGY_METRIC_SPECS
        ]
    )


EVALUATION_METRIC_CATALOG = _build_evaluation_metric_catalog()


@dataclass(frozen=True)
class LoadedEvaluationCase:
    case: EvaluationCase
    evaluation_case_id: int | None
    case_key: str
    metadata_json: dict[str, object] | None = None
    tags: list[str] | None = None


@dataclass(frozen=True)
class ManualCalibrationCaseContract:
    case_id: str
    answerable: bool
    required_citation: bool
    tags: tuple[str, ...]


@dataclass(frozen=True)
class PromotionSourceCase:
    evaluation_case_id: int | None
    case_key: str
    question: str
    expected_answer: str | None
    expected_keywords: list[str]
    expected_document_ids: list[int]
    expected_chunk_ids: list[int]
    required_citation: bool
    tags: list[str]
    metadata_json: dict[str, object] | None


@dataclass(frozen=True)
class LoadedEvaluationRunComparison:
    run: EvaluationRun
    summary: EvaluationRunSummary
    items: list[EvaluationRunItem]
    results_by_item: dict[int, list[EvaluationResult]]


@dataclass(frozen=True)
class EvaluationCaseComparisonSource:
    evaluation_run_item_id: int
    case_id: str
    question_hash: str | None
    case_snapshot_hash: str | None
    comparison_label: str | None
    status: EvaluationStatus
    metric_values: dict[str, float]


@dataclass(frozen=True)
class EvaluationStrategyTarget:
    comparison_label: str
    retrieval_strategy: RetrievalStrategy
    graph_store_provider: str | None = None
    cache_mode: EvaluationCacheMode = EvaluationCacheMode.DEFAULT

    @property
    def storage_strategy_type(self) -> str:
        return self.retrieval_strategy.value

    @property
    def request_strategy_label(self) -> str:
        base_label = self.comparison_label.split("__cache_", 1)[0]
        if base_label in GRAPH_COMPARISON_LABELS:
            return base_label
        return self.retrieval_strategy.value

    @property
    def cache_suffix(self) -> str | None:
        if self.cache_mode == EvaluationCacheMode.DEFAULT:
            return None
        return self.cache_mode.value


@dataclass(frozen=True)
class EvaluationGenerationSummary:
    total_estimated_cost_usd: float | None
    total_input_tokens: int | None
    total_output_tokens: int | None
    total_tokens: int | None
    avg_generation_latency_ms: float | None
    generation_providers: list[str]
    generation_models: list[str]


class EvaluationRagService(Protocol):
    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult: ...


class EvaluationService:
    def __init__(
        self,
        *,
        repository: EvaluationRepository | None = None,
        job_repository: JobRepository | None = None,
        rag_service_factory: Callable[..., EvaluationRagService] = create_evaluation_rag_service,
        settings: Settings | None = None,
        trace_export_service: TraceExportService | None = None,
        claim_judge_factory: Callable[[Settings], EvaluationClaimJudgeService] | None = None,
    ) -> None:
        self.repository = repository or EvaluationRepository()
        self.job_repository = job_repository or JobRepository()
        self.rag_service_factory = rag_service_factory
        self.settings = settings or get_settings()
        self.trace_export_service = trace_export_service or TraceExportService(self.settings)
        self.claim_judge_factory = claim_judge_factory or EvaluationClaimJudgeService

    @staticmethod
    def get_metric_catalog() -> EvaluationMetricCatalog:
        return EVALUATION_METRIC_CATALOG.model_copy(deep=True)

    def create_run(
        self,
        db: Session,
        *,
        payload: EvaluationRunCreateRequest,
        user: User,
    ) -> EvaluationRunCreateResponse:
        dataset_name = payload.dataset_name
        corpus_fingerprint: str | None = None
        logical_document_ids: list[int] = []
        if payload.evaluation_dataset_id is not None:
            dataset = self.repository.get_dataset(
                db,
                evaluation_dataset_id=payload.evaluation_dataset_id,
            )
            if dataset is None:
                raise ResourceNotFound()
            if dataset.status != "active":
                raise ValidationFailed({"evaluation_dataset_id": "dataset is archived"})
            active_case_count = self.repository.count_cases(
                db,
                evaluation_dataset_id=payload.evaluation_dataset_id,
                status="active",
            )
            if active_case_count < 1:
                raise ValidationFailed({"evaluation_dataset_id": "dataset has no active cases"})
            dataset_name = dataset.dataset_name
            corpus_fingerprint = getattr(dataset, "corpus_fingerprint", None)
            if getattr(dataset, "corpus_mode", "shared_legacy") == "isolated":
                readiness = self.get_corpus_readiness(
                    db,
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                )
                if not readiness.ready:
                    raise EvaluationCorpusNotReady(details=readiness.model_dump(mode="json"))
                logical_document_ids = [
                    source.logical_document_id
                    for source in readiness.sources
                    if source.logical_document_id is not None
                ]

        strategy_targets = _selected_strategy_targets(payload)
        strategies = [target.comparison_label for target in strategy_targets]
        metrics = [metric.value for metric in payload.metrics]
        evaluation_scope = payload.evaluation_scope or _evaluation_scope_from_targets(
            strategy_targets
        )
        strategy_type = strategy_targets[0].storage_strategy_type
        cache_modes = [mode.value for mode in _selected_cache_modes(payload.cache_modes)]
        trigger_type = payload.trigger_type.value
        retrieval_settings = _retrieval_settings_snapshot(
            strategy_type=strategy_type,
            strategies=strategies,
            metrics=metrics,
            cache_modes=cache_modes,
            evaluation_scope=evaluation_scope,
            strategy_targets=strategy_targets,
            case_limit=payload.case_limit,
            top_k=payload.top_k,
            rerank_top_n=payload.rerank_top_n,
        )
        if corpus_fingerprint is not None:
            retrieval_settings["corpus_fingerprint"] = corpus_fingerprint
        if logical_document_ids:
            retrieval_settings["logical_document_ids"] = logical_document_ids
        run = self.repository.create_run(
            db,
            created_by=user.user_id,
            dataset_name=dataset_name,
            evaluation_dataset_id=payload.evaluation_dataset_id,
            case_limit=payload.case_limit,
            strategy_type=strategy_type,
            strategies=strategies,
            metrics=metrics,
            evaluation_scope=evaluation_scope,
            top_k=payload.top_k,
            rerank_top_n=payload.rerank_top_n,
            generation_provider=payload.generation_provider,
            generation_model=payload.generation_model,
            trigger_type=trigger_type,
            corpus_fingerprint=corpus_fingerprint,
            retrieval_settings_json=retrieval_settings,
        )
        job = self.job_repository.create_job(
            db,
            job_type="evaluation_run",
            target_type="evaluation_run",
            target_id=run.evaluation_run_id,
            payload_json={
                "evaluation_run_id": run.evaluation_run_id,
                "dataset_name": dataset_name,
                "evaluation_dataset_id": payload.evaluation_dataset_id,
                "corpus_fingerprint": corpus_fingerprint,
                "logical_document_ids": logical_document_ids,
                "case_limit": payload.case_limit,
                "strategy_type": strategy_type,
                "strategies": strategies,
                "metrics": metrics,
                "cache_modes": cache_modes,
                "evaluation_scope": evaluation_scope,
                "strategy_targets": [_target_metadata_json(target) for target in strategy_targets],
                "top_k": payload.top_k,
                "rerank_top_n": payload.rerank_top_n,
                "generation_provider": payload.generation_provider,
                "generation_model": payload.generation_model,
                "trigger_type": trigger_type,
            },
            created_by=user.user_id,
            priority=100,
        )
        db.commit()
        db.refresh(run)
        db.refresh(job)
        return EvaluationRunCreateResponse(
            evaluation_run_id=run.evaluation_run_id,
            job_id=job.job_id,
            status="queued",
            strategies=strategies,
            evaluation_scope=evaluation_scope,
        )

    def get_generation_readiness(
        self,
        *,
        payload: EvaluationGenerationReadinessRequest,
    ) -> EvaluationGenerationReadinessResponse:
        if payload.generation_provider != "lmstudio":
            return EvaluationGenerationReadinessResponse(
                generation_provider=payload.generation_provider,
                requested_model=payload.generation_model,
                resolved_model=payload.generation_model,
                ready=True,
                reason_code="provider_not_checked",
            )
        readiness = check_lmstudio_model_readiness(
            self.settings,
            payload.generation_model,
        )
        return EvaluationGenerationReadinessResponse(
            generation_provider="lmstudio",
            requested_model=readiness.requested_model,
            resolved_model=readiness.resolved_model,
            ready=readiness.ready,
            reason_code=readiness.reason_code,
        )

    def create_dataset(
        self,
        db: Session,
        *,
        payload: EvaluationDatasetCreateRequest,
        user: User,
    ) -> EvaluationDatasetResponse:
        if self.repository.get_dataset_by_name_and_version(
            db,
            dataset_name=payload.dataset_name,
            version=payload.version,
        ):
            raise ConflictError()
        try:
            dataset = self.repository.create_dataset(
                db,
                dataset_name=payload.dataset_name,
                description=payload.description,
                version=payload.version,
                source_type=payload.source_type.value,
                status=payload.status.value,
                metadata_json=payload.metadata_json,
                created_by=user.user_id,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError() from exc
        db.refresh(dataset)
        return self._dataset_response(db, dataset)

    def list_datasets(
        self,
        db: Session,
        *,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> tuple[list[EvaluationDatasetResponse], PaginationMeta]:
        datasets, total = self.repository.list_datasets(
            db,
            offset=pagination.offset,
            limit=pagination.page_size,
            status=status,
        )
        return [self._dataset_response(db, dataset) for dataset in datasets], pagination_meta(
            pagination, total
        )

    def get_dataset_detail(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> EvaluationDatasetResponse:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        return self._dataset_response(db, dataset)

    def update_dataset(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        payload: EvaluationDatasetUpdateRequest,
    ) -> EvaluationDatasetResponse:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        fields_set = payload.model_fields_set
        immutable_changes = {
            "description": payload.description != dataset.description,
            "version": payload.version is not None and payload.version != dataset.version,
            "metadata_json": payload.metadata_json != dataset.metadata_json,
        }
        changed_fields = {
            field_name
            for field_name, changed in immutable_changes.items()
            if field_name in fields_set and changed
        }
        if dataset.content_fingerprint is not None and changed_fields:
            raise ConflictError(
                "dataset_version_conflict",
                details={
                    "evaluation_dataset_id": dataset.evaluation_dataset_id,
                    "immutable_fields": sorted(changed_fields),
                },
            )
        if (
            "version" in changed_fields
            and payload.version is not None
            and self.repository.get_dataset_by_name_and_version(
                db,
                dataset_name=dataset.dataset_name,
                version=payload.version,
            )
            is not None
        ):
            raise ConflictError()
        try:
            self.repository.update_dataset(
                db,
                dataset=dataset,
                description=payload.description,
                version=payload.version,
                metadata_json=payload.metadata_json,
                updated_at=datetime.now(UTC),
                description_provided="description" in fields_set,
                metadata_json_provided="metadata_json" in fields_set,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError() from exc
        db.refresh(dataset)
        return self._dataset_response(db, dataset)

    def archive_dataset(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> EvaluationDatasetResponse:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        self.repository.archive_dataset(db, dataset=dataset, updated_at=datetime.now(UTC))
        db.commit()
        db.refresh(dataset)
        return self._dataset_response(db, dataset)

    def create_case(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        payload: EvaluationCaseCreateRequest,
    ) -> EvaluationCaseResponse:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        if self.repository.get_case_by_key(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            case_key=payload.case_key,
        ):
            raise ConflictError()
        try:
            case = self.repository.create_case(
                db,
                evaluation_dataset_id=evaluation_dataset_id,
                case_key=payload.case_key,
                question=payload.question,
                expected_answer=payload.expected_answer,
                expected_keywords=payload.expected_keywords,
                expected_document_ids=payload.expected_document_ids,
                expected_chunk_ids=payload.expected_chunk_ids,
                required_citation=payload.required_citation,
                tags=payload.tags,
                metadata_json=payload.metadata_json,
                status=payload.status.value,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError() from exc
        db.refresh(case)
        return self._case_response(case)

    def list_cases(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> tuple[list[EvaluationCaseResponse], PaginationMeta]:
        if self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id) is None:
            raise ResourceNotFound()
        cases, total = self.repository.list_cases(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            offset=pagination.offset,
            limit=pagination.page_size,
            status=status,
        )
        return [self._case_response(case) for case in cases], pagination_meta(pagination, total)

    def get_case_detail(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        evaluation_case_id: int,
    ) -> EvaluationCaseResponse:
        case = self.repository.get_case(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            evaluation_case_id=evaluation_case_id,
        )
        if case is None:
            raise ResourceNotFound()
        return self._case_response(case)

    def update_case(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        evaluation_case_id: int,
        payload: EvaluationCaseUpdateRequest,
    ) -> EvaluationCaseResponse:
        case = self.repository.get_case(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            evaluation_case_id=evaluation_case_id,
        )
        if case is None:
            raise ResourceNotFound()
        values = payload.model_dump(exclude_unset=True)
        if values:
            _assert_case_expected_signal(case, values)
            self.repository.update_case(
                db,
                case=case,
                values=values,
                updated_at=datetime.now(UTC),
            )
            db.commit()
            db.refresh(case)
        return self._case_response(case)

    def archive_case(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        evaluation_case_id: int,
    ) -> EvaluationCaseResponse:
        case = self.repository.get_case(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            evaluation_case_id=evaluation_case_id,
        )
        if case is None:
            raise ResourceNotFound()
        self.repository.archive_case(db, case=case, updated_at=datetime.now(UTC))
        db.commit()
        db.refresh(case)
        return self._case_response(case)

    def _legacy_import_dataset_manifest(
        self,
        db: Session,
        *,
        manifest: EvaluationDatasetManifest,
        user: User,
    ) -> EvaluationDatasetImportResponse:
        dataset = self.repository.get_dataset_by_name(
            db,
            dataset_name=manifest.dataset.dataset_name,
        )
        result_code = "updated"
        if dataset is None:
            result_code = "created"
            dataset = self.repository.create_dataset(
                db,
                dataset_name=manifest.dataset.dataset_name,
                description=manifest.dataset.description,
                version=manifest.dataset.version,
                source_type=manifest.dataset.source_type.value,
                status=manifest.dataset.status.value,
                metadata_json=manifest.dataset.metadata_json,
                created_by=user.user_id,
            )
        else:
            self.repository.update_dataset(
                db,
                dataset=dataset,
                description=manifest.dataset.description,
                version=manifest.dataset.version,
                metadata_json=manifest.dataset.metadata_json,
                updated_at=datetime.now(UTC),
                description_provided=True,
                metadata_json_provided=True,
            )
            dataset.source_type = manifest.dataset.source_type.value
            dataset.status = manifest.dataset.status.value
        db.flush()

        imported_case_count = 0
        for case_spec in manifest.cases:
            existing = self.repository.get_case_by_key(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                case_key=case_spec.case_key,
            )
            if existing is None:
                self.repository.create_case(
                    db,
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    case_key=case_spec.case_key,
                    question=case_spec.question,
                    expected_answer=case_spec.expected_answer,
                    expected_keywords=case_spec.expected_keywords,
                    expected_document_ids=case_spec.expected_document_ids,
                    expected_chunk_ids=case_spec.expected_chunk_ids,
                    required_citation=case_spec.required_citation,
                    tags=case_spec.tags,
                    metadata_json=case_spec.metadata_json,
                    status=case_spec.status.value,
                )
            else:
                self.repository.update_case(
                    db,
                    case=existing,
                    values={
                        "question": case_spec.question,
                        "expected_answer": case_spec.expected_answer,
                        "expected_keywords": case_spec.expected_keywords,
                        "expected_document_ids": case_spec.expected_document_ids,
                        "expected_chunk_ids": case_spec.expected_chunk_ids,
                        "required_citation": case_spec.required_citation,
                        "tags": case_spec.tags,
                        "metadata_json": case_spec.metadata_json,
                        "status": case_spec.status.value,
                    },
                    updated_at=datetime.now(UTC),
                )
            imported_case_count += 1
        db.commit()
        db.refresh(dataset)
        return EvaluationDatasetImportResponse(
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            dataset_name=dataset.dataset_name,
            version=dataset.version,
            content_fingerprint=(
                dataset.content_fingerprint
                or hashlib.sha256(f"{dataset.dataset_name}:{dataset.version}".encode()).hexdigest()
            ),
            corpus_fingerprint=dataset.corpus_fingerprint,
            case_count=self.repository.count_cases(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
            ),
            imported_case_count=imported_case_count,
            result_code="created" if result_code == "created" else "unchanged",
        )

    def validate_dataset_manifest(
        self,
        *,
        manifest: EvaluationDatasetManifestInput,
    ) -> EvaluationDatasetValidationResponse:
        return EvaluationDatasetManifestService(self.repository).validate(
            manifest=manifest,
        )

    def import_dataset_manifest(
        self,
        db: Session,
        *,
        manifest: EvaluationDatasetManifestInput,
        user: User,
    ) -> EvaluationDatasetImportResponse:
        return EvaluationDatasetManifestService(self.repository).import_manifest(
            db,
            manifest=manifest,
            user=user,
        )

    def prepare_dataset_corpus(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        user: User,
        request_id: str | None,
    ) -> EvaluationCorpusPrepareResponse:
        return EvaluationCorpusService(self.repository).prepare(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            user=user,
            request_id=request_id,
        )

    def get_corpus_readiness(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> EvaluationCorpusReadinessResponse:
        return EvaluationCorpusService(self.repository).readiness(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
        )

    def export_dataset_manifest(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> EvaluationDatasetManifest:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        cases, _ = self.repository.list_cases(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            offset=0,
            limit=None,
        )
        if not cases:
            raise ValidationFailed({"dataset": "dataset has no cases"})
        return EvaluationDatasetManifest(
            schema_version=DATASET_MANIFEST_SCHEMA_VERSION,
            dataset=EvaluationDatasetManifestInfo(
                dataset_name=dataset.dataset_name,
                description=dataset.description,
                version=dataset.version,
                source_type=dataset.source_type,
                status=dataset.status,
                metadata_json=dataset.metadata_json,
            ),
            cases=[self._case_spec(case) for case in cases],
            metric_specs=list(STRATEGY_METRIC_SPECS),
        )

    def list_runs(
        self,
        db: Session,
        *,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> tuple[list[EvaluationRunSummary], PaginationMeta]:
        runs, total = self.repository.list_runs(
            db,
            offset=pagination.offset,
            limit=pagination.page_size,
            status=status,
        )
        return [self._summary(db, run) for run in runs], pagination_meta(pagination, total)

    def _load_run_comparison(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> LoadedEvaluationRunComparison:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise ResourceNotFound()
        items = self.repository.list_items(db, evaluation_run_id=evaluation_run_id)
        results_by_item = self.repository.list_results(
            db,
            evaluation_run_item_ids=[item.evaluation_run_item_id for item in items],
        )
        summary = self._summary_from_loaded(db, run, items, results_by_item)
        return LoadedEvaluationRunComparison(
            run=run,
            summary=summary,
            items=items,
            results_by_item=results_by_item,
        )

    def get_run_detail(self, db: Session, *, evaluation_run_id: int) -> EvaluationRunDetail:
        loaded = self._load_run_comparison(db, evaluation_run_id=evaluation_run_id)
        return EvaluationRunDetail(
            **loaded.summary.model_dump(),
            items=[
                self._item_response(
                    item,
                    loaded.results_by_item.get(item.evaluation_run_item_id, []),
                )
                for item in loaded.items
            ],
            failure_candidates=self._failure_candidates(db, run=loaded.run),
        )

    def get_human_calibrations(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> EvaluationHumanCalibrationSummary:
        run = self._require_manual_calibration_run(
            db,
            evaluation_run_id=evaluation_run_id,
        )
        now = datetime.now(UTC)
        if self.repository.purge_expired_review_payloads(db, now=now):
            db.commit()
        items = self.repository.list_items(
            db,
            evaluation_run_id=run.evaluation_run_id,
        )
        source_cases = self._promotion_source_cases(db, run)
        judgments = {
            row.evaluation_run_item_id: row
            for row in self.repository.list_auxiliary_judgments(
                db,
                evaluation_run_id=run.evaluation_run_id,
            )
        }
        review_payloads = self.repository.list_review_payloads(
            db,
            evaluation_run_id=run.evaluation_run_id,
        )
        targets: list[EvaluationHumanCalibrationTarget] = []
        for item in items:
            source = source_cases.get(item.evaluation_run_item_id)
            if source is None:
                continue
            contract = _manual_calibration_case_contract(source)
            judgment = judgments.get(item.evaluation_run_item_id)
            review_payload = review_payloads.get(item.evaluation_run_item_id)
            decision = _auxiliary_decision_from_judgment(judgment, case_id=contract.case_id)
            targets.append(
                EvaluationHumanCalibrationTarget(
                    evaluation_run_item_id=item.evaluation_run_item_id,
                    case_id=contract.case_id,
                    strategy_type=cast(RetrievalStrategy, item.strategy_type),
                    status=cast(EvaluationStatus, item.status),
                    answerable=contract.answerable,
                    required_citation=contract.required_citation,
                    prompt_injection="prompt_injection" in contract.tags,
                    judge_status=cast(
                        Literal["succeeded", "failed", "missing"],
                        judgment.status if judgment is not None else "missing",
                    ),
                    judge_failure_code=(judgment.failure_code if judgment is not None else None),
                    auxiliary_decision=decision,
                    claim_faithfulness=(
                        _decimal_float(judgment.claim_faithfulness)
                        if judgment is not None
                        else None
                    ),
                    generated_answer=(
                        review_payload.answer_text
                        if review_payload is not None and review_payload.purged_at is None
                        else None
                    ),
                    citation_excerpts=(
                        list(review_payload.citations_json or [])
                        if review_payload is not None and review_payload.purged_at is None
                        else []
                    ),
                    required_facts=(
                        list(review_payload.required_facts_json or [])
                        if review_payload is not None and review_payload.purged_at is None
                        else []
                    ),
                    review_payload_available=(
                        review_payload is not None
                        and review_payload.purged_at is None
                        and review_payload.answer_text is not None
                    ),
                    review_payload_expires_at=(
                        review_payload.expires_at if review_payload is not None else None
                    ),
                )
            )
        records = [
            self._human_calibration_response(row)
            for row in self.repository.list_human_calibrations(
                db,
                evaluation_run_id=run.evaluation_run_id,
            )
        ]
        agreement = calibration_agreement([record.human_calibration for record in records])
        return EvaluationHumanCalibrationSummary(
            evaluation_run_id=run.evaluation_run_id,
            eligible_count=len(targets),
            reviewed_count=len(records),
            agreement_rate=agreement,
            targets=targets,
            records=records,
        )

    def upsert_human_calibration(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        evaluation_run_item_id: int,
        payload: EvaluationHumanCalibrationUpsertRequest,
        user: User,
        request_id: str | None,
    ) -> EvaluationHumanCalibrationResponse:
        run = self._require_manual_calibration_run(
            db,
            evaluation_run_id=evaluation_run_id,
        )
        item = self.repository.get_item(
            db,
            evaluation_run_id=evaluation_run_id,
            evaluation_run_item_id=evaluation_run_item_id,
            for_update=True,
        )
        if item is None:
            raise ResourceNotFound()
        source_case = self._promotion_source_cases(db, run).get(item.evaluation_run_item_id)
        if source_case is None:
            raise ValidationFailed(
                {"evaluation_run_item_id": "item is not eligible for manual calibration"}
            )
        contract = _manual_calibration_case_contract(source_case)

        judgment = self.repository.get_auxiliary_judgment(
            db,
            evaluation_run_item_id=item.evaluation_run_item_id,
        )
        decision = _auxiliary_decision_from_judgment(judgment, case_id=contract.case_id)
        if decision is None:
            raise ValidationFailed(
                {"evaluation_run_item_id": ("a successful automatic judge result is required")}
            )
        try:
            auxiliary_pass = grounded_answer_pass(contract, decision)
            human_pass = payload.human_pass
            if payload.human_dimensions is not None:
                human_decision = AuxiliaryJudgeDecision(
                    case_id=contract.case_id,
                    rubric_version=decision.rubric_version,
                    required_facts_supported=(payload.human_dimensions.required_facts_supported),
                    citation_support=payload.human_dimensions.citation_support,
                    forbidden_claims_absent=(payload.human_dimensions.forbidden_claims_absent),
                    abstention_correct=payload.human_dimensions.abstention_correct,
                    prompt_injection_resisted=(payload.human_dimensions.prompt_injection_resisted),
                    confidence=1.0,
                    reason_codes=payload.human_reason_codes,
                )
                calculated_human_pass = grounded_answer_pass(contract, human_decision)
                if calculated_human_pass != human_pass:
                    raise ValueError("manual dimensions do not match human_pass")
            human_record = HumanCalibrationRecord(
                case_id=contract.case_id,
                rubric_version=decision.rubric_version,
                auxiliary_pass=auxiliary_pass,
                human_pass=human_pass,
                disagreement_category=payload.disagreement_category,
                reason_codes=payload.human_reason_codes,
            )
        except (ValueError, PydanticValidationError) as exc:
            raise ValidationFailed(
                {"manual_calibration": "verdicts do not match the calibration contract"}
            ) from exc

        now = datetime.now(UTC)
        self.repository.purge_expired_review_payloads(db, now=now)
        row = self.repository.upsert_human_calibration(
            db,
            evaluation_run_item_id=item.evaluation_run_item_id,
            case_id=contract.case_id,
            rubric_version=decision.rubric_version,
            required_facts_supported=decision.required_facts_supported.value,
            citation_support=decision.citation_support.value,
            forbidden_claims_absent=decision.forbidden_claims_absent.value,
            abstention_correct=decision.abstention_correct.value,
            prompt_injection_resisted=decision.prompt_injection_resisted.value,
            human_required_facts_supported=(
                payload.human_dimensions.required_facts_supported.value
                if payload.human_dimensions is not None
                else None
            ),
            human_citation_support=(
                payload.human_dimensions.citation_support.value
                if payload.human_dimensions is not None
                else None
            ),
            human_forbidden_claims_absent=(
                payload.human_dimensions.forbidden_claims_absent.value
                if payload.human_dimensions is not None
                else None
            ),
            human_abstention_correct=(
                payload.human_dimensions.abstention_correct.value
                if payload.human_dimensions is not None
                else None
            ),
            human_prompt_injection_resisted=(
                payload.human_dimensions.prompt_injection_resisted.value
                if payload.human_dimensions is not None
                else None
            ),
            auxiliary_confidence=Decimal(str(decision.confidence)).quantize(Decimal("0.0001")),
            auxiliary_reason_codes=[code.value for code in decision.reason_codes],
            auxiliary_pass=human_record.auxiliary_pass,
            human_pass=human_record.human_pass,
            disagreement_category=(
                human_record.disagreement_category.value
                if human_record.disagreement_category is not None
                else None
            ),
            human_reason_codes=[code.value for code in human_record.reason_codes],
            reviewed_by=user.user_id,
            updated_at=now,
        )
        audit(
            db,
            action="evaluation.human_calibration_saved",
            actor_user_id=user.user_id,
            request_id=request_id,
            target_type="evaluation_human_calibration",
            target_id=row.evaluation_human_calibration_id,
            metadata={
                "evaluation_run_id": evaluation_run_id,
                "evaluation_run_item_id": evaluation_run_item_id,
                "case_id": contract.case_id,
                "auxiliary_pass": human_record.auxiliary_pass,
                "human_pass": human_record.human_pass,
                "disagreement_category": row.disagreement_category,
            },
        )
        db.commit()
        db.refresh(row)
        return self._human_calibration_response(row)

    def _require_manual_calibration_run(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> EvaluationRun:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise ResourceNotFound()
        return run

    @staticmethod
    def _human_calibration_response(
        row: EvaluationHumanCalibration,
    ) -> EvaluationHumanCalibrationResponse:
        decision = AuxiliaryJudgeDecision(
            case_id=row.case_id,
            rubric_version=cast(Literal["phase3.grounded_answer_judge.v1"], row.rubric_version),
            required_facts_supported=row.required_facts_supported,
            citation_support=row.citation_support,
            forbidden_claims_absent=row.forbidden_claims_absent,
            abstention_correct=row.abstention_correct,
            prompt_injection_resisted=row.prompt_injection_resisted,
            confidence=float(row.auxiliary_confidence),
            reason_codes=row.auxiliary_reason_codes_json,
        )
        human_record = HumanCalibrationRecord(
            case_id=row.case_id,
            rubric_version=cast(Literal["phase3.grounded_answer_judge.v1"], row.rubric_version),
            auxiliary_pass=row.auxiliary_pass,
            human_pass=row.human_pass,
            disagreement_category=row.disagreement_category,
            reason_codes=row.human_reason_codes_json,
        )
        return EvaluationHumanCalibrationResponse(
            evaluation_human_calibration_id=row.evaluation_human_calibration_id,
            evaluation_run_item_id=row.evaluation_run_item_id,
            auxiliary_decision=decision,
            human_dimensions=_manual_dimensions_from_row(row),
            human_calibration=human_record,
            reviewed_by=row.reviewed_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def compare_runs(
        self,
        db: Session,
        *,
        base_run_id: int,
        candidate_run_id: int,
    ) -> EvaluationRunComparison:
        base = self._load_run_comparison(db, evaluation_run_id=base_run_id)
        candidate = (
            base
            if base_run_id == candidate_run_id
            else self._load_run_comparison(db, evaluation_run_id=candidate_run_id)
        )
        metrics = _compare_metric_summaries(base.summary, candidate.summary)
        generation = _compare_generation_summaries(
            base.summary,
            candidate.summary,
            base.items,
            base.results_by_item,
            candidate.items,
            candidate.results_by_item,
        )
        cases = _compare_run_cases(
            base.items,
            base.results_by_item,
            candidate.items,
            candidate.results_by_item,
        )
        return EvaluationRunComparison(
            base_run=base.summary,
            candidate_run=candidate.summary,
            generation=generation,
            metrics=metrics,
            cases=cases,
            summary=EvaluationRunComparisonSummary(
                improved_metric_count=sum(
                    1 for metric in metrics if metric.direction == "improved"
                ),
                regressed_metric_count=sum(
                    1 for metric in metrics if metric.direction == "regressed"
                ),
                unchanged_metric_count=sum(
                    1 for metric in metrics if metric.direction == "unchanged"
                ),
                regressed_case_count=sum(1 for case in cases if case.transition == "regressed"),
                improved_case_count=sum(1 for case in cases if case.transition == "improved"),
                common_case_count=sum(
                    1
                    for case in cases
                    if case.base_status is not None and case.candidate_status is not None
                ),
                base_only_case_count=sum(1 for case in cases if case.transition == "removed"),
                candidate_only_case_count=sum(1 for case in cases if case.transition == "added"),
            ),
        )

    def get_strategy_comparison(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> EvaluationStrategyComparisonResponse:
        detail = self.get_run_detail(db, evaluation_run_id=evaluation_run_id)
        return EvaluationStrategyComparisonResponse(
            evaluation_run_id=evaluation_run_id,
            strategies=detail.strategies,
            metrics=detail.strategy_comparison,
        )

    def list_failure_candidates(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> EvaluationFailureCandidatesResponse:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise ResourceNotFound()
        return EvaluationFailureCandidatesResponse(
            evaluation_run_id=evaluation_run_id,
            candidates=self._failure_candidates(db, run=run),
        )

    def promote_failures(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        payload: EvaluationFailurePromotionRequest,
    ) -> EvaluationFailurePromotionResponse:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise ResourceNotFound()
        target = self.repository.get_dataset(
            db,
            evaluation_dataset_id=payload.target_dataset_id,
        )
        if target is None:
            raise ResourceNotFound()
        if target.status != "active":
            raise ValidationFailed({"target_dataset_id": "target dataset is archived"})

        selected_types = set(payload.failure_types or [])
        selected_promotion_keys = set(payload.promotion_keys or [])
        candidates = [
            candidate
            for candidate in self._failure_candidates(db, run=run)
            if (not selected_promotion_keys or candidate.promotion_key in selected_promotion_keys)
            and (not selected_types or candidate.failure_type in selected_types)
            and _severity_rank(candidate.severity) >= _severity_rank(payload.min_severity)
        ]
        candidates = _primary_failure_candidates(candidates)
        limit = min(payload.limit, self.settings.evaluation_failure_max_promotions_per_run)
        candidates = candidates[:limit]
        source_cases = self._promotion_source_cases(db, run)

        created_count = 0
        skipped_count = 0
        items: list[EvaluationFailurePromotionItem] = []
        for candidate in candidates:
            source_case = source_cases.get(candidate.evaluation_run_item_id)
            if source_case is None:
                skipped_count += 1
                items.append(
                    EvaluationFailurePromotionItem(
                        promotion_key=candidate.promotion_key,
                        failure_type=candidate.failure_type,
                        strategy_type=candidate.strategy_type,
                        evaluation_run_item_id=candidate.evaluation_run_item_id,
                        evaluation_case_id=candidate.evaluation_case_id,
                        result_code="source_case_missing",
                    )
                )
                continue

            if _source_case_changed(source_case, candidate):
                skipped_count += 1
                items.append(
                    EvaluationFailurePromotionItem(
                        promotion_key=candidate.promotion_key,
                        failure_type=candidate.failure_type,
                        strategy_type=candidate.strategy_type,
                        evaluation_run_item_id=candidate.evaluation_run_item_id,
                        evaluation_case_id=candidate.evaluation_case_id,
                        result_code="source_case_changed",
                    )
                )
                continue

            case_key = f"failure_{candidate.promotion_key[:16]}"
            existing = self.repository.get_case_by_key(
                db,
                evaluation_dataset_id=payload.target_dataset_id,
                case_key=case_key,
            )
            if existing is not None:
                skipped_count += 1
                items.append(
                    EvaluationFailurePromotionItem(
                        promotion_key=candidate.promotion_key,
                        failure_type=candidate.failure_type,
                        strategy_type=candidate.strategy_type,
                        evaluation_run_item_id=candidate.evaluation_run_item_id,
                        evaluation_case_id=candidate.evaluation_case_id,
                        promoted_case_id=existing.evaluation_case_id,
                        case_key=existing.case_key,
                        result_code="already_exists",
                    )
                )
                continue

            try:
                with db.begin_nested():
                    created = self.repository.create_case(
                        db,
                        evaluation_dataset_id=payload.target_dataset_id,
                        case_key=case_key,
                        question=source_case.question,
                        expected_answer=source_case.expected_answer,
                        expected_keywords=source_case.expected_keywords,
                        expected_document_ids=source_case.expected_document_ids,
                        expected_chunk_ids=source_case.expected_chunk_ids,
                        required_citation=source_case.required_citation,
                        tags=_promotion_tags(source_case.tags, candidate.recommended_tags),
                        metadata_json=_promotion_metadata(candidate, source_case.metadata_json),
                        status="active",
                    )
            except IntegrityError as exc:
                existing = self.repository.get_case_by_key(
                    db,
                    evaluation_dataset_id=payload.target_dataset_id,
                    case_key=case_key,
                )
                if existing is None:
                    raise ConflictError("evaluation_case_conflict") from exc
                skipped_count += 1
                items.append(
                    EvaluationFailurePromotionItem(
                        promotion_key=candidate.promotion_key,
                        failure_type=candidate.failure_type,
                        strategy_type=candidate.strategy_type,
                        evaluation_run_item_id=candidate.evaluation_run_item_id,
                        evaluation_case_id=candidate.evaluation_case_id,
                        promoted_case_id=existing.evaluation_case_id,
                        case_key=existing.case_key,
                        result_code="already_exists",
                    )
                )
                continue
            created_count += 1
            items.append(
                EvaluationFailurePromotionItem(
                    promotion_key=candidate.promotion_key,
                    failure_type=candidate.failure_type,
                    strategy_type=candidate.strategy_type,
                    evaluation_run_item_id=candidate.evaluation_run_item_id,
                    evaluation_case_id=candidate.evaluation_case_id,
                    promoted_case_id=created.evaluation_case_id,
                    case_key=created.case_key,
                    result_code="created",
                )
            )

        db.commit()
        return EvaluationFailurePromotionResponse(
            evaluation_run_id=evaluation_run_id,
            target_dataset_id=payload.target_dataset_id,
            created_count=created_count,
            skipped_count=skipped_count,
            items=items,
        )

    def run_job(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        request_id: str | None,
    ) -> dict[str, object]:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id, for_update=True)
        if run is None:
            raise EvaluationFixtureError("evaluation_run_not_found")
        if run.status == "succeeded":
            return {"status": "succeeded", "evaluation_run_id": evaluation_run_id, "noop": True}

        config = _config(run)
        now = datetime.now(UTC)
        self.repository.purge_expired_review_payloads(db, now=now)
        cache_attempt_id = _evaluation_cache_attempt_id(evaluation_run_id, now)
        self.repository.mark_run_running(db, run=run, started_at=now)
        self.repository.delete_items_and_results(db, evaluation_run_id=evaluation_run_id)
        db.commit()

        try:
            cases = self._load_cases_for_run(db, run)
        except EvaluationFixtureError as exc:
            run = self._require_run(db, evaluation_run_id)
            self.repository.mark_run_failed(
                db,
                run=run,
                error_code=str(exc),
                error_message=None,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            raise

        succeeded_count = 0
        failed_count = 0
        try:
            generation_provider = cast(str | None, config["generation_provider"])
            generation_model = cast(str | None, config["generation_model"])
            rag_service = self._create_rag_service(
                db,
                generation_provider=generation_provider,
                generation_model=generation_model,
            )
            strategy_targets = _strategy_targets_from_config(config)
            requested_metrics = set(cast(list[str], config["metrics"]))
            evaluation_scope = cast(EvaluationScope, config["evaluation_scope"])
            claim_judge = (
                self.claim_judge_factory(self.settings)
                if evaluation_scope == "end_to_end"
                and cast(str | None, config["corpus_fingerprint"]) is not None
                else None
            )
            top_k = cast(int | None, config["top_k"])
            rerank_top_n = cast(int | None, config["rerank_top_n"])
            logical_document_ids = cast(list[int], config["logical_document_ids"])
            corpus_fingerprint = cast(str | None, config["corpus_fingerprint"])
            latency_baselines: dict[tuple[str, str], int] = {}
            for loaded_case in cases:
                for target in strategy_targets:
                    item = self.repository.create_item(
                        db,
                        evaluation_run_id=evaluation_run_id,
                        status="running",
                        strategy_type=target.storage_strategy_type,
                        evaluation_case_id=loaded_case.evaluation_case_id,
                        case_key=loaded_case.case_key,
                    )
                    item_id = item.evaluation_run_item_id
                    db.commit()
                    try:
                        baseline_key = (loaded_case.case_key, target.request_strategy_label)
                        case_result = self._run_case(
                            db,
                            rag_service=rag_service,
                            case=loaded_case.case,
                            case_metadata_json=loaded_case.metadata_json,
                            target=target,
                            requested_metrics=requested_metrics,
                            evaluation_scope=evaluation_scope,
                            request_id=_case_request_id(
                                request_id,
                                case_key=loaded_case.case_key,
                                strategy_type=target.comparison_label,
                            ),
                            top_k=top_k,
                            rerank_top_n=rerank_top_n,
                            logical_document_ids=logical_document_ids,
                            corpus_fingerprint=corpus_fingerprint,
                            evaluation_run_id=evaluation_run_id,
                            cache_attempt_id=_evaluation_cache_target_id(
                                cache_attempt_id,
                                case_key=loaded_case.case_key,
                                target_label=target.request_strategy_label,
                            ),
                            baseline_latency_ms=latency_baselines.get(baseline_key),
                        )
                    except Exception:
                        db.rollback()
                        failed_count += 1
                        self._store_case_failure(
                            db,
                            item_id=item_id,
                            case=loaded_case.case,
                            target=target,
                            requested_metrics=requested_metrics,
                        )
                        db.commit()
                        continue
                    if case_result["status"] == "succeeded":
                        succeeded_count += 1
                    else:
                        failed_count += 1
                    if target.cache_mode == EvaluationCacheMode.COLD and isinstance(
                        case_result.get("latency_ms"), int
                    ):
                        latency_baselines[baseline_key] = cast(int, case_result["latency_ms"])
                    self._store_case_result(
                        db,
                        item=item,
                        case_result=case_result,
                        target=target,
                        claim_judge=claim_judge,
                    )
                    db.commit()
        except Exception:
            db.rollback()
            run = self._require_run(db, evaluation_run_id)
            self.repository.mark_run_failed(
                db,
                run=run,
                error_code="internal_error",
                error_message=None,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            raise

        run = self._require_run(db, evaluation_run_id)
        summary = self._summary(db, run)
        item_count = len(cases) * len(_strategy_targets_from_config(config))
        failure_candidates = self._failure_candidates(db, run=run)
        run.strategy_metrics_summary_json = _strategy_metrics_summary_json(
            strategies=_strategy_values(_config(run)),
            strategy_comparison=summary.strategy_comparison,
            metric_summary=summary.metric_summary,
            case_count=item_count,
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            failure_candidates=failure_candidates,
        )
        if succeeded_count == 0:
            self.repository.mark_run_failed(
                db,
                run=run,
                error_code="all_cases_failed",
                error_message=None,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            self._export_evaluation_trace_safely(db, evaluation_run_id=evaluation_run_id)
            raise EvaluationFixtureError("all_cases_failed")
        self.repository.mark_run_succeeded(db, run=run, finished_at=datetime.now(UTC))
        db.commit()
        self._export_evaluation_trace_safely(db, evaluation_run_id=evaluation_run_id)
        return {
            "status": "succeeded",
            "evaluation_run_id": evaluation_run_id,
            "case_count": item_count,
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
        }

    def _export_evaluation_trace_safely(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> None:
        try:
            run = self._require_run(db, evaluation_run_id)
            summary = self._summary(db, run)
            self.trace_export_service.export_evaluation_summary(summary)
        except Exception:
            return

    def _create_rag_service(
        self,
        db: Session,
        *,
        generation_provider: str | None,
        generation_model: str | None,
    ) -> EvaluationRagService:
        if _factory_accepts_generation_selection(self.rag_service_factory):
            return self.rag_service_factory(
                self.settings,
                db,
                generation_provider=generation_provider,
                generation_model=generation_model,
            )
        return self.rag_service_factory(self.settings, db)

    def _run_case(
        self,
        db: Session,
        *,
        rag_service: EvaluationRagService,
        case: EvaluationCase,
        case_metadata_json: dict[str, object] | None,
        target: EvaluationStrategyTarget,
        requested_metrics: set[str],
        evaluation_scope: EvaluationScope,
        request_id: str | None,
        top_k: int | None,
        rerank_top_n: int | None,
        logical_document_ids: Sequence[int],
        corpus_fingerprint: str | None,
        evaluation_run_id: int,
        cache_attempt_id: str,
        baseline_latency_ms: int | None,
    ) -> dict[str, object]:
        started = time.perf_counter()
        target_runner = getattr(rag_service, "evaluate_strategy_target", None)
        strategy_runner = getattr(rag_service, "evaluate_strategy", None)
        generate_answer = evaluation_scope == "end_to_end"
        common_kwargs: dict[str, object] = {
            "question": case.question,
            "request_id": request_id,
            "strategy_type": target.retrieval_strategy,
            "top_k": top_k,
            "rerank_top_n": rerank_top_n,
        }
        if callable(target_runner) and (
            _requires_strategy_target_runner(target) or generate_answer
        ):
            target_kwargs: dict[str, object] = {
                "question": case.question,
                "request_id": request_id,
                "target": target,
                "top_k": top_k,
                "rerank_top_n": rerank_top_n,
                "evaluation_run_id": evaluation_run_id,
                "cache_attempt_id": cache_attempt_id,
            }
            if _callable_accepts_parameter(target_runner, "generate_answer"):
                target_kwargs["generate_answer"] = generate_answer
            rag_result = _call_evaluation_runner(
                cast(Callable[..., RagEvaluationResult], target_runner),
                db,
                kwargs=target_kwargs,
                logical_document_ids=logical_document_ids,
                corpus_fingerprint=corpus_fingerprint,
            )
        elif generate_answer:
            answer_runner = getattr(rag_service, "answer_question_with_strategy", None)
            selected_runner = (
                cast(Callable[..., RagEvaluationResult], answer_runner)
                if callable(answer_runner)
                else rag_service.evaluate_question
            )
            rag_result = _call_evaluation_runner(
                selected_runner,
                db,
                kwargs=common_kwargs,
                logical_document_ids=logical_document_ids,
            )
        elif target.retrieval_strategy in ASK_ONLY_EVALUATION_STRATEGIES:
            rag_result = _call_evaluation_runner(
                rag_service.evaluate_question,
                db,
                kwargs=common_kwargs,
                logical_document_ids=logical_document_ids,
            )
        elif callable(strategy_runner):
            rag_result = _call_evaluation_runner(
                cast(Callable[..., RagEvaluationResult], strategy_runner),
                db,
                kwargs=common_kwargs,
                logical_document_ids=logical_document_ids,
            )
        else:
            rag_result = _call_evaluation_runner(
                rag_service.evaluate_question,
                db,
                kwargs=common_kwargs,
                logical_document_ids=logical_document_ids,
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        metrics = _replace_metrics(
            calculate_metrics(
                EvaluationMetricInputs(
                    case=case,
                    answer_text=rag_result.answer_text,
                    citations=rag_result.citations,
                    confidence=rag_result.confidence,
                    retrieval_summary=rag_result.retrieval_score_summary,
                    retrieved_items=rag_result.retrieved_items,
                    latency_ms=latency_ms,
                    error_code=rag_result.error_code,
                )
            ),
            self._agentic_metrics(
                db,
                strategy_type=target.retrieval_strategy,
                case_metadata_json=case_metadata_json,
                rag_result=rag_result,
            ),
        )
        if _is_safe_provider_skip(rag_result):
            metrics = _replace_metrics(
                metrics,
                _provider_skip_base_metric_replacements(target=target, rag_result=rag_result),
            )
        metrics = _replace_metrics(
            metrics,
            [
                *self._graph_metrics(
                    db,
                    case=case,
                    case_metadata_json=case_metadata_json,
                    target=target,
                    rag_result=rag_result,
                ),
                *self._retrieval_trace_metrics(
                    db,
                    target=target,
                    rag_result=rag_result,
                ),
                *self._cache_metrics(
                    db,
                    target=target,
                    rag_result=rag_result,
                    latency_ms=latency_ms,
                    baseline_latency_ms=baseline_latency_ms,
                ),
            ],
        )
        metrics = _filter_metrics(metrics, requested_metrics)
        status = (
            "succeeded"
            if rag_result.status == "succeeded" or _is_safe_provider_skip(rag_result)
            else "failed"
        )
        return {
            "case": case,
            "rag_result": rag_result,
            "metrics": metrics,
            "latency_ms": latency_ms,
            "status": status,
            "target": target,
            "requested_metrics": requested_metrics,
        }

    def _store_case_result(
        self,
        db: Session,
        *,
        item: EvaluationRunItem,
        case_result: dict[str, object],
        target: EvaluationStrategyTarget,
        claim_judge: EvaluationClaimJudgeService | None,
    ) -> None:
        rag_result = case_result["rag_result"]
        case = case_result["case"]
        metrics = case_result["metrics"]
        result_target = case_result["target"]
        if (
            not isinstance(rag_result, RagEvaluationResult)
            or not isinstance(case, EvaluationCase)
            or not isinstance(metrics, list)
            or not isinstance(result_target, EvaluationStrategyTarget)
        ):
            raise RuntimeError("invalid_evaluation_case_result")
        latency_ms = case_result["latency_ms"]
        if not isinstance(latency_ms, int):
            raise RuntimeError("invalid_evaluation_case_result")
        status = str(case_result["status"])
        answer_outcome = _resolved_answer_outcome(rag_result)
        requested_metrics = case_result.get("requested_metrics")
        if not isinstance(requested_metrics, set):
            raise RuntimeError("invalid_evaluation_case_result")
        judge_metric = self._store_claim_judgment(
            db,
            item=item,
            case=case,
            rag_result=rag_result,
            answer_outcome=answer_outcome,
            claim_judge=claim_judge,
        )
        if (
            judge_metric is not None
            and EvaluationMetricName.CLAIM_FAITHFULNESS.value in requested_metrics
        ):
            metrics = _replace_metrics(metrics, [judge_metric])
        generation_provider = (
            _safe_optional_generation_label(
                rag_result.generation_provider,
                max_length=50,
            )
            if status == "succeeded"
            else None
        )
        generation_model = (
            _safe_optional_generation_label(
                rag_result.generation_model,
                max_length=128,
            )
            if status == "succeeded"
            else None
        )
        input_tokens = (
            _non_negative_int_or_none(rag_result.input_tokens) if status == "succeeded" else None
        )
        output_tokens = (
            _non_negative_int_or_none(rag_result.output_tokens) if status == "succeeded" else None
        )
        total_tokens = (
            _non_negative_int_or_none(rag_result.total_tokens) if status == "succeeded" else None
        )
        estimated_cost_usd = (
            _decimal_cost_usd(rag_result.estimated_cost_usd) if status == "succeeded" else None
        )
        generation_latency_ms = (
            _non_negative_int_or_none(rag_result.generation_latency_ms)
            if status == "succeeded"
            else None
        )
        metric_by_name = {
            metric.metric_name: metric for metric in metrics if isinstance(metric, MetricValue)
        }
        metric_summary_json = _metric_summary_json(
            metrics,
            case=case,
            target=result_target,
            answer_generated=answer_outcome == EvaluationAnswerOutcome.ANSWERED.value,
        )
        self.repository.finish_item(
            db,
            item=item,
            status=status,
            answer_outcome=answer_outcome,
            retrieval_run_id=rag_result.retrieval_run_id,
            faithfulness_score=_metric_decimal(metric_by_name.get("faithfulness")),
            groundedness_score=_metric_decimal(metric_by_name.get("groundedness")),
            citation_coverage=_metric_decimal(metric_by_name.get("citation_coverage")),
            latency_ms=latency_ms,
            generation_provider=generation_provider,
            generation_model=generation_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            generation_latency_ms=generation_latency_ms,
            latency_breakdown_json=_latency_breakdown_json(latency_ms),
            metric_summary_json=metric_summary_json,
            error_code=rag_result.error_code if status == "failed" else None,
            error_detail_code=(rag_result.error_detail_code if status == "failed" else None),
            error_message=None,
        )
        self.repository.save_results(
            db,
            evaluation_run_item_id=item.evaluation_run_item_id,
            results=[
                _result_input(metric, strategy_type=target.storage_strategy_type, target=target)
                for metric in metrics
                if isinstance(metric, MetricValue)
            ],
        )

    def _store_claim_judgment(
        self,
        db: Session,
        *,
        item: EvaluationRunItem,
        case: EvaluationCase,
        rag_result: RagEvaluationResult,
        answer_outcome: str | None,
        claim_judge: EvaluationClaimJudgeService | None,
    ) -> MetricValue | None:
        metadata = case.metadata_json if isinstance(case.metadata_json, dict) else {}
        if (
            claim_judge is None
            or metadata.get("manifest_schema_version") != DATASET_MANIFEST_V2_SCHEMA_VERSION
            or answer_outcome
            not in {
                EvaluationAnswerOutcome.ANSWERED.value,
                EvaluationAnswerOutcome.ABSTAINED.value,
            }
        ):
            return None

        required_facts = _safe_dict_list(metadata.get("required_facts"))
        forbidden_claims = _string_list(metadata.get("forbidden_claims"))
        context = [
            value
            for value in rag_result.context_sources_for_safety
            if isinstance(value, str) and value.strip()
        ]
        citations = [
            {
                "citation_id": citation.citation_id,
                "local_citation_id": citation.local_citation_id,
                "source_label": citation.source_label,
                "snippet": citation.snippet,
            }
            for citation in rag_result.citations
        ]
        answer_hash = hashlib.sha256(rag_result.answer_text.encode("utf-8")).hexdigest()
        context_hash = hashlib.sha256("\\x00".join(context).encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=30)
        metric_score: float | None = None
        metric_label = "not_applicable"
        reason_code = "judge_failed"
        try:
            judged = claim_judge.judge(
                case_id=case.case_id,
                answerable=metadata.get("answerable") is True,
                required_citation=case.required_citation,
                tags=list(case.tags),
                answer_outcome=cast(
                    Literal["answered", "abstained"],
                    answer_outcome,
                ),
                answer_text=rag_result.answer_text,
                citations=citations,
                context=context,
                required_facts=required_facts,
                forbidden_claims=forbidden_claims,
            )
            decision = judged.decision
            self.repository.upsert_auxiliary_judgment(
                db,
                evaluation_run_item_id=item.evaluation_run_item_id,
                values={
                    "status": "succeeded",
                    "rubric_version": decision.rubric_version,
                    "judge_provider": claim_judge.provider,
                    "judge_model": claim_judge.model,
                    "required_facts_supported": decision.required_facts_supported.value,
                    "citation_support": decision.citation_support.value,
                    "forbidden_claims_absent": decision.forbidden_claims_absent.value,
                    "abstention_correct": decision.abstention_correct.value,
                    "prompt_injection_resisted": (decision.prompt_injection_resisted.value),
                    "confidence": Decimal(str(decision.confidence)).quantize(Decimal("0.0001")),
                    "reason_codes_json": [code.value for code in decision.reason_codes],
                    "auxiliary_pass": judged.auxiliary_pass,
                    "claim_faithfulness": _decimal_score(judged.claim_faithfulness),
                    "failure_code": None,
                    "answer_hash": judged.answer_hash,
                    "context_hash": judged.context_hash,
                },
                updated_at=now,
            )
            metric_score = judged.claim_faithfulness
            metric_label = (
                _metric_label(metric_score) if metric_score is not None else "not_applicable"
            )
            reason_code = (
                "claim_faithfulness_available"
                if metric_score is not None
                else "claim_faithfulness_not_applicable"
            )
        except Exception:
            self.repository.upsert_auxiliary_judgment(
                db,
                evaluation_run_item_id=item.evaluation_run_item_id,
                values={
                    "status": "failed",
                    "rubric_version": JUDGE_RUBRIC_VERSION,
                    "judge_provider": DEFAULT_JUDGE_PROVIDER,
                    "judge_model": DEFAULT_JUDGE_MODEL,
                    "required_facts_supported": None,
                    "citation_support": None,
                    "forbidden_claims_absent": None,
                    "abstention_correct": None,
                    "prompt_injection_resisted": None,
                    "confidence": None,
                    "reason_codes_json": [],
                    "auxiliary_pass": None,
                    "claim_faithfulness": None,
                    "failure_code": "judge_failed",
                    "answer_hash": answer_hash,
                    "context_hash": context_hash,
                },
                updated_at=now,
            )

        self.repository.upsert_review_payload(
            db,
            evaluation_run_item_id=item.evaluation_run_item_id,
            values={
                "answer_text": rag_result.answer_text,
                "context_json": context,
                "citations_json": citations,
                "required_facts_json": required_facts,
                "answer_hash": answer_hash,
                "context_hash": context_hash,
                "expires_at": expires_at,
            },
            updated_at=now,
        )
        return MetricValue(
            metric_name=EvaluationMetricName.CLAIM_FAITHFULNESS.value,
            metric_score=metric_score,
            metric_label=metric_label,
            details={
                "schema_version": EVALUATION_SCHEMA_VERSION,
                "method": "local_judge",
                "reason_code": reason_code,
                "judge_coverage": 1.0 if metric_score is not None else 0.0,
            },
        )

    def _store_case_failure(
        self,
        db: Session,
        *,
        item_id: int,
        case: EvaluationCase,
        target: EvaluationStrategyTarget,
        requested_metrics: set[str],
    ) -> None:
        item = db.get(EvaluationRunItem, item_id)
        if item is None:
            return
        metrics = _filter_metrics(
            failure_metrics(case, error_code="internal_error"),
            requested_metrics,
        )
        self.repository.finish_item(
            db,
            item=item,
            status="failed",
            answer_outcome=EvaluationAnswerOutcome.RETRIEVAL_ERROR.value,
            retrieval_run_id=None,
            faithfulness_score=_metric_decimal(_find_metric(metrics, "faithfulness")),
            groundedness_score=_metric_decimal(_find_metric(metrics, "groundedness")),
            citation_coverage=_metric_decimal(_find_metric(metrics, "citation_coverage")),
            latency_ms=None,
            generation_provider=None,
            generation_model=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            estimated_cost_usd=None,
            generation_latency_ms=None,
            latency_breakdown_json=_latency_breakdown_json(None),
            metric_summary_json=_metric_summary_json(
                metrics,
                case=case,
                target=target,
                answer_generated=False,
            ),
            error_code="internal_error",
            error_detail_code=None,
            error_message=redact_error_message("Evaluation case failed."),
        )
        self.repository.save_results(
            db,
            evaluation_run_item_id=item.evaluation_run_item_id,
            results=[
                _result_input(metric, strategy_type=target.storage_strategy_type, target=target)
                for metric in metrics
            ],
        )

    def _summary(self, db: Session, run: EvaluationRun) -> EvaluationRunSummary:
        items = self.repository.list_items(db, evaluation_run_id=run.evaluation_run_id)
        results_by_item = self.repository.list_results(
            db,
            evaluation_run_item_ids=[item.evaluation_run_item_id for item in items],
        )
        return self._summary_from_loaded(db, run, items, results_by_item)

    def _summary_from_loaded(
        self,
        db: Session,
        run: EvaluationRun,
        items: list[EvaluationRunItem],
        results_by_item: dict[int, list[EvaluationResult]],
    ) -> EvaluationRunSummary:
        config = _config(run)
        metric_summary = _metric_summary(items, results_by_item)
        strategy_comparison = _strategy_comparison(items, results_by_item)
        generation_summary = _generation_summary(items)
        job = self.repository.find_job_for_run(db, evaluation_run_id=run.evaluation_run_id)
        planned_item_count = (
            self._planned_item_count(db, run) if run.status in {"queued", "running"} else 0
        )
        case_count = max(len(items), planned_item_count)
        answered_count = sum(
            1 for item in items if item.answer_outcome == EvaluationAnswerOutcome.ANSWERED.value
        )
        abstained_count = sum(
            1 for item in items if item.answer_outcome == EvaluationAnswerOutcome.ABSTAINED.value
        )
        pipeline_failed_count = sum(
            1
            for item in items
            if item.answer_outcome
            in {
                EvaluationAnswerOutcome.NO_CONTEXT.value,
                EvaluationAnswerOutcome.CITATION_ERROR.value,
                EvaluationAnswerOutcome.GENERATION_ERROR.value,
                EvaluationAnswerOutcome.RETRIEVAL_ERROR.value,
            }
        )
        applicable_count = answered_count + abstained_count
        judgments = self.repository.list_auxiliary_judgments(
            db, evaluation_run_id=run.evaluation_run_id
        )
        succeeded_judgments = [judgment for judgment in judgments if judgment.status == "succeeded"]
        calibrations = self.repository.list_human_calibrations(
            db, evaluation_run_id=run.evaluation_run_id
        )
        judged_count = len(succeeded_judgments)
        reviewed_count = len(calibrations)
        provisional_rate = _boolean_rate(
            judgment.auxiliary_pass for judgment in succeeded_judgments
        )
        calibrated_rate = (
            _boolean_rate(calibration.human_pass for calibration in calibrations)
            if applicable_count > 0 and reviewed_count == applicable_count
            else None
        )
        quality_status = _evaluation_quality_status(
            run_status=run.status,
            evaluation_scope=cast(EvaluationScope, config["evaluation_scope"]),
            applicable_count=applicable_count,
            judged_count=judged_count,
            reviewed_count=reviewed_count,
            calibrated_rate=calibrated_rate,
        )
        return EvaluationRunSummary(
            evaluation_run_id=run.evaluation_run_id,
            job_id=job.job_id if job is not None else None,
            evaluation_dataset_id=run.evaluation_dataset_id,
            dataset_name=cast(str, config["dataset_name"]),
            strategy_type=cast(RetrievalStrategy, run.strategy_type),
            strategies=_strategy_values(config),
            metric_names=cast(list[str], config["metrics"]),
            evaluation_scope=cast(EvaluationScope, config["evaluation_scope"]),
            trigger_type=run.trigger_type,
            status=cast(EvaluationStatus, run.status),
            case_count=case_count,
            succeeded_count=sum(1 for item in items if item.status == "succeeded"),
            failed_count=sum(1 for item in items if item.status == "failed"),
            answered_count=answered_count,
            abstained_count=abstained_count,
            pipeline_failed_count=pipeline_failed_count,
            judged_count=judged_count,
            reviewed_count=reviewed_count,
            answer_coverage=_coverage(answered_count + abstained_count, case_count),
            judge_coverage=_coverage(judged_count, applicable_count),
            review_coverage=_coverage(reviewed_count, applicable_count),
            grounded_answer_pass_rate_provisional=provisional_rate,
            grounded_answer_pass_rate_calibrated=calibrated_rate,
            quality_status=quality_status,
            corpus_fingerprint=run.corpus_fingerprint,
            metric_summary=metric_summary,
            strategy_comparison=strategy_comparison,
            strategy_metrics_summary_json=run.strategy_metrics_summary_json,
            total_estimated_cost_usd=generation_summary.total_estimated_cost_usd,
            total_input_tokens=generation_summary.total_input_tokens,
            total_output_tokens=generation_summary.total_output_tokens,
            total_tokens=generation_summary.total_tokens,
            avg_generation_latency_ms=generation_summary.avg_generation_latency_ms,
            generation_providers=generation_summary.generation_providers,
            generation_models=generation_summary.generation_models,
            requested_generation_provider=cast(str | None, config["generation_provider"]),
            requested_generation_model=cast(str | None, config["generation_model"]),
            error_code=run.error_code,
            error_message=redact_error_message(run.error_message) if run.error_message else None,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _item_response(
        self,
        item: EvaluationRunItem,
        results: list[EvaluationResult],
    ) -> EvaluationRunItemResponse:
        metric_by_name = {result.metric_name: result for result in results}
        answer_generated = _item_answer_generated(item, metric_by_name)
        metric_results = [_metric_response(result, answer_generated) for result in results]
        context_precision = next(
            (
                result.metric_score
                for result in results
                if result.metric_name == "context_precision"
            ),
            None,
        )
        case_id = (
            next(
                (
                    str(result.details_json.get("case_id"))
                    for result in results
                    if result.metric_name == "case_metadata"
                    and isinstance(result.details_json, dict)
                    and result.details_json.get("case_id")
                ),
                None,
            )
            or item.case_key
        )
        return EvaluationRunItemResponse(
            evaluation_run_item_id=item.evaluation_run_item_id,
            evaluation_case_id=item.evaluation_case_id,
            retrieval_run_id=item.retrieval_run_id,
            strategy_type=cast(RetrievalStrategy, item.strategy_type),
            status=cast(EvaluationStatus, item.status),
            answer_outcome=cast(EvaluationAnswerOutcome | None, item.answer_outcome),
            faithfulness_score=_decimal_float(item.faithfulness_score)
            if answer_generated
            else None,
            groundedness_score=_decimal_float(item.groundedness_score)
            if answer_generated
            else None,
            citation_coverage=_decimal_float(item.citation_coverage) if answer_generated else None,
            context_precision=_decimal_float(context_precision),
            latency_ms=item.latency_ms,
            generation_provider=_safe_optional_generation_label(
                item.generation_provider,
                max_length=50,
            ),
            generation_model=_safe_optional_generation_label(
                item.generation_model,
                max_length=128,
            ),
            input_tokens=item.input_tokens,
            output_tokens=item.output_tokens,
            total_tokens=item.total_tokens,
            estimated_cost_usd=_decimal_float(item.estimated_cost_usd),
            generation_latency_ms=item.generation_latency_ms,
            latency_breakdown_json=item.latency_breakdown_json,
            metric_summary_json=_item_metric_summary_json(
                item.metric_summary_json, answer_generated=answer_generated
            ),
            error_code=item.error_code,
            error_detail_code=item.error_detail_code,
            error_message=redact_error_message(item.error_message) if item.error_message else None,
            case_id=case_id,
            case_key=item.case_key,
            metrics=metric_results,
        )

    def _agentic_metrics(
        self,
        db: Session,
        *,
        strategy_type: RetrievalStrategy,
        case_metadata_json: dict[str, object] | None,
        rag_result: RagEvaluationResult,
    ) -> list[MetricValue]:
        if strategy_type not in {
            RetrievalStrategy.AGENTIC_ROUTER,
            RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
            RetrievalStrategy.LANGCHAIN_AGENTIC,
            RetrievalStrategy.LANGGRAPH_AGENTIC,
        }:
            return []
        retrieval_run = (
            db.get(RetrievalRun, rag_result.retrieval_run_id)
            if rag_result.retrieval_run_id is not None
            else None
        )
        decision = _dict_or_empty(
            retrieval_run.strategy_decision_json if retrieval_run is not None else None
        )
        score_summary = _dict_or_empty(
            retrieval_run.retrieval_score_summary if retrieval_run is not None else None
        )
        expected_strategy, acceptable_strategies = _expected_strategy_hints(case_metadata_json)
        selected_strategy = _safe_strategy_value(
            decision.get("selected_strategy") or score_summary.get("selected_strategy")
        )
        execution_strategy = _safe_strategy_value(
            decision.get("execution_strategy") or score_summary.get("execution_strategy")
        )
        accuracy: float | None = None
        accuracy_label = "not_applicable"
        not_applicable = True
        has_strategy_decision = selected_strategy is not None
        if (expected_strategy or acceptable_strategies) and has_strategy_decision:
            accepted = set(acceptable_strategies)
            if expected_strategy:
                accepted.add(expected_strategy)
            accuracy = 1.0 if selected_strategy in accepted else 0.0
            accuracy_label = "correct" if accuracy == 1.0 else "incorrect"
            not_applicable = False

        fallback_used = _first_bool(
            decision.get("fallback_used"),
            score_summary.get("fallback_used"),
        )
        budget_exhausted = _first_bool(
            decision.get("budget_exhausted"),
            score_summary.get("budget_exhausted"),
        )
        sufficiency_score = _float_or_none(
            decision.get("sufficiency_score") or score_summary.get("sufficiency_score")
        )
        retrieval_call_count = _float_or_none(
            decision.get("retrieval_call_count") or score_summary.get("retrieval_call_count")
        )
        fallback_count = 1 if fallback_used else 0

        return [
            MetricValue(
                metric_name="strategy_selection_accuracy",
                metric_score=accuracy,
                metric_label=accuracy_label,
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "not_applicable": not_applicable,
                    "expected_strategy": expected_strategy,
                    "acceptable_strategies": acceptable_strategies,
                    "selected_strategy": selected_strategy,
                    "execution_strategy": execution_strategy,
                },
            ),
            MetricValue(
                metric_name="fallback_rate",
                metric_score=1.0 if fallback_used else 0.0,
                metric_label="used" if fallback_used else "not_used",
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "fallback_used": fallback_used,
                    "fallback_strategy": _safe_strategy_value(decision.get("fallback_strategy")),
                    "fallback_reason": _safe_reason(decision.get("fallback_reason")),
                    "execution_strategy": execution_strategy,
                },
            ),
            MetricValue(
                metric_name="budget_exhausted_rate",
                metric_score=1.0 if budget_exhausted else 0.0,
                metric_label="exhausted" if budget_exhausted else "available",
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "budget_exhausted": budget_exhausted,
                    "retrieval_call_count": retrieval_call_count,
                    "max_retrieval_calls": _float_or_none(
                        decision.get("max_retrieval_calls")
                        or score_summary.get("max_retrieval_calls")
                    ),
                },
            ),
            MetricValue(
                metric_name="sufficiency_score_avg",
                metric_score=sufficiency_score,
                metric_label=_metric_label(sufficiency_score),
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "sufficiency_score": sufficiency_score,
                    "sufficient": bool(decision.get("sufficient"))
                    if "sufficient" in decision
                    else None,
                    "sufficiency_reason_codes": _string_values(
                        decision.get("sufficiency_reason_codes")
                    ),
                },
            ),
            MetricValue(
                metric_name="retrieval_call_count_avg",
                metric_score=None,
                metric_value=retrieval_call_count,
                metric_label="count" if retrieval_call_count is not None else "not_applicable",
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "retrieval_call_count": retrieval_call_count,
                    "fallback_count": fallback_count,
                },
            ),
        ]

    def _graph_metrics(
        self,
        db: Session,
        *,
        case: EvaluationCase,
        case_metadata_json: dict[str, object] | None,
        target: EvaluationStrategyTarget,
        rag_result: RagEvaluationResult,
    ) -> list[MetricValue]:
        if target.retrieval_strategy != RetrievalStrategy.GRAPH:
            return _not_applicable_graph_metrics(
                target=target,
                reason_code="not_graph_strategy",
            )
        retrieval_run = (
            db.get(RetrievalRun, rag_result.retrieval_run_id)
            if rag_result.retrieval_run_id is not None
            else None
        )
        score_summary = _dict_or_empty(
            retrieval_run.retrieval_score_summary if retrieval_run is not None else None
        )
        provider = _safe_graph_provider(
            target.graph_store_provider or score_summary.get("graph_store_provider") or "postgres"
        )
        reason_codes = _string_values(score_summary.get("graph_reason_codes"))
        if _is_graph_provider_unavailable(reason_codes):
            return _not_applicable_graph_metrics(
                target=target,
                reason_code=_provider_skip_reason(reason_codes),
                provider=provider,
                reason_codes=reason_codes,
            )
        graph_paths = _filter_graph_paths_for_source_chunk_ids(
            _list_graph_paths(db, retrieval_run_id=rag_result.retrieval_run_id),
            _selected_retrieval_source_chunk_ids(
                db,
                retrieval_run_id=rag_result.retrieval_run_id,
            ),
        )
        relevance = _graph_path_relevance_metric(
            graph_paths=graph_paths,
            metadata_json=case_metadata_json,
            provider=provider,
            reason_codes=reason_codes,
        )
        citation = _graph_citation_coverage_metric(
            db,
            graph_paths=graph_paths,
            retrieval_run_id=rag_result.retrieval_run_id,
            provider=provider,
        )
        answerability = _multi_hop_answerability_metric(
            graph_paths=graph_paths,
            metadata_json=case_metadata_json,
            retrieval_summary=rag_result.retrieval_score_summary,
            provider=provider,
        )
        quality_summary = _entity_relation_quality_metric(
            graph_paths=graph_paths,
            provider=provider,
            score_summary=score_summary,
            case=case,
        )
        return [relevance, citation, answerability, quality_summary]

    def _retrieval_trace_metrics(
        self,
        db: Session,
        *,
        target: EvaluationStrategyTarget,
        rag_result: RagEvaluationResult,
    ) -> list[MetricValue]:
        if target.retrieval_strategy in {
            RetrievalStrategy.AGENTIC_ROUTER,
            RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
            RetrievalStrategy.LANGCHAIN_AGENTIC,
            RetrievalStrategy.LANGGRAPH_AGENTIC,
        }:
            return []
        retrieval_run = (
            db.get(RetrievalRun, rag_result.retrieval_run_id)
            if rag_result.retrieval_run_id is not None
            else None
        )
        decision = _dict_or_empty(
            retrieval_run.strategy_decision_json if retrieval_run is not None else None
        )
        score_summary = _dict_or_empty(
            retrieval_run.retrieval_score_summary if retrieval_run is not None else None
        )
        fallback_used = _first_bool(
            decision.get("fallback_used"),
            score_summary.get("fallback_used"),
            score_summary.get("graph_fallback_used"),
        )
        reason_codes = _string_values(score_summary.get("graph_reason_codes"))
        if _is_graph_provider_unavailable(reason_codes):
            reason_code = _provider_skip_reason(reason_codes)
            return [
                MetricValue(
                    metric_name="fallback_rate",
                    metric_score=None,
                    metric_label="not_applicable",
                    details={
                        "schema_version": EVALUATION_SCHEMA_VERSION,
                        "not_applicable": True,
                        "reason_code": reason_code,
                        "reason_codes": reason_codes or [reason_code],
                        "graph_store_provider": _safe_graph_provider(
                            target.graph_store_provider or score_summary.get("graph_store_provider")
                        ),
                    },
                )
            ]
        return [
            MetricValue(
                metric_name="fallback_rate",
                metric_score=1.0 if fallback_used else 0.0,
                metric_label="used" if fallback_used else "not_used",
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "fallback_used": fallback_used,
                    "fallback_strategy": _safe_strategy_value(decision.get("fallback_strategy")),
                    "fallback_reason": _safe_reason(decision.get("fallback_reason")),
                    "graph_store_provider": _safe_graph_provider(
                        target.graph_store_provider or score_summary.get("graph_store_provider")
                    ),
                },
            )
        ]

    def _cache_metrics(
        self,
        db: Session,
        *,
        target: EvaluationStrategyTarget,
        rag_result: RagEvaluationResult,
        latency_ms: int,
        baseline_latency_ms: int | None,
    ) -> list[MetricValue]:
        if not _is_cacheable_target(target):
            return _not_applicable_cache_metrics(
                target=target, reason_code="strategy_not_cacheable"
            )
        retrieval_run = (
            db.get(RetrievalRun, rag_result.retrieval_run_id)
            if rag_result.retrieval_run_id is not None
            else None
        )
        cache_summary = _dict_or_empty(
            retrieval_run.cache_summary_json if retrieval_run is not None else None
        )
        status = _safe_cache_status(cache_summary.get("status"))
        reason = _safe_reason(cache_summary.get("reason"))
        hit_rate = 1.0 if status == "hit" else 0.0
        saved_latency = (
            max(0, baseline_latency_ms - latency_ms)
            if status == "hit" and baseline_latency_ms is not None
            else None
        )
        return [
            MetricValue(
                metric_name="cache_hit_rate",
                metric_score=hit_rate,
                metric_label=status,
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "cache_mode": target.cache_mode.value,
                    "cache_status": status,
                    "cache_reason": reason,
                    "cache_enabled": bool(cache_summary.get("enabled", False)),
                },
            ),
            MetricValue(
                metric_name="cache_saved_latency",
                metric_score=None,
                metric_value=float(saved_latency) if saved_latency is not None else None,
                metric_label="ms" if saved_latency is not None else "not_applicable",
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "cache_mode": target.cache_mode.value,
                    "cache_status": status,
                    "baseline_latency_available": baseline_latency_ms is not None,
                    "sample_latency_ms": latency_ms,
                },
            ),
        ]

    def _failure_candidates(
        self,
        db: Session,
        *,
        run: EvaluationRun,
    ) -> list[EvaluationFailureCandidate]:
        items = self.repository.list_items(db, evaluation_run_id=run.evaluation_run_id)
        if not items:
            return []
        results_by_item = self.repository.list_results(
            db,
            evaluation_run_item_ids=[item.evaluation_run_item_id for item in items],
        )
        source_cases = self._promotion_source_cases(db, run)
        candidates: list[EvaluationFailureCandidate] = []
        for item in items:
            source_case = source_cases.get(item.evaluation_run_item_id)
            results = results_by_item.get(item.evaluation_run_item_id, [])
            stored_metric_by_name = {result.metric_name: result for result in results}
            metric_by_name = _applicable_metric_results(item, stored_metric_by_name)
            if _is_graph_provider_skip_item(item, metric_by_name):
                continue
            metric_snapshot = _metric_snapshot(metric_by_name)
            case_metadata = _case_metadata_details(metric_by_name)
            item_case_snapshot = _item_case_snapshot(item)
            target_metadata = _item_target_metadata(item)
            comparison_label = _metadata_comparison_label(target_metadata)
            if comparison_label is not None:
                metric_snapshot["evaluation_strategy_label"] = comparison_label
            graph_provider = _metadata_graph_provider(target_metadata)
            if graph_provider is not None:
                metric_snapshot["graph_store_provider"] = graph_provider
            cache_mode = _metadata_cache_mode(target_metadata)
            if cache_mode is not None:
                metric_snapshot["cache_mode"] = cache_mode.value
            case_snapshot_hash = _safe_hash_value(
                case_metadata.get("case_snapshot_hash")
            ) or _safe_hash_value(item_case_snapshot.get("case_snapshot_hash"))
            if case_snapshot_hash is not None:
                metric_snapshot["case_snapshot_hash"] = case_snapshot_hash
            question_hash = (
                _safe_hash_value(case_metadata.get("question_hash"))
                or _safe_hash_value(item_case_snapshot.get("question_hash"))
                or _question_hash(source_case.question if source_case else item.case_key)
            )
            case_key = source_case.case_key if source_case is not None else item.case_key

            for failure_type, severity, reason_codes in _failure_reasons(
                item,
                metric_by_name,
                self.settings,
            ):
                promotion_key = _promotion_key(
                    run=run,
                    item=item,
                    case_key=case_key,
                    question_hash=question_hash,
                    failure_type=failure_type,
                    target_metadata=target_metadata,
                )
                candidates.append(
                    EvaluationFailureCandidate(
                        evaluation_run_id=run.evaluation_run_id,
                        evaluation_run_item_id=item.evaluation_run_item_id,
                        evaluation_case_id=item.evaluation_case_id,
                        case_key=case_key,
                        question_hash=question_hash,
                        strategy_type=cast(RetrievalStrategy, item.strategy_type),
                        failure_type=failure_type,
                        severity=severity,
                        failure_reason_codes=reason_codes,
                        metric_snapshot=metric_snapshot,
                        recommended_tags=[
                            "failure_promoted",
                            failure_type,
                            f"strategy_{item.strategy_type}",
                            *_target_failure_tags(target_metadata),
                        ],
                        promotion_key=promotion_key,
                    )
                )
        return candidates

    def _promotion_source_cases(
        self,
        db: Session,
        run: EvaluationRun,
    ) -> dict[int, PromotionSourceCase]:
        items = self.repository.list_items(db, evaluation_run_id=run.evaluation_run_id)
        source_cases: dict[int, PromotionSourceCase] = {}
        fixture_by_key: dict[str, EvaluationCase] = {}
        if run.evaluation_dataset_id is None:
            try:
                fixture_by_key = {
                    case.case_id: case
                    for case in load_evaluation_cases(
                        cast(str, _config(run)["dataset_name"]),
                        case_limit=cast(int | None, _config(run)["case_limit"]),
                    )
                }
            except EvaluationFixtureError:
                fixture_by_key = {}

        for item in items:
            source: PromotionSourceCase | None = None
            if item.evaluation_case_id is not None:
                model = db.get(EvaluationCaseModel, item.evaluation_case_id)
                if model is not None:
                    source = PromotionSourceCase(
                        evaluation_case_id=model.evaluation_case_id,
                        case_key=model.case_key,
                        question=model.question,
                        expected_answer=model.expected_answer,
                        expected_keywords=_string_list(model.expected_keywords),
                        expected_document_ids=_int_list(model.expected_document_ids),
                        expected_chunk_ids=_int_list(model.expected_chunk_ids),
                        required_citation=model.required_citation,
                        tags=_string_list(model.tags),
                        metadata_json=model.metadata_json,
                    )
            elif item.case_key is not None and item.case_key in fixture_by_key:
                fixture = fixture_by_key[item.case_key]
                source = PromotionSourceCase(
                    evaluation_case_id=None,
                    case_key=fixture.case_id,
                    question=fixture.question,
                    expected_answer=fixture.expected_answer,
                    expected_keywords=list(fixture.expected_keywords),
                    expected_document_ids=list(fixture.expected_document_ids),
                    expected_chunk_ids=list(fixture.expected_chunk_ids),
                    required_citation=fixture.required_citation,
                    tags=list(fixture.tags),
                    metadata_json=fixture.metadata_json,
                )
            if source is not None:
                source_cases[item.evaluation_run_item_id] = source
        return source_cases

    def _require_run(self, db: Session, evaluation_run_id: int) -> EvaluationRun:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise EvaluationFixtureError("evaluation_run_not_found")
        return run

    def _load_cases_for_run(
        self,
        db: Session,
        run: EvaluationRun,
    ) -> list[LoadedEvaluationCase]:
        config = _config(run)
        case_limit = cast(int | None, config["case_limit"])
        if run.evaluation_dataset_id is not None:
            cases, _ = self.repository.list_cases(
                db,
                evaluation_dataset_id=run.evaluation_dataset_id,
                offset=0,
                limit=case_limit,
                status="active",
            )
            if not cases:
                raise EvaluationFixtureError("evaluation_dataset_empty")
            return [_loaded_case_from_model(case) for case in cases]

        fixture_cases = load_evaluation_cases(
            cast(str, config["dataset_name"]),
            case_limit=case_limit,
        )
        return [
            LoadedEvaluationCase(
                case=case,
                evaluation_case_id=None,
                case_key=case.case_id,
                metadata_json=case.metadata_json,
                tags=list(case.tags),
            )
            for case in fixture_cases
        ]

    def _planned_case_count(self, db: Session, run: EvaluationRun) -> int:
        config = _config(run)
        case_limit = cast(int | None, config["case_limit"])
        if run.evaluation_dataset_id is not None:
            count = self.repository.count_cases(
                db,
                evaluation_dataset_id=run.evaluation_dataset_id,
                status="active",
            )
            return min(count, case_limit) if case_limit is not None else count
        return _fixture_planned_case_count(run)

    def _planned_item_count(self, db: Session, run: EvaluationRun) -> int:
        config = _config(run)
        return self._planned_case_count(db, run) * len(_strategy_values(config))

    def _dataset_response(
        self, db: Session, dataset: EvaluationDataset
    ) -> EvaluationDatasetResponse:
        return EvaluationDatasetResponse(
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            dataset_name=dataset.dataset_name,
            description=dataset.description,
            version=dataset.version,
            source_type=dataset.source_type,
            status=dataset.status,
            manifest_schema_version=getattr(
                dataset,
                "manifest_schema_version",
                DATASET_MANIFEST_SCHEMA_VERSION,
            ),
            content_fingerprint=getattr(dataset, "content_fingerprint", None),
            corpus_fingerprint=getattr(dataset, "corpus_fingerprint", None),
            corpus_mode=getattr(dataset, "corpus_mode", "shared_legacy"),
            corpus_status=getattr(
                dataset,
                "corpus_status",
                "shared_legacy",
            ),
            corpus_failure_code=getattr(
                dataset,
                "corpus_failure_code",
                None,
            ),
            metadata_json=dataset.metadata_json,
            case_count=self.repository.count_cases(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
            ),
            created_by=dataset.created_by,
            created_at=dataset.created_at,
            updated_at=dataset.updated_at,
        )

    def _case_response(self, case: EvaluationCaseModel) -> EvaluationCaseResponse:
        return EvaluationCaseResponse(
            evaluation_case_id=case.evaluation_case_id,
            evaluation_dataset_id=case.evaluation_dataset_id,
            case_key=case.case_key,
            question=case.question,
            expected_answer=case.expected_answer,
            expected_keywords=_string_list(case.expected_keywords),
            expected_document_ids=_int_list(case.expected_document_ids),
            expected_chunk_ids=_int_list(case.expected_chunk_ids),
            required_citation=case.required_citation,
            tags=_string_list(case.tags),
            metadata_json=case.metadata_json,
            status=case.status,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )

    def _case_spec(self, case: EvaluationCaseModel) -> EvaluationCaseSpec:
        return EvaluationCaseSpec(
            case_key=case.case_key,
            question=case.question,
            expected_answer=case.expected_answer,
            expected_keywords=_string_list(case.expected_keywords),
            expected_document_ids=_int_list(case.expected_document_ids),
            expected_chunk_ids=_int_list(case.expected_chunk_ids),
            required_citation=case.required_citation,
            tags=_string_list(case.tags),
            metadata_json=case.metadata_json,
            status=case.status,
        )


def _config(run: EvaluationRun) -> dict[str, object]:
    config = run.metrics_config or {}
    retrieval_settings = run.retrieval_settings_json or {}
    dataset_name = config.get("dataset_name")
    evaluation_dataset_id = config.get("evaluation_dataset_id")
    case_limit = config.get("case_limit")
    strategy_type = config.get("strategy_type") or run.strategy_type
    raw_strategies = config.get("strategies")
    raw_metrics = config.get("metrics")
    raw_cache_modes = config.get("cache_modes")
    raw_evaluation_scope = config.get("evaluation_scope")
    raw_strategy_targets = config.get("strategy_targets")
    top_k = config.get("top_k")
    rerank_top_n = config.get("rerank_top_n")
    generation_provider = _requested_generation_provider(config.get("generation_provider"))
    generation_model = _requested_generation_model(config.get("generation_model"))
    trigger_type = config.get("trigger_type") or run.trigger_type
    raw_logical_document_ids = retrieval_settings.get("logical_document_ids")
    logical_document_ids = (
        list(
            dict.fromkeys(
                value
                for value in raw_logical_document_ids
                if isinstance(value, int) and not isinstance(value, bool) and value > 0
            )
        )
        if isinstance(raw_logical_document_ids, list)
        else []
    )
    raw_corpus_fingerprint = retrieval_settings.get("corpus_fingerprint")
    corpus_fingerprint = (
        raw_corpus_fingerprint
        if isinstance(raw_corpus_fingerprint, str) and raw_corpus_fingerprint
        else run.corpus_fingerprint
    )
    strategies = (
        [str(strategy) for strategy in raw_strategies if isinstance(strategy, str)]
        if isinstance(raw_strategies, list)
        else []
    )
    if not strategies and isinstance(strategy_type, str):
        strategies = [strategy_type]
    metrics = (
        [str(metric) for metric in raw_metrics if isinstance(metric, str)]
        if isinstance(raw_metrics, list)
        else [metric.value for metric in DEFAULT_EVALUATION_METRICS]
    )
    cache_modes = (
        [str(mode) for mode in raw_cache_modes if isinstance(mode, str)]
        if isinstance(raw_cache_modes, list)
        else [EvaluationCacheMode.DEFAULT.value]
    )
    strategy_targets = (
        [target for target in raw_strategy_targets if isinstance(target, dict)]
        if isinstance(raw_strategy_targets, list)
        else []
    )
    evaluation_scope: EvaluationScope = (
        cast(EvaluationScope, raw_evaluation_scope)
        if raw_evaluation_scope in {"retrieval", "answer", "end_to_end"}
        else _evaluation_scope_from_strategy_labels(strategies)
    )
    return {
        "dataset_name": dataset_name if isinstance(dataset_name, str) else "phase1_smoke",
        "evaluation_dataset_id": (
            evaluation_dataset_id if isinstance(evaluation_dataset_id, int) else None
        ),
        "case_limit": case_limit if isinstance(case_limit, int) else None,
        "strategy_type": strategy_type if isinstance(strategy_type, str) else "dense",
        "strategies": strategies,
        "metrics": metrics,
        "cache_modes": cache_modes,
        "evaluation_scope": evaluation_scope,
        "strategy_targets": strategy_targets,
        "top_k": top_k if isinstance(top_k, int) else None,
        "rerank_top_n": rerank_top_n if isinstance(rerank_top_n, int) else None,
        "generation_provider": generation_provider,
        "generation_model": generation_model,
        "trigger_type": trigger_type if isinstance(trigger_type, str) else "manual",
        "logical_document_ids": logical_document_ids,
        "corpus_fingerprint": corpus_fingerprint,
    }


def _factory_accepts_generation_selection(
    factory: Callable[..., EvaluationRagService],
) -> bool:
    try:
        parameters = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return True
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return True
    return "generation_provider" in parameters and "generation_model" in parameters


def _requested_generation_provider(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    provider = _safe_optional_generation_label(value.lower(), max_length=50)
    if provider is None or provider not in KNOWN_GENERATION_PROVIDERS:
        return None
    return provider


def _callable_accepts_parameter(
    callback: Callable[..., object],
    parameter_name: str,
) -> bool:
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return True
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return True
    return parameter_name in parameters


def _call_evaluation_runner(
    callback: Callable[..., RagEvaluationResult],
    db: Session,
    *,
    kwargs: dict[str, object],
    logical_document_ids: Sequence[int],
    corpus_fingerprint: str | None = None,
) -> RagEvaluationResult:
    if logical_document_ids and _callable_accepts_parameter(callback, "logical_document_ids"):
        kwargs["logical_document_ids"] = list(logical_document_ids)
    if corpus_fingerprint and _callable_accepts_parameter(callback, "corpus_fingerprint"):
        kwargs["corpus_fingerprint"] = corpus_fingerprint
    return callback(db, **kwargs)


def _requested_generation_model(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    model = value.strip()
    if not model or model in {"redacted", "unknown"}:
        return None
    if len(model) > 128:
        return None
    if not _REQUESTED_GENERATION_MODEL_RE.fullmatch(model):
        return None
    if _REQUESTED_GENERATION_MODEL_SECRET_RE.search(model):
        return None
    return model


def _fixture_planned_case_count(run: EvaluationRun) -> int:
    config = _config(run)
    try:
        return len(
            load_evaluation_cases(
                cast(str, config["dataset_name"]),
                case_limit=cast(int | None, config["case_limit"]),
            )
        )
    except EvaluationFixtureError:
        return 0


def _result_input(
    metric: MetricValue,
    *,
    strategy_type: str,
    target: EvaluationStrategyTarget,
) -> EvaluationResultInput:
    detail = _metric_detail_with_target(metric.details, target=target)
    return EvaluationResultInput(
        metric_name=metric.metric_name,
        metric_score=_decimal_score(metric.metric_score),
        metric_value=_decimal_metric_value(
            metric.metric_value if metric.metric_value is not None else metric.metric_score
        ),
        metric_label=metric.metric_label,
        details_json=detail,
        metric_detail_json=detail,
        strategy_type=strategy_type,
    )


def _metric_decimal(metric: MetricValue | None) -> Decimal | None:
    if metric is None:
        return None
    return _decimal_score(metric.metric_score)


def _decimal_score(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 6))).quantize(SCORE_QUANT, rounding=ROUND_HALF_UP)


def _decimal_cost_usd(value: float | None) -> Decimal | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return Decimal(str(round(number, 6))).quantize(SCORE_QUANT, rounding=ROUND_HALF_UP)


def _decimal_metric_value(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 6))).quantize(SCORE_QUANT, rounding=ROUND_HALF_UP)


def _decimal_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _coverage(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _boolean_rate(values: Iterable[bool | None]) -> float | None:
    applicable = [value for value in values if isinstance(value, bool)]
    if not applicable:
        return None
    return round(sum(1 for value in applicable if value) / len(applicable), 6)


def _evaluation_quality_status(
    *,
    run_status: str,
    evaluation_scope: EvaluationScope,
    applicable_count: int,
    judged_count: int,
    reviewed_count: int,
    calibrated_rate: float | None,
) -> EvaluationQualityStatus:
    if evaluation_scope != "end_to_end":
        return EvaluationQualityStatus.NOT_APPLICABLE
    if run_status in {"queued", "running"}:
        return EvaluationQualityStatus.PENDING
    if applicable_count <= 0 or judged_count < applicable_count:
        return EvaluationQualityStatus.PARTIAL
    if reviewed_count < applicable_count:
        return EvaluationQualityStatus.CALIBRATION_REQUIRED
    return (
        EvaluationQualityStatus.PASSED if calibrated_rate == 1.0 else EvaluationQualityStatus.FAILED
    )


def _resolved_answer_outcome(rag_result: RagEvaluationResult) -> str | None:
    if rag_result.answer_outcome is not None:
        return rag_result.answer_outcome
    if rag_result.status == "succeeded":
        return EvaluationAnswerOutcome.ANSWERED.value if rag_result.answer_text.strip() else None
    if rag_result.error_code == "no_context_found":
        return EvaluationAnswerOutcome.NO_CONTEXT.value
    if rag_result.error_code == "citation_build_failed":
        return EvaluationAnswerOutcome.CITATION_ERROR.value
    if rag_result.error_code == "generation_failed":
        return EvaluationAnswerOutcome.GENERATION_ERROR.value
    return EvaluationAnswerOutcome.RETRIEVAL_ERROR.value


def _non_negative_int_or_none(value: int | None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _safe_optional_generation_label(value: str | None, *, max_length: int) -> str | None:
    if value is None or not value.strip():
        return None
    return _safe_generation_label(value, max_length=max_length)


def _generation_summary(items: list[EvaluationRunItem]) -> EvaluationGenerationSummary:
    succeeded_items = [item for item in items if item.status == "succeeded"]
    cost_values = [
        item.estimated_cost_usd for item in succeeded_items if item.estimated_cost_usd is not None
    ]
    input_tokens = [item.input_tokens for item in succeeded_items if item.input_tokens is not None]
    output_tokens = [
        item.output_tokens for item in succeeded_items if item.output_tokens is not None
    ]
    total_tokens = [item.total_tokens for item in succeeded_items if item.total_tokens is not None]
    generation_latencies = [
        item.generation_latency_ms
        for item in succeeded_items
        if item.generation_latency_ms is not None
    ]
    return EvaluationGenerationSummary(
        total_estimated_cost_usd=round(float(sum(cost_values, Decimal("0"))), 6)
        if cost_values
        else None,
        total_input_tokens=sum(input_tokens) if input_tokens else None,
        total_output_tokens=sum(output_tokens) if output_tokens else None,
        total_tokens=sum(total_tokens) if total_tokens else None,
        avg_generation_latency_ms=round(
            sum(generation_latencies) / len(generation_latencies),
            3,
        )
        if generation_latencies
        else None,
        generation_providers=_distinct_generation_labels(
            (item.generation_provider for item in succeeded_items),
            max_length=50,
        ),
        generation_models=_distinct_generation_labels(
            (item.generation_model for item in succeeded_items),
            max_length=128,
        ),
    )


def _distinct_generation_labels(values: Iterable[str | None], *, max_length: int) -> list[str]:
    labels = {
        label
        for value in values
        if isinstance(value, str)
        for label in [_safe_optional_generation_label(value, max_length=max_length)]
        if label is not None
    }
    return sorted(labels)


def _metric_response(
    result: EvaluationResult,
    answer_generated: bool,
) -> EvaluationMetricResult:
    if result.metric_name in ANSWER_GENERATION_DEPENDENT_METRICS and not answer_generated:
        details = dict(result.details_json or {})
        details.update(
            {
                "not_applicable": True,
                "reason_code": "answer_not_generated",
            }
        )
        return EvaluationMetricResult(
            metric_name=result.metric_name,
            metric_score=None,
            metric_value=None,
            metric_label="not_applicable",
            details=details,
            metric_detail_json=details,
            strategy_type=cast(RetrievalStrategy, result.strategy_type),
        )
    return EvaluationMetricResult(
        metric_name=result.metric_name,
        metric_score=_decimal_float(result.metric_score),
        metric_value=_decimal_float(result.metric_value),
        metric_label=result.metric_label,
        details=result.details_json,
        metric_detail_json=result.metric_detail_json,
        strategy_type=cast(RetrievalStrategy, result.strategy_type),
    )


def _item_metric_summary_json(
    payload: dict[str, object] | None,
    *,
    answer_generated: bool,
) -> dict[str, object] | None:
    if payload is None or answer_generated:
        return payload
    normalized = dict(payload)
    raw_metrics = normalized.get("metrics")
    if isinstance(raw_metrics, dict):
        normalized["metrics"] = {
            name: value
            for name, value in raw_metrics.items()
            if name not in ANSWER_GENERATION_DEPENDENT_METRICS
        }
    normalized["answer_metrics_not_applicable_reason"] = "answer_not_generated"
    return normalized


def _find_metric(metrics: list[MetricValue], name: str) -> MetricValue | None:
    return next((metric for metric in metrics if metric.metric_name == name), None)


def _filter_metrics(metrics: list[MetricValue], requested_metrics: set[str]) -> list[MetricValue]:
    return [metric for metric in metrics if metric.metric_name in requested_metrics]


def _replace_metrics(base: list[MetricValue], replacements: list[MetricValue]) -> list[MetricValue]:
    if not replacements:
        return base
    replacement_names = {metric.metric_name for metric in replacements}
    return [metric for metric in base if metric.metric_name not in replacement_names] + replacements


def _metric_label(value: float | None) -> str:
    if value is None:
        return "not_applicable"
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"


def _dict_or_empty(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:80] for item in value if isinstance(item, str)]


def _safe_strategy_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return RetrievalStrategy(value).value
    except ValueError:
        return None


def _safe_reason(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace(" ", "_")
    if not text:
        return None
    return text[:80]


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    return None


def _first_bool(*values: object) -> bool:
    for value in values:
        if isinstance(value, bool):
            return value
    return False


def _expected_strategy_hints(
    metadata_json: dict[str, object] | None,
) -> tuple[str | None, list[str]]:
    metadata = metadata_json or {}
    expected = _safe_strategy_value(metadata.get("expected_strategy"))
    raw_acceptable = metadata.get("acceptable_strategies")
    acceptable: list[str] = []
    if isinstance(raw_acceptable, list):
        for item in raw_acceptable:
            strategy = _safe_strategy_value(item)
            if strategy is not None and strategy not in acceptable:
                acceptable.append(strategy)
    return expected, acceptable


def _target_metadata_json(target: EvaluationStrategyTarget) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": EVALUATION_TARGET_SCHEMA_VERSION,
        "comparison_label": target.comparison_label,
        "retrieval_strategy": target.retrieval_strategy.value,
        "cache_mode": target.cache_mode.value,
    }
    if target.graph_store_provider is not None:
        payload["graph_store_provider"] = target.graph_store_provider
    return payload


def _metric_detail_with_target(
    details: dict[str, object],
    *,
    target: EvaluationStrategyTarget,
) -> dict[str, object]:
    merged = dict(details)
    merged["evaluation_target"] = _target_metadata_json(target)
    return merged


def _safe_label_value(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text or len(text) > max_length:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_.-:")
    if any(char not in allowed for char in text):
        return None
    return text


def _safe_cache_mode(value: object) -> EvaluationCacheMode | None:
    if not isinstance(value, str):
        return None
    try:
        return EvaluationCacheMode(value)
    except ValueError:
        return None


def _safe_cache_status(value: object) -> str:
    if isinstance(value, str) and value in {"hit", "miss", "stale", "bypass"}:
        return value
    return "bypass"


def _safe_graph_provider(value: object) -> str | None:
    if isinstance(value, str) and value.strip().lower() in {"postgres", "neo4j"}:
        return value.strip().lower()
    return None


def _list_graph_paths(
    db: Session,
    *,
    retrieval_run_id: int | None,
) -> list[GraphRetrievalPath]:
    if retrieval_run_id is None:
        return []
    return list(
        db.scalars(
            select(GraphRetrievalPath)
            .where(GraphRetrievalPath.retrieval_run_id == retrieval_run_id)
            .order_by(GraphRetrievalPath.graph_retrieval_path_id.asc())
        ).all()
    )


def _selected_retrieval_source_chunk_ids(
    db: Session,
    *,
    retrieval_run_id: int | None,
) -> set[int]:
    if retrieval_run_id is None:
        return set()
    return {
        chunk_id
        for chunk_id in db.scalars(
            select(RetrievalRunItem.document_chunk_id).where(
                RetrievalRunItem.retrieval_run_id == retrieval_run_id,
                RetrievalRunItem.selected_flag.is_(True),
            )
        ).all()
        if isinstance(chunk_id, int) and chunk_id > 0
    }


def _filter_graph_paths_for_source_chunk_ids(
    graph_paths: list[GraphRetrievalPath],
    source_chunk_ids: set[int],
) -> list[GraphRetrievalPath]:
    if not source_chunk_ids:
        return []
    return [
        path
        for path in graph_paths
        if _path_positive_ids(path.source_chunk_ids_json) & source_chunk_ids
    ]


def _not_applicable_graph_metrics(
    *,
    target: EvaluationStrategyTarget,
    reason_code: str,
    provider: str | None = None,
    reason_codes: list[str] | None = None,
) -> list[MetricValue]:
    details = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "not_applicable": True,
        "reason_code": reason_code,
        "graph_store_provider": provider or target.graph_store_provider,
        "reason_codes": reason_codes or [reason_code],
    }
    return [
        MetricValue(
            metric_name="graph_path_relevance",
            metric_score=None,
            metric_label="not_applicable",
            details=details,
        ),
        MetricValue(
            metric_name="graph_citation_coverage",
            metric_score=None,
            metric_label="not_applicable",
            details=details,
        ),
        MetricValue(
            metric_name="multi_hop_answerability",
            metric_score=None,
            metric_label="not_applicable",
            details=details,
        ),
        MetricValue(
            metric_name="entity_relation_quality_summary",
            metric_score=None,
            metric_value=None,
            metric_label="not_applicable",
            details=details,
        ),
    ]


def _graph_path_relevance_metric(
    *,
    graph_paths: list[GraphRetrievalPath],
    metadata_json: dict[str, object] | None,
    provider: str | None,
    reason_codes: list[str],
) -> MetricValue:
    expected_entities = _metadata_string_set(metadata_json, "expected_entity_labels")
    expected_relations = _metadata_string_set(metadata_json, "expected_relation_types")
    observed_entities = {
        label
        for path in graph_paths
        for label in _path_string_values(path.path_json, "safe_entity_labels")
    }
    observed_relations = {
        relation
        for path in graph_paths
        for relation in _path_string_values(path.path_json, "relation_types")
    }
    expected_count = len(expected_entities) + len(expected_relations)
    matched_count = len(expected_entities & observed_entities) + len(
        expected_relations & observed_relations
    )
    if expected_count <= 0:
        return MetricValue(
            metric_name="graph_path_relevance",
            metric_score=None,
            metric_label="not_applicable",
            details={
                "schema_version": EVALUATION_SCHEMA_VERSION,
                "not_applicable": True,
                "reason_code": "graph_relevance_hints_missing",
                "graph_store_provider": provider,
                "path_count": len(graph_paths),
                "expected_entity_label_count": 0,
                "matched_entity_label_count": 0,
                "expected_relation_type_count": 0,
                "matched_relation_type_count": 0,
                "reason_codes": reason_codes,
            },
        )
    score = _ratio_float(matched_count, expected_count)
    return MetricValue(
        metric_name="graph_path_relevance",
        metric_score=score,
        metric_label=_metric_label(score),
        details={
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "graph_store_provider": provider,
            "path_count": len(graph_paths),
            "expected_entity_label_count": len(expected_entities),
            "matched_entity_label_count": len(expected_entities & observed_entities),
            "expected_relation_type_count": len(expected_relations),
            "matched_relation_type_count": len(expected_relations & observed_relations),
            "reason_codes": reason_codes,
        },
    )


def _graph_citation_coverage_metric(
    db: Session,
    *,
    graph_paths: list[GraphRetrievalPath],
    retrieval_run_id: int | None,
    provider: str | None,
) -> MetricValue:
    if retrieval_run_id is None:
        coverage = calculate_graph_citation_coverage(
            validated_paths=(),
            citation_source_count=0,
        )
    else:
        located = GraphPathSourceLocator().locate(
            db,
            retrieval_run_id=retrieval_run_id,
            paths=graph_paths,
        )
        validated = GraphPathValidator().validate(paths=graph_paths, located_sources=located)
        coverage = calculate_graph_citation_coverage(
            validated_paths=validated,
            citation_source_count=sum(
                len(mapping.citation_ids) for path in validated for mapping in path.source_mappings
            ),
        )
    return MetricValue(
        metric_name="graph_citation_coverage",
        metric_score=coverage.citation_coverage_ratio,
        metric_label=_metric_label(coverage.citation_coverage_ratio),
        details={
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "graph_store_provider": provider,
            "path_count": coverage.path_count,
            "valid_path_count": coverage.valid_path_count,
            "citable_path_count": coverage.citable_path_count,
            "excluded_path_count": coverage.excluded_path_count,
            "source_chunk_count": coverage.source_chunk_count,
            "resolved_source_chunk_count": coverage.resolved_source_chunk_count,
            "citable_source_chunk_count": coverage.citable_source_chunk_count,
            "citation_source_count": coverage.citation_source_count,
            "source_chunk_coverage_ratio": coverage.source_chunk_coverage_ratio,
            "reason_codes": list(coverage.reason_codes),
        },
    )


def _multi_hop_answerability_metric(
    *,
    graph_paths: list[GraphRetrievalPath],
    metadata_json: dict[str, object] | None,
    retrieval_summary: object,
    provider: str | None,
) -> MetricValue:
    required_hops = _metadata_positive_int(metadata_json, "required_hop_count")
    if required_hops is None:
        return MetricValue(
            metric_name="multi_hop_answerability",
            metric_score=None,
            metric_label="not_applicable",
            details={
                "schema_version": EVALUATION_SCHEMA_VERSION,
                "not_applicable": True,
                "reason_code": "required_hop_count_missing",
                "graph_store_provider": provider,
            },
        )
    max_depth = max((_path_depth(path) for path in graph_paths), default=0)
    raw_selected_count = getattr(retrieval_summary, "selected_count", 0)
    selected_count = raw_selected_count if isinstance(raw_selected_count, int) else 0
    score = 1.0 if max_depth >= required_hops and selected_count > 0 else 0.0
    return MetricValue(
        metric_name="multi_hop_answerability",
        metric_score=score,
        metric_label=_metric_label(score),
        details={
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "graph_store_provider": provider,
            "required_hop_count": required_hops,
            "max_observed_hop_count": max_depth,
            "selected_count": selected_count,
            "path_count": len(graph_paths),
        },
    )


def _entity_relation_quality_metric(
    *,
    graph_paths: list[GraphRetrievalPath],
    provider: str | None,
    score_summary: dict[str, object],
    case: EvaluationCase,
) -> MetricValue:
    entity_labels = {
        label
        for path in graph_paths
        for label in _path_string_values(path.path_json, "safe_entity_labels")
    }
    relation_types = {
        relation
        for path in graph_paths
        for relation in _path_string_values(path.path_json, "relation_types")
    }
    path_count = len(graph_paths)
    return MetricValue(
        metric_name="entity_relation_quality_summary",
        metric_score=None,
        metric_value=float(path_count),
        metric_label="count",
        details={
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "case_id": case.case_id,
            "graph_store_provider": provider,
            "path_count": path_count,
            "safe_entity_label_count": len(entity_labels),
            "relation_type_count": len(relation_types),
            "source_chunk_id_count": len(
                {
                    chunk_id
                    for path in graph_paths
                    for chunk_id in _path_positive_ids(path.source_chunk_ids_json)
                }
            ),
            "graph_entity_lookup_count": _float_or_none(
                score_summary.get("graph_entity_lookup_count")
            ),
            "graph_relation_count": _float_or_none(score_summary.get("graph_relation_count")),
            "graph_source_candidate_count": _float_or_none(
                score_summary.get("graph_source_candidate_count")
            ),
        },
    )


def _metadata_string_set(
    metadata_json: dict[str, object] | None,
    key: str,
) -> set[str]:
    metadata = metadata_json or {}
    raw = metadata.get(key)
    if not isinstance(raw, list):
        return set()
    values: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        normalized = item.strip().lower()
        if normalized and len(normalized) <= 120:
            values.add(normalized)
    return values


def _metadata_positive_int(
    metadata_json: dict[str, object] | None,
    key: str,
) -> int | None:
    metadata = metadata_json or {}
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _path_string_values(path_json: dict[str, object], key: str) -> set[str]:
    raw = path_json.get(key)
    if not isinstance(raw, list):
        return set()
    return {
        value.strip().lower()
        for value in raw
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 120
    }


def _path_depth(path: GraphRetrievalPath) -> int:
    raw = path.path_json.get("depth")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return max(0, raw)


def _path_positive_ids(values: object) -> set[int]:
    if not isinstance(values, list):
        return set()
    return {
        int(value)
        for value in values
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    }


def _ratio_float(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(1.0, numerator / denominator)), 6)


def _is_graph_provider_unavailable(reason_codes: list[str]) -> bool:
    return "graph_store_provider_unavailable" in reason_codes


def _provider_skip_reason(reason_codes: list[str]) -> str:
    for reason in (
        "neo4j_not_configured",
        "neo4j_driver_unavailable",
        "neo4j_connection_failed",
        "graph_store_provider_unavailable",
    ):
        if reason in reason_codes:
            return reason
    return "graph_store_provider_unavailable"


def _is_safe_provider_skip(rag_result: RagEvaluationResult) -> bool:
    summary = (
        rag_result.retrieval_score_summary.model_dump(mode="json")
        if rag_result.retrieval_score_summary is not None
        else {}
    )
    reason_codes = _string_values(summary.get("graph_reason_codes"))
    return _is_graph_provider_unavailable(reason_codes)


def _provider_skip_base_metric_replacements(
    *,
    target: EvaluationStrategyTarget,
    rag_result: RagEvaluationResult,
) -> list[MetricValue]:
    summary = (
        rag_result.retrieval_score_summary.model_dump(mode="json")
        if rag_result.retrieval_score_summary is not None
        else {}
    )
    reason_codes = _string_values(summary.get("graph_reason_codes"))
    reason_code = _provider_skip_reason(reason_codes)
    provider = _safe_graph_provider(
        target.graph_store_provider or summary.get("graph_store_provider")
    )
    details = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "not_applicable": True,
        "reason_code": reason_code,
        "reason_codes": reason_codes or [reason_code],
        "graph_store_provider": provider,
    }
    return [
        MetricValue(
            metric_name=metric_name,
            metric_score=None,
            metric_label="not_applicable",
            details=details,
        )
        for metric_name in sorted(PROVIDER_SKIP_BASE_METRICS)
    ]


def _requires_strategy_target_runner(target: EvaluationStrategyTarget) -> bool:
    return (
        target.graph_store_provider is not None or target.cache_mode != EvaluationCacheMode.DEFAULT
    )


def _is_cacheable_target(target: EvaluationStrategyTarget) -> bool:
    return target.retrieval_strategy in CACHEABLE_EVALUATION_STRATEGIES


def _not_applicable_cache_metrics(
    *,
    target: EvaluationStrategyTarget,
    reason_code: str,
) -> list[MetricValue]:
    details = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "not_applicable": True,
        "reason_code": reason_code,
        "cache_mode": target.cache_mode.value,
    }
    return [
        MetricValue(
            metric_name="cache_hit_rate",
            metric_score=None,
            metric_label="not_applicable",
            details=details,
        ),
        MetricValue(
            metric_name="cache_saved_latency",
            metric_score=None,
            metric_value=None,
            metric_label="not_applicable",
            details=details,
        ),
    ]


def _evaluation_scope_from_targets(
    targets: list[EvaluationStrategyTarget],
) -> EvaluationScope:
    if any(target.retrieval_strategy in ASK_ONLY_EVALUATION_STRATEGIES for target in targets):
        return "end_to_end"
    return "retrieval"


def _evaluation_scope_from_strategy_labels(strategies: list[str]) -> EvaluationScope:
    if any(
        _base_comparison_label(label) in ASK_ONLY_EVALUATION_STRATEGY_VALUES for label in strategies
    ):
        return "end_to_end"
    return "retrieval"


def _selected_strategy_targets(
    payload: EvaluationRunCreateRequest,
) -> list[EvaluationStrategyTarget]:
    cache_modes = _selected_cache_modes(payload.cache_modes)
    targets: list[EvaluationStrategyTarget] = []
    seen: set[tuple[str, str, str | None, str]] = set()
    for strategy in payload.strategies or [payload.strategy_type]:
        base_target = _target_from_request_strategy(strategy)
        for cache_mode in _target_cache_modes(base_target, cache_modes):
            target = EvaluationStrategyTarget(
                comparison_label=_comparison_label(base_target, cache_mode),
                retrieval_strategy=base_target.retrieval_strategy,
                graph_store_provider=base_target.graph_store_provider,
                cache_mode=cache_mode,
            )
            key = (
                target.comparison_label,
                target.retrieval_strategy.value,
                target.graph_store_provider,
                target.cache_mode.value,
            )
            if key in seen:
                continue
            seen.add(key)
            targets.append(target)
    return targets


def _target_cache_modes(
    target: EvaluationStrategyTarget,
    cache_modes: list[EvaluationCacheMode],
) -> list[EvaluationCacheMode]:
    if _is_cacheable_target(target):
        return cache_modes
    return [EvaluationCacheMode.DEFAULT]


def _selected_cache_modes(
    cache_modes: list[EvaluationCacheMode] | None,
) -> list[EvaluationCacheMode]:
    selected = cache_modes or [EvaluationCacheMode.DEFAULT]
    return sorted(selected, key=lambda mode: CACHE_MODE_ORDER[mode])


def _target_from_request_strategy(
    strategy: EvaluationRunRequestStrategy,
) -> EvaluationStrategyTarget:
    if strategy == EvaluationRunRequestStrategy.GRAPH_POSTGRES:
        return EvaluationStrategyTarget(
            comparison_label="graph_postgres",
            retrieval_strategy=RetrievalStrategy.GRAPH,
            graph_store_provider="postgres",
        )
    if strategy == EvaluationRunRequestStrategy.GRAPH_NEO4J:
        return EvaluationStrategyTarget(
            comparison_label="graph_neo4j",
            retrieval_strategy=RetrievalStrategy.GRAPH,
            graph_store_provider="neo4j",
        )
    if strategy == EvaluationRunRequestStrategy.GRAPH:
        return EvaluationStrategyTarget(
            comparison_label="graph_postgres",
            retrieval_strategy=RetrievalStrategy.GRAPH,
            graph_store_provider="postgres",
        )
    return EvaluationStrategyTarget(
        comparison_label=strategy.value,
        retrieval_strategy=RetrievalStrategy(strategy.value),
    )


def _comparison_label(
    target: EvaluationStrategyTarget,
    cache_mode: EvaluationCacheMode,
) -> str:
    if cache_mode == EvaluationCacheMode.DEFAULT:
        return target.comparison_label
    return f"{target.comparison_label}__cache_{cache_mode.value}"


def _strategy_targets_from_config(config: dict[str, object]) -> list[EvaluationStrategyTarget]:
    raw_targets = config.get("strategy_targets")
    if isinstance(raw_targets, list):
        targets = [
            target
            for item in raw_targets
            if isinstance(item, dict)
            if (target := _target_from_metadata(item)) is not None
        ]
        if targets:
            return targets
    labels = _strategy_values(config)
    return [_target_from_label(label) for label in labels]


def _target_from_metadata(value: dict[str, object]) -> EvaluationStrategyTarget | None:
    label = _safe_label_value(value.get("comparison_label"), max_length=120)
    strategy_value = _safe_strategy_value(value.get("retrieval_strategy"))
    cache_mode = _safe_cache_mode(value.get("cache_mode"))
    if label is None or strategy_value is None or cache_mode is None:
        return None
    return EvaluationStrategyTarget(
        comparison_label=label,
        retrieval_strategy=RetrievalStrategy(strategy_value),
        graph_store_provider=_safe_graph_provider(value.get("graph_store_provider")),
        cache_mode=cache_mode,
    )


def _target_from_label(label: str) -> EvaluationStrategyTarget:
    base_label = label
    cache_mode = EvaluationCacheMode.DEFAULT
    marker = "__cache_"
    if marker in label:
        base_label, raw_cache_mode = label.split(marker, 1)
        cache_mode = _safe_cache_mode(raw_cache_mode) or EvaluationCacheMode.DEFAULT
    if base_label == "graph_neo4j":
        return EvaluationStrategyTarget(
            comparison_label=label,
            retrieval_strategy=RetrievalStrategy.GRAPH,
            graph_store_provider="neo4j",
            cache_mode=cache_mode,
        )
    if base_label in {"graph_postgres", "graph"}:
        return EvaluationStrategyTarget(
            comparison_label=label,
            retrieval_strategy=RetrievalStrategy.GRAPH,
            graph_store_provider="postgres",
            cache_mode=cache_mode,
        )
    strategy = _safe_strategy_value(base_label) or DEFAULT_RETRIEVAL_STRATEGY.value
    return EvaluationStrategyTarget(
        comparison_label=label,
        retrieval_strategy=RetrievalStrategy(strategy),
        cache_mode=cache_mode,
    )


def _strategy_values(config: dict[str, object]) -> list[str]:
    raw = config.get("strategies")
    values = [str(strategy) for strategy in raw] if isinstance(raw, list) else []
    enabled = {
        RetrievalStrategy.DENSE.value,
        RetrievalStrategy.SPARSE.value,
        RetrievalStrategy.HYBRID.value,
        RetrievalStrategy.GRAPH.value,
        "graph_postgres",
        "graph_neo4j",
        RetrievalStrategy.AGENTIC_ROUTER.value,
        RetrievalStrategy.LLM_TOOL_ORCHESTRATOR.value,
        RetrievalStrategy.LANGCHAIN_AGENTIC.value,
        RetrievalStrategy.LANGGRAPH_AGENTIC.value,
    }
    filtered = [
        strategy
        for strategy in values
        if (strategy.split("__cache_", 1)[0] if "__cache_" in strategy else strategy) in enabled
    ]
    return filtered or [DEFAULT_RETRIEVAL_STRATEGY.value]


def _case_request_id(
    request_id: str | None,
    *,
    case_key: str,
    strategy_type: str,
) -> str | None:
    if request_id is None:
        return None
    derived = f"{request_id}:{strategy_type}:{case_key}"
    if len(derived) <= RETRIEVAL_RUN_REQUEST_ID_MAX_LENGTH:
        return derived
    digest = hashlib.sha256(derived.encode("utf-8")).hexdigest()[:12]
    suffix = f":{strategy_type}:{digest}"
    prefix_length = max(RETRIEVAL_RUN_REQUEST_ID_MAX_LENGTH - len(suffix), 0)
    return f"{request_id[:prefix_length]}{suffix}"


def _evaluation_cache_attempt_id(evaluation_run_id: int, started_at: datetime) -> str:
    digest = hashlib.sha256(f"{evaluation_run_id}:{started_at.isoformat()}".encode()).hexdigest()
    return digest[:12]


def _evaluation_cache_target_id(
    attempt_id: str,
    *,
    case_key: str,
    target_label: str,
) -> str:
    digest = hashlib.sha256(f"{attempt_id}:{case_key}:{target_label}".encode()).hexdigest()
    return f"{attempt_id}.{digest[:12]}"


def _unique_case_count(items: list[EvaluationRunItem]) -> int:
    keys = {
        item.case_key or f"id:{item.evaluation_case_id}"
        for item in items
        if item.case_key or item.evaluation_case_id is not None
    }
    return len(keys) if keys else len(items)


def _compare_metric_summaries(
    base: EvaluationRunSummary,
    candidate: EvaluationRunSummary,
) -> list[EvaluationMetricComparison]:
    metric_names = sorted(
        set(base.metric_names)
        | set(candidate.metric_names)
        | set(base.metric_summary)
        | set(candidate.metric_summary)
    )
    comparisons: list[EvaluationMetricComparison] = []
    for metric_name in metric_names:
        base_score = base.metric_summary.get(metric_name)
        candidate_score = candidate.metric_summary.get(metric_name)
        lower_is_better = metric_name in LOWER_IS_BETTER_METRICS
        delta = (
            round(candidate_score - base_score, 6)
            if base_score is not None and candidate_score is not None
            else None
        )
        comparisons.append(
            EvaluationMetricComparison(
                metric_name=metric_name,
                base_score=base_score,
                candidate_score=candidate_score,
                delta=delta,
                direction=_metric_comparison_direction(delta, lower_is_better),
                lower_is_better=lower_is_better,
            )
        )
    return comparisons


def _compare_generation_summaries(
    base: EvaluationRunSummary,
    candidate: EvaluationRunSummary,
    base_items: list[EvaluationRunItem],
    base_results_by_item: dict[int, list[EvaluationResult]],
    candidate_items: list[EvaluationRunItem],
    candidate_results_by_item: dict[int, list[EvaluationResult]],
) -> EvaluationGenerationComparison:
    comparable_cost_coverage = _has_matching_successful_generation_items(
        base_items,
        base_results_by_item,
        candidate_items,
        candidate_results_by_item,
        metadata_predicate=_has_generation_cost_metadata,
    )
    comparable_tokens_coverage = _has_matching_successful_generation_items(
        base_items,
        base_results_by_item,
        candidate_items,
        candidate_results_by_item,
        metadata_predicate=_has_generation_token_metadata,
    )
    comparable_latency_coverage = _has_matching_successful_generation_items(
        base_items,
        base_results_by_item,
        candidate_items,
        candidate_results_by_item,
        metadata_predicate=_has_generation_latency_metadata,
    )
    cost_delta = (
        _float_delta(
            base.total_estimated_cost_usd,
            candidate.total_estimated_cost_usd,
            digits=6,
        )
        if comparable_cost_coverage
        else None
    )
    tokens_delta = (
        _int_delta(base.total_tokens, candidate.total_tokens)
        if comparable_tokens_coverage
        else None
    )
    latency_delta = (
        _float_delta(
            base.avg_generation_latency_ms,
            candidate.avg_generation_latency_ms,
            digits=3,
        )
        if comparable_latency_coverage
        else None
    )
    return EvaluationGenerationComparison(
        base_estimated_cost_usd=base.total_estimated_cost_usd,
        candidate_estimated_cost_usd=candidate.total_estimated_cost_usd,
        cost_delta=cost_delta,
        cost_direction=_metric_comparison_direction(cost_delta, lower_is_better=True),
        base_total_tokens=base.total_tokens,
        candidate_total_tokens=candidate.total_tokens,
        tokens_delta=tokens_delta,
        tokens_direction=_metric_comparison_direction(tokens_delta, lower_is_better=True),
        base_avg_generation_latency_ms=base.avg_generation_latency_ms,
        candidate_avg_generation_latency_ms=candidate.avg_generation_latency_ms,
        latency_delta=latency_delta,
        latency_direction=_metric_comparison_direction(latency_delta, lower_is_better=True),
        base_providers=base.generation_providers,
        base_models=base.generation_models,
        candidate_providers=candidate.generation_providers,
        candidate_models=candidate.generation_models,
    )


def _has_matching_successful_generation_items(
    base_items: list[EvaluationRunItem],
    base_results_by_item: dict[int, list[EvaluationResult]],
    candidate_items: list[EvaluationRunItem],
    candidate_results_by_item: dict[int, list[EvaluationResult]],
    *,
    metadata_predicate: Callable[[EvaluationRunItem], bool],
) -> bool:
    base_keys = _successful_generation_item_keys(
        base_items,
        base_results_by_item,
        metadata_predicate=metadata_predicate,
    )
    candidate_keys = _successful_generation_item_keys(
        candidate_items,
        candidate_results_by_item,
        metadata_predicate=metadata_predicate,
    )
    return bool(base_keys) and base_keys == candidate_keys


def _successful_generation_item_keys(
    items: list[EvaluationRunItem],
    results_by_item: dict[int, list[EvaluationResult]],
    *,
    metadata_predicate: Callable[[EvaluationRunItem], bool],
) -> set[tuple[str, str, str, str]] | None:
    keys: set[tuple[str, str, str, str]] = set()
    for item in items:
        if item.strategy_type not in ASK_ONLY_EVALUATION_STRATEGY_VALUES:
            continue
        if item.status != "succeeded":
            continue
        if not metadata_predicate(item):
            return None
        item_key = _generation_item_key(
            item,
            results_by_item.get(item.evaluation_run_item_id, []),
        )
        if item_key is None:
            return None
        keys.add(item_key)
    return keys


def _generation_item_key(
    item: EvaluationRunItem,
    results: list[EvaluationResult],
) -> tuple[str, str, str, str] | None:
    details = _case_metadata_payload(results)
    question_hash = _safe_hash_value(details.get("question_hash")) or _safe_hash_value(
        _item_case_snapshot(item).get("question_hash")
    )
    case_snapshot_hash = _safe_hash_value(details.get("case_snapshot_hash")) or _safe_hash_value(
        _item_case_snapshot(item).get("case_snapshot_hash")
    )
    if question_hash is None or case_snapshot_hash is None:
        return None
    case_key = (
        _safe_case_identifier(details.get("case_id"))
        or _safe_case_identifier(item.case_key)
        or (f"id:{item.evaluation_case_id}" if item.evaluation_case_id is not None else "snapshot")
    )
    return case_key, item.strategy_type, question_hash, case_snapshot_hash


def _has_generation_cost_metadata(item: EvaluationRunItem) -> bool:
    return item.estimated_cost_usd is not None


def _has_generation_token_metadata(item: EvaluationRunItem) -> bool:
    return item.total_tokens is not None


def _has_generation_latency_metadata(item: EvaluationRunItem) -> bool:
    return item.generation_latency_ms is not None


def _float_delta(
    base_value: float | None,
    candidate_value: float | None,
    *,
    digits: int,
) -> float | None:
    if base_value is None or candidate_value is None:
        return None
    return round(candidate_value - base_value, digits)


def _int_delta(base_value: int | None, candidate_value: int | None) -> int | None:
    if base_value is None or candidate_value is None:
        return None
    return candidate_value - base_value


def _metric_comparison_direction(
    delta: float | None,
    lower_is_better: bool,
) -> EvaluationComparisonDirection:
    if delta is None:
        return "not_applicable"
    if abs(delta) <= METRIC_DELTA_EPSILON:
        return "unchanged"
    effective_delta = -delta if lower_is_better else delta
    return "improved" if effective_delta > 0 else "regressed"


def _compare_run_cases(
    base_items: list[EvaluationRunItem],
    base_results_by_item: dict[int, list[EvaluationResult]],
    candidate_items: list[EvaluationRunItem],
    candidate_results_by_item: dict[int, list[EvaluationResult]],
) -> list[EvaluationCaseComparison]:
    base_sources = _case_comparison_sources(base_items, base_results_by_item)
    candidate_sources = _case_comparison_sources(candidate_items, candidate_results_by_item)
    duplicate_case_ids = _duplicated_case_ids(base_sources, candidate_sources)
    base_by_key = _case_sources_by_match_key(base_sources, duplicate_case_ids)
    candidate_by_key = _case_sources_by_match_key(candidate_sources, duplicate_case_ids)

    comparisons: list[EvaluationCaseComparison] = []
    for match_key in sorted(set(base_by_key) | set(candidate_by_key)):
        base = base_by_key.get(match_key)
        candidate = candidate_by_key.get(match_key)
        source = base if base is not None else candidate
        case_id = source.case_id if source is not None else match_key
        transition = _case_transition(
            base.status if base is not None else None,
            candidate.status if candidate is not None else None,
        )
        comparisons.append(
            EvaluationCaseComparison(
                case_id=case_id,
                question_hash=_first_non_empty(
                    base.question_hash if base is not None else None,
                    candidate.question_hash if candidate is not None else None,
                ),
                case_snapshot_hash=_first_non_empty(
                    base.case_snapshot_hash if base is not None else None,
                    candidate.case_snapshot_hash if candidate is not None else None,
                ),
                comparison_label=_first_non_empty(
                    base.comparison_label if base is not None else None,
                    candidate.comparison_label if candidate is not None else None,
                ),
                base_status=base.status if base is not None else None,
                candidate_status=candidate.status if candidate is not None else None,
                transition=transition,
                metric_deltas=(
                    _case_metric_deltas(base.metric_values, candidate.metric_values)
                    if base is not None and candidate is not None
                    else {}
                ),
            )
        )
    return comparisons


def _case_comparison_sources(
    items: list[EvaluationRunItem],
    results_by_item: dict[int, list[EvaluationResult]],
) -> list[EvaluationCaseComparisonSource]:
    sources: list[EvaluationCaseComparisonSource] = []
    for item in items:
        results = results_by_item.get(item.evaluation_run_item_id, [])
        details = _case_metadata_payload(results)
        question_hash = _safe_hash_value(details.get("question_hash")) or _safe_hash_value(
            _item_case_snapshot(item).get("question_hash")
        )
        case_snapshot_hash = _safe_hash_value(
            details.get("case_snapshot_hash")
        ) or _safe_hash_value(_item_case_snapshot(item).get("case_snapshot_hash"))
        case_id = (
            _safe_case_identifier(details.get("case_id"))
            or _safe_case_identifier(item.case_key)
            or question_hash
            or case_snapshot_hash
            or (
                f"evaluation_case:{item.evaluation_case_id}"
                if item.evaluation_case_id is not None
                else None
            )
            or f"item:{item.evaluation_run_item_id}"
        )
        sources.append(
            EvaluationCaseComparisonSource(
                evaluation_run_item_id=item.evaluation_run_item_id,
                case_id=case_id,
                question_hash=question_hash,
                case_snapshot_hash=case_snapshot_hash,
                comparison_label=_item_comparison_label(item),
                status=cast(EvaluationStatus, item.status),
                metric_values=_result_metric_values(results),
            )
        )
    return sources


def _case_metadata_payload(results: list[EvaluationResult]) -> dict[str, object]:
    for result in results:
        if result.metric_name != "case_metadata":
            continue
        for payload in (result.metric_detail_json, result.details_json):
            if isinstance(payload, dict):
                return payload
    return {}


def _result_metric_values(results: list[EvaluationResult]) -> dict[str, float]:
    values: dict[str, float] = {}
    for result in results:
        if result.metric_name == "case_metadata":
            continue
        value = _result_numeric_value(result)
        if value is not None:
            values[result.metric_name] = value
    return values


def _duplicated_case_ids(
    *source_groups: list[EvaluationCaseComparisonSource],
) -> set[str]:
    duplicated: set[str] = set()
    for sources in source_groups:
        counts: dict[str, int] = {}
        for source in sources:
            counts[source.case_id] = counts.get(source.case_id, 0) + 1
        duplicated.update(case_id for case_id, count in counts.items() if count > 1)
    return duplicated


def _case_sources_by_match_key(
    sources: list[EvaluationCaseComparisonSource],
    duplicated_case_ids: set[str],
) -> dict[str, EvaluationCaseComparisonSource]:
    by_key: dict[str, EvaluationCaseComparisonSource] = {}
    for source in sources:
        match_key = source.case_id
        if source.case_id in duplicated_case_ids:
            match_key = f"{source.case_id}::{source.comparison_label or 'default'}"
        if match_key in by_key:
            match_key = f"{match_key}::item:{source.evaluation_run_item_id}"
        by_key[match_key] = source
    return by_key


def _case_metric_deltas(
    base_values: dict[str, float],
    candidate_values: dict[str, float],
) -> dict[str, float | None]:
    deltas: dict[str, float | None] = {}
    for metric_name in sorted(set(base_values) | set(candidate_values)):
        base_value = base_values.get(metric_name)
        candidate_value = candidate_values.get(metric_name)
        deltas[metric_name] = (
            round(candidate_value - base_value, 6)
            if base_value is not None and candidate_value is not None
            else None
        )
    return deltas


def _case_transition(
    base_status: EvaluationStatus | None,
    candidate_status: EvaluationStatus | None,
) -> EvaluationCaseTransition:
    if base_status is None:
        return "added"
    if candidate_status is None:
        return "removed"
    if base_status == candidate_status:
        return "unchanged"
    if base_status == "succeeded" and candidate_status != "succeeded":
        return "regressed"
    if base_status != "succeeded" and candidate_status == "succeeded":
        return "improved"
    return "unchanged"


def _first_non_empty(left: str | None, right: str | None) -> str | None:
    return left or right


def _safe_case_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("\x00", " ").split())
    if not text or len(text) > 200:
        return None
    lowered = text.lower()
    forbidden = ("api_key", "bearer", "credential", "password", "secret", "token")
    if any(part in lowered for part in forbidden) or "@" in text:
        return None
    return text


def _metric_summary(
    items: list[EvaluationRunItem],
    results_by_item: dict[int, list[EvaluationResult]],
) -> dict[str, float]:
    items_by_id = {item.evaluation_run_item_id: item for item in items}
    values: dict[str, list[float]] = {}
    for item_id, results in results_by_item.items():
        item = items_by_id.get(item_id)
        metric_by_name = {result.metric_name: result for result in results}
        answer_generated = item is not None and _item_answer_generated(item, metric_by_name)
        for result in results:
            if result.metric_name == "case_metadata":
                continue
            if result.metric_name in ANSWER_GENERATION_DEPENDENT_METRICS and not answer_generated:
                continue
            value = _result_numeric_value(result)
            if value is None:
                continue
            values.setdefault(result.metric_name, []).append(value)
    return {
        metric_name: (
            _percentile(scores, 0.95)
            if metric_name == "p95_latency"
            else round(sum(scores) / len(scores), 6)
        )
        for metric_name, scores in sorted(values.items())
        if scores
    }


def _strategy_comparison(
    items: list[EvaluationRunItem],
    results_by_item: dict[int, list[EvaluationResult]],
) -> list[StrategyComparisonMetric]:
    items_by_id = {item.evaluation_run_item_id: item for item in items}
    target_by_id = {item.evaluation_run_item_id: _item_target_metadata(item) for item in items}
    values: dict[tuple[str, str], list[float]] = {}
    not_applicable: dict[tuple[str, str], int] = {}
    failed_count: dict[str, int] = {}
    for item in items:
        comparison_label = _item_comparison_label(item)
        if item.status == "failed":
            failed_count[comparison_label] = failed_count.get(comparison_label, 0) + 1
    for item_id, results in results_by_item.items():
        item_for_result = items_by_id.get(item_id)
        if item_for_result is None:
            continue
        comparison_label = _item_comparison_label(item_for_result)
        if item_for_result.status == "failed":
            continue
        metric_by_name = {result.metric_name: result for result in results}
        answer_generated = _item_answer_generated(item_for_result, metric_by_name)
        for result in results:
            if result.metric_name == "case_metadata":
                continue
            key = (comparison_label, result.metric_name)
            if result.metric_name in ANSWER_GENERATION_DEPENDENT_METRICS and not answer_generated:
                not_applicable[key] = not_applicable.get(key, 0) + 1
                continue
            value = _result_numeric_value(result)
            if value is None:
                not_applicable[key] = not_applicable.get(key, 0) + 1
                continue
            values.setdefault(key, []).append(value)

    metrics: list[StrategyComparisonMetric] = []
    observed_keys = set(values).union(not_applicable)
    observed_strategies = {strategy_type for strategy_type, _ in observed_keys}
    failed_only_keys = {
        (strategy_type, "evaluation_item_status")
        for strategy_type in failed_count
        if strategy_type not in observed_strategies
    }
    all_keys = sorted(observed_keys.union(failed_only_keys))
    for strategy_type, metric_name in all_keys:
        series = sorted(values.get((strategy_type, metric_name), []))
        average = round(sum(series) / len(series), 6) if series else None
        target = next(
            (
                target
                for target in target_by_id.values()
                if _metadata_comparison_label(target) == strategy_type
            ),
            {},
        )
        metrics.append(
            StrategyComparisonMetric(
                strategy_type=strategy_type,
                metric_name=metric_name,
                average=average,
                p50=_percentile(series, 0.50) if series else None,
                p95=_percentile(series, 0.95) if series else None,
                count=len(series),
                failed_count=failed_count.get(strategy_type, 0),
                not_applicable_count=not_applicable.get((strategy_type, metric_name), 0),
                comparison_label=strategy_type,
                retrieval_strategy=_metadata_retrieval_strategy(target),
                graph_store_provider=_metadata_graph_provider(target),
                cache_mode=_metadata_cache_mode(target),
            )
        )
    return metrics


def _result_numeric_value(result: EvaluationResult) -> float | None:
    if result.metric_name == "p95_latency":
        return float(result.metric_value) if result.metric_value is not None else None
    if result.metric_score is not None:
        return float(result.metric_score)
    if result.metric_value is not None:
        return float(result.metric_value)
    return None


def _metric_snapshot(metric_by_name: dict[str, EvaluationResult]) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for name, result in sorted(metric_by_name.items()):
        if name == "case_metadata":
            continue
        value = _result_numeric_value(result)
        if value is not None:
            snapshot[name] = round(value, 6)
    return snapshot


def _case_metadata_details(metric_by_name: dict[str, EvaluationResult]) -> dict[str, object]:
    case_metadata = metric_by_name.get("case_metadata")
    if case_metadata is None or not isinstance(case_metadata.metric_detail_json, dict):
        return {}
    return case_metadata.metric_detail_json


def _item_answer_generated(
    item: EvaluationRunItem,
    metric_by_name: dict[str, EvaluationResult],
) -> bool:
    summary_value = (item.metric_summary_json or {}).get("answer_generated")
    if isinstance(summary_value, bool):
        return summary_value
    metadata_value = _case_metadata_details(metric_by_name).get("answer_generated")
    if isinstance(metadata_value, bool):
        return metadata_value
    return item.strategy_type in ASK_ONLY_EVALUATION_STRATEGY_VALUES


def _applicable_metric_results(
    item: EvaluationRunItem,
    metric_by_name: dict[str, EvaluationResult],
) -> dict[str, EvaluationResult]:
    if _item_answer_generated(item, metric_by_name):
        return metric_by_name
    return {
        name: result
        for name, result in metric_by_name.items()
        if name not in ANSWER_GENERATION_DEPENDENT_METRICS
    }


def _is_graph_provider_skip_item(
    item: EvaluationRunItem,
    metric_by_name: dict[str, EvaluationResult],
) -> bool:
    target_metadata = _item_target_metadata(item)
    if _metadata_retrieval_strategy(target_metadata) != RetrievalStrategy.GRAPH:
        return False
    case_metadata = _case_metadata_details(metric_by_name)
    if case_metadata.get("error_code") == "graph_provider_skipped":
        return True
    for result in metric_by_name.values():
        detail = result.metric_detail_json
        if not isinstance(detail, dict):
            continue
        if _is_graph_provider_unavailable(_string_values(detail.get("reason_codes"))):
            return True
    return False


def _item_case_snapshot(item: EvaluationRunItem) -> dict[str, object]:
    payload = item.metric_summary_json
    if not isinstance(payload, dict):
        return {}
    case_snapshot = payload.get("case_snapshot")
    return case_snapshot if isinstance(case_snapshot, dict) else {}


def _item_target_metadata(item: EvaluationRunItem) -> dict[str, object]:
    payload = item.metric_summary_json
    if not isinstance(payload, dict):
        return {}
    target = payload.get("evaluation_target")
    return target if isinstance(target, dict) else {}


def _item_comparison_label(item: EvaluationRunItem) -> str:
    label = _metadata_comparison_label(_item_target_metadata(item))
    return label or item.strategy_type


def _metadata_comparison_label(value: dict[str, object]) -> str | None:
    return _safe_label_value(value.get("comparison_label"), max_length=120)


def _metadata_retrieval_strategy(value: dict[str, object]) -> RetrievalStrategy | None:
    strategy = _safe_strategy_value(value.get("retrieval_strategy"))
    return RetrievalStrategy(strategy) if strategy is not None else None


def _metadata_graph_provider(value: dict[str, object]) -> str | None:
    return _safe_graph_provider(value.get("graph_store_provider"))


def _metadata_cache_mode(value: dict[str, object]) -> EvaluationCacheMode | None:
    return _safe_cache_mode(value.get("cache_mode"))


def _safe_hash_value(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 64:
        return None
    lowered = value.lower()
    if any(char not in "0123456789abcdef" for char in lowered):
        return None
    return lowered


def _metric_score(metric_by_name: dict[str, EvaluationResult], name: str) -> float | None:
    result = metric_by_name.get(name)
    if result is None or result.metric_score is None:
        return None
    return float(result.metric_score)


def _metric_value(metric_by_name: dict[str, EvaluationResult], name: str) -> float | None:
    result = metric_by_name.get(name)
    if result is None or result.metric_value is None:
        return None
    return float(result.metric_value)


def _failure_reasons(
    item: EvaluationRunItem,
    metric_by_name: dict[str, EvaluationResult],
    settings: Settings,
) -> list[tuple[str, EvaluationFailureSeverity, list[str]]]:
    reasons: list[tuple[str, EvaluationFailureSeverity, list[str]]] = []
    no_context = _metric_score(metric_by_name, "no_context_rate")
    if item.error_code == "no_context_found" or no_context == 1.0:
        reasons.append(("no_context", EvaluationFailureSeverity.HIGH, ["no_context_found"]))
    recall = _metric_score(metric_by_name, "recall_at_k")
    if recall is not None and recall < settings.evaluation_failure_low_recall_threshold:
        reasons.append(("low_recall", EvaluationFailureSeverity.MEDIUM, ["recall_below_threshold"]))
    mrr = _metric_score(metric_by_name, "mrr")
    if mrr is not None and mrr < settings.evaluation_failure_low_mrr_threshold:
        reasons.append(("low_mrr", EvaluationFailureSeverity.MEDIUM, ["mrr_below_threshold"]))
    citation = _metric_score(metric_by_name, "citation_coverage")
    if (
        citation is not None
        and citation < settings.evaluation_failure_low_citation_coverage_threshold
    ):
        reasons.append(
            (
                "low_citation_coverage",
                EvaluationFailureSeverity.MEDIUM,
                ["citation_coverage_below_threshold"],
            )
        )
    graph_path_relevance = _metric_score(metric_by_name, "graph_path_relevance")
    if graph_path_relevance is not None and graph_path_relevance < GRAPH_QUALITY_FAILURE_THRESHOLD:
        reasons.append(
            (
                "low_graph_path_relevance",
                EvaluationFailureSeverity.MEDIUM,
                ["graph_path_relevance_below_threshold"],
            )
        )
    graph_citation = _metric_score(metric_by_name, "graph_citation_coverage")
    if graph_citation is not None and graph_citation < GRAPH_QUALITY_FAILURE_THRESHOLD:
        reasons.append(
            (
                "low_graph_citation_coverage",
                EvaluationFailureSeverity.MEDIUM,
                ["graph_citation_coverage_below_threshold"],
            )
        )
    multi_hop = _metric_score(metric_by_name, "multi_hop_answerability")
    if multi_hop is not None and multi_hop < GRAPH_QUALITY_FAILURE_THRESHOLD:
        reasons.append(
            (
                "low_multi_hop_answerability",
                EvaluationFailureSeverity.MEDIUM,
                ["multi_hop_answerability_below_threshold"],
            )
        )
    groundedness = _metric_score(metric_by_name, "groundedness")
    if (
        groundedness is not None
        and groundedness < settings.evaluation_failure_low_groundedness_threshold
    ):
        reasons.append(
            ("low_groundedness", EvaluationFailureSeverity.MEDIUM, ["groundedness_below_threshold"])
        )
    faithfulness = _metric_score(metric_by_name, "faithfulness")
    if (
        faithfulness is not None
        and faithfulness < settings.evaluation_failure_low_faithfulness_threshold
    ):
        reasons.append(
            ("low_faithfulness", EvaluationFailureSeverity.MEDIUM, ["faithfulness_below_threshold"])
        )
    strategy_accuracy = _metric_score(metric_by_name, "strategy_selection_accuracy")
    if strategy_accuracy == 0.0:
        reasons.append(
            (
                "strategy_selection_incorrect",
                EvaluationFailureSeverity.MEDIUM,
                ["strategy_selection_mismatch"],
            )
        )
    if _metric_score(metric_by_name, "budget_exhausted_rate") == 1.0:
        reasons.append(
            ("budget_exhausted", EvaluationFailureSeverity.HIGH, ["retrieval_budget_exhausted"])
        )
    latency = item.latency_ms or _metric_value(metric_by_name, "p95_latency")
    if latency is not None and latency > settings.evaluation_failure_high_latency_ms:
        reasons.append(("high_latency", EvaluationFailureSeverity.LOW, ["latency_above_threshold"]))
    if item.error_code in {"retrieval_failed", "rerank_failed", "internal_error"}:
        reasons.append(
            ("retrieval_exception", EvaluationFailureSeverity.HIGH, [str(item.error_code)])
        )
    if item.error_code == "generation_failed":
        reasons.append(
            ("generation_exception", EvaluationFailureSeverity.HIGH, ["generation_failed"])
        )
    if item.error_code == "citation_build_failed":
        reasons.append(
            ("citation_build_failed", EvaluationFailureSeverity.HIGH, ["citation_build_failed"])
        )
    if (
        item.status == "failed"
        and _metric_score(metric_by_name, "fallback_rate") == 1.0
        and item.error_code
    ):
        reasons.append(("fallback_failed", EvaluationFailureSeverity.HIGH, ["fallback_failed"]))
    return _dedupe_failures(reasons)


def _dedupe_failures(
    failures: list[tuple[str, EvaluationFailureSeverity, list[str]]],
) -> list[tuple[str, EvaluationFailureSeverity, list[str]]]:
    by_type: dict[str, tuple[str, EvaluationFailureSeverity, list[str]]] = {}
    for failure_type, severity, reason_codes in failures:
        existing = by_type.get(failure_type)
        if existing is None or _severity_rank(severity) > _severity_rank(existing[1]):
            by_type[failure_type] = (failure_type, severity, reason_codes)
    return list(by_type.values())


def _question_hash(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _promotion_key(
    *,
    run: EvaluationRun,
    item: EvaluationRunItem,
    case_key: str | None,
    question_hash: str,
    failure_type: str,
    target_metadata: dict[str, object],
) -> str:
    config = _config(run)
    dataset_identity = run.evaluation_dataset_id or cast(str, config["dataset_name"])
    comparison_label = _metadata_comparison_label(target_metadata) or item.strategy_type
    graph_provider = _metadata_graph_provider(target_metadata) or "none"
    cache_mode = _metadata_cache_mode(target_metadata)
    base = ":".join(
        [
            str(dataset_identity),
            str(case_key or item.evaluation_case_id),
            comparison_label,
            graph_provider,
            cache_mode.value if cache_mode is not None else EvaluationCacheMode.DEFAULT.value,
            failure_type,
            question_hash,
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _promotion_tags(source_tags: list[str], recommended_tags: list[str]) -> list[str]:
    tags: list[str] = []
    for tag in [*source_tags, *recommended_tags]:
        safe = str(tag).strip().lower().replace(" ", "_")[:80]
        if safe and safe not in tags:
            tags.append(safe)
    return tags[:20]


def _target_failure_tags(target_metadata: dict[str, object]) -> list[str]:
    tags: list[str] = []
    label = _metadata_comparison_label(target_metadata)
    if label is not None:
        tags.append(f"target_{label}")
    provider = _metadata_graph_provider(target_metadata)
    if provider is not None:
        tags.append(f"graph_provider_{provider}")
    cache_mode = _metadata_cache_mode(target_metadata)
    if cache_mode is not None and cache_mode != EvaluationCacheMode.DEFAULT:
        tags.append(f"cache_{cache_mode.value}")
    return tags


def _primary_failure_candidates(
    candidates: list[EvaluationFailureCandidate],
) -> list[EvaluationFailureCandidate]:
    by_item: dict[int, EvaluationFailureCandidate] = {}
    for candidate in candidates:
        existing = by_item.get(candidate.evaluation_run_item_id)
        if existing is None or _failure_candidate_priority(candidate) < _failure_candidate_priority(
            existing
        ):
            by_item[candidate.evaluation_run_item_id] = candidate
    return sorted(by_item.values(), key=lambda candidate: candidate.evaluation_run_item_id)


def _failure_candidate_priority(candidate: EvaluationFailureCandidate) -> tuple[int, int, str, str]:
    return (
        -_severity_rank(candidate.severity),
        _failure_type_priority(candidate.failure_type),
        candidate.failure_type,
        candidate.promotion_key,
    )


def _failure_type_priority(failure_type: str) -> int:
    priority = {
        "retrieval_exception": 0,
        "generation_exception": 1,
        "citation_build_failed": 2,
        "fallback_failed": 3,
        "budget_exhausted": 4,
        "strategy_selection_incorrect": 5,
        "no_context": 6,
    }
    return priority.get(failure_type, 100)


def _source_case_changed(
    source_case: PromotionSourceCase,
    candidate: EvaluationFailureCandidate,
) -> bool:
    snapshot_hash = _safe_hash_value(candidate.metric_snapshot.get("case_snapshot_hash"))
    if snapshot_hash is None:
        return False
    return snapshot_hash != _source_case_snapshot_hash(source_case)


def _source_case_snapshot_hash(source_case: PromotionSourceCase) -> str:
    return evaluation_case_snapshot_hash(
        question=source_case.question,
        expected_answer=source_case.expected_answer,
        expected_keywords=source_case.expected_keywords,
        expected_document_ids=source_case.expected_document_ids,
        expected_chunk_ids=source_case.expected_chunk_ids,
        required_citation=source_case.required_citation,
        metadata_json=source_case.metadata_json,
    )


def _promotion_metadata(
    candidate: EvaluationFailureCandidate,
    source_metadata_json: dict[str, object] | None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source": "failure_promoted",
        "source_evaluation_run_id": candidate.evaluation_run_id,
        "source_evaluation_run_item_id": candidate.evaluation_run_item_id,
        "source_evaluation_case_id": candidate.evaluation_case_id,
        "source_strategy_type": candidate.strategy_type.value,
        "failure_type": candidate.failure_type,
        "failure_reason_codes": candidate.failure_reason_codes,
        "metric_snapshot": candidate.metric_snapshot,
        "promotion_key": candidate.promotion_key,
        "question_hash": candidate.question_hash,
    }
    expected_strategy, acceptable_strategies = _expected_strategy_hints(source_metadata_json)
    if expected_strategy is not None:
        metadata["expected_strategy"] = expected_strategy
    if acceptable_strategies:
        metadata["acceptable_strategies"] = acceptable_strategies
    metadata.update(_graph_hint_metadata(source_metadata_json))
    return metadata


def _graph_hint_metadata(source_metadata_json: dict[str, object] | None) -> dict[str, object]:
    source = source_metadata_json or {}
    metadata: dict[str, object] = {}
    for key in GRAPH_PROMOTION_STRING_HINT_KEYS:
        values = _safe_graph_hint_values(source.get(key))
        if values:
            metadata[key] = values
    for key in GRAPH_PROMOTION_INT_HINT_KEYS:
        value = _metadata_positive_int(source, key)
        if value is not None and value <= 10:
            metadata[key] = value
    return metadata


def _safe_graph_hint_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = " ".join(item.replace("\x00", " ").split())
        lowered = text.lower()
        if (
            not text
            or len(text) > 120
            or any(part in lowered for part in GRAPH_PROMOTION_HINT_FORBIDDEN_PARTS)
        ):
            continue
        if text not in values:
            values.append(text)
    return values


def _failure_summary(candidates: list[EvaluationFailureCandidate]) -> dict[str, object]:
    by_type: dict[str, int] = {}
    by_strategy: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for candidate in candidates:
        by_type[candidate.failure_type] = by_type.get(candidate.failure_type, 0) + 1
        by_strategy[candidate.strategy_type.value] = (
            by_strategy.get(candidate.strategy_type.value, 0) + 1
        )
        by_severity[candidate.severity.value] = by_severity.get(candidate.severity.value, 0) + 1
    return {
        "total_count": len(candidates),
        "by_type": by_type,
        "by_strategy": by_strategy,
        "by_severity": by_severity,
    }


def _severity_rank(severity: EvaluationFailureSeverity) -> int:
    return {
        EvaluationFailureSeverity.LOW: 1,
        EvaluationFailureSeverity.MEDIUM: 2,
        EvaluationFailureSeverity.HIGH: 3,
    }[severity]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    index = int(round((len(ordered) - 1) * percentile))
    return round(ordered[min(max(index, 0), len(ordered) - 1)], 6)


def _metric_summary_json(
    metrics: list[MetricValue],
    *,
    case: EvaluationCase,
    target: EvaluationStrategyTarget,
    answer_generated: bool,
) -> dict[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "answer_generated": answer_generated,
        "case_snapshot": _case_snapshot_from_case(case),
        "evaluation_target": _target_metadata_json(target),
        "metrics": {
            metric.metric_name: (
                metric.metric_value if metric.metric_value is not None else metric.metric_score
            )
            for metric in metrics
            if (metric.metric_score is not None or metric.metric_value is not None)
            and metric.metric_name != "case_metadata"
        },
    }


def _case_snapshot_from_case(case: EvaluationCase) -> dict[str, object]:
    return {
        "question_hash": _question_hash(case.question),
        "case_snapshot_hash": evaluation_case_snapshot_hash(
            question=case.question,
            expected_answer=case.expected_answer,
            expected_keywords=case.expected_keywords,
            expected_document_ids=case.expected_document_ids,
            expected_chunk_ids=case.expected_chunk_ids,
            required_citation=case.required_citation,
            metadata_json=case.metadata_json,
        ),
    }


def _latency_breakdown_json(latency_ms: int | None) -> dict[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "total_ms": latency_ms,
        "evaluation_case_ms": latency_ms,
    }


def _retrieval_settings_snapshot(
    *,
    strategy_type: str,
    strategies: list[str],
    metrics: list[str],
    cache_modes: list[str],
    evaluation_scope: EvaluationScope,
    strategy_targets: list[EvaluationStrategyTarget],
    case_limit: int | None,
    top_k: int | None,
    rerank_top_n: int | None,
) -> dict[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "strategy_type": strategy_type,
        "strategies": strategies,
        "metrics": metrics,
        "cache_modes": cache_modes,
        "evaluation_scope": evaluation_scope,
        "strategy_targets": [_target_metadata_json(target) for target in strategy_targets],
        "case_limit": case_limit,
        "top_k": top_k,
        "rerank_top_n": rerank_top_n,
        "runner_implementation": "phase3_graph_cache_strategy_evaluation_runner",
        "strategy_runner_enabled": True,
    }


def _strategy_metrics_summary_json(
    *,
    strategies: list[str],
    strategy_comparison: list[StrategyComparisonMetric],
    metric_summary: dict[str, float],
    case_count: int,
    succeeded_count: int,
    failed_count: int,
    failure_candidates: list[EvaluationFailureCandidate] | None = None,
) -> dict[str, object]:
    by_strategy: dict[str, dict[str, object]] = {}
    for comparison in strategy_comparison:
        strategy_key = comparison.comparison_label or comparison.strategy_type
        entry = by_strategy.setdefault(
            strategy_key,
            {
                "comparison_label": strategy_key,
                "retrieval_strategy": comparison.retrieval_strategy.value
                if comparison.retrieval_strategy is not None
                else None,
                "graph_store_provider": comparison.graph_store_provider,
                "cache_mode": comparison.cache_mode.value if comparison.cache_mode else None,
                "metric_summary": {},
                "case_count": 0,
                "succeeded_count": 0,
                "failed_count": comparison.failed_count,
            },
        )
        case_value = entry.get("case_count")
        succeeded_value = entry.get("succeeded_count")
        failed_value = entry.get("failed_count")
        successful_metric_count = comparison.count + comparison.not_applicable_count
        observed_count = successful_metric_count + comparison.failed_count
        entry["case_count"] = max(case_value if isinstance(case_value, int) else 0, observed_count)
        entry["succeeded_count"] = max(
            succeeded_value if isinstance(succeeded_value, int) else 0,
            successful_metric_count,
        )
        entry["failed_count"] = max(
            failed_value if isinstance(failed_value, int) else 0,
            comparison.failed_count,
        )
        summary_value = _strategy_comparison_summary_value(comparison)
        if summary_value is not None:
            cast(dict[str, float], entry["metric_summary"])[str(comparison.metric_name)] = (
                summary_value
            )
    failure_summary = _failure_summary(failure_candidates or [])
    agentic_entries = [
        entry
        for entry in by_strategy.values()
        if entry.get("retrieval_strategy") == RetrievalStrategy.AGENTIC_ROUTER.value
        or entry.get("comparison_label") == RetrievalStrategy.AGENTIC_ROUTER.value
    ]
    agentic_metrics = _average_metric_summaries(
        [_metric_summary_mapping(entry.get("metric_summary")) for entry in agentic_entries]
    )
    agentic_summary: dict[str, object] | None = None
    if (
        any(
            _base_comparison_label(strategy) == RetrievalStrategy.AGENTIC_ROUTER.value
            for strategy in strategies
        )
        or agentic_metrics
    ):
        agentic_summary = {
            "strategy_type": RetrievalStrategy.AGENTIC_ROUTER.value,
            "case_count": sum(_safe_count(entry.get("case_count")) for entry in agentic_entries)
            or case_count,
            "fallback_rate": agentic_metrics.get("fallback_rate"),
            "budget_exhausted_rate": agentic_metrics.get("budget_exhausted_rate"),
            "strategy_selection_accuracy": agentic_metrics.get("strategy_selection_accuracy"),
            "sufficiency_score_avg": agentic_metrics.get("sufficiency_score_avg"),
            "retrieval_call_count_avg": agentic_metrics.get("retrieval_call_count_avg"),
            "no_context_rate": agentic_metrics.get("no_context_rate"),
            "p95_latency": agentic_metrics.get("p95_latency"),
        }
    payload: dict[str, object] = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "strategies": strategies,
        "metric_summary": metric_summary,
        "strategy_metrics": by_strategy,
        "provider_comparison": _provider_comparison_summary(by_strategy),
        "cache_comparison": _cache_comparison_summary(by_strategy),
        "graph_quality_summary": _graph_quality_summary(by_strategy),
        "case_count": case_count,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }
    if agentic_summary is not None:
        payload["agentic_summary"] = agentic_summary
    payload["failure_summary"] = failure_summary
    return payload


def _provider_comparison_summary(
    by_strategy: dict[str, dict[str, object]],
) -> dict[str, object]:
    summary: dict[str, object] = {}
    for label, entry in by_strategy.items():
        provider = entry.get("graph_store_provider")
        if provider not in {"postgres", "neo4j"}:
            continue
        provider_entry = cast(
            dict[str, object],
            summary.setdefault(
                str(provider),
                {
                    "labels": [],
                    "case_count": 0,
                    "succeeded_count": 0,
                    "failed_count": 0,
                    "metric_summary": {},
                    "metric_summary_by_label": {},
                },
            ),
        )
        cast(list[str], provider_entry["labels"]).append(label)
        provider_entry["case_count"] = _safe_count(provider_entry.get("case_count")) + _safe_count(
            entry.get("case_count")
        )
        provider_entry["succeeded_count"] = _safe_count(
            provider_entry.get("succeeded_count")
        ) + _safe_count(entry.get("succeeded_count"))
        provider_entry["failed_count"] = _safe_count(
            provider_entry.get("failed_count")
        ) + _safe_count(entry.get("failed_count"))
        metric_summary = entry.get("metric_summary")
        if isinstance(metric_summary, dict):
            cast(dict[str, object], provider_entry["metric_summary_by_label"])[label] = (
                _metric_summary_mapping(metric_summary)
            )
    _finalize_rollup_metric_summary(summary)
    return summary


def _cache_comparison_summary(by_strategy: dict[str, dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for label, entry in by_strategy.items():
        cache_mode = entry.get("cache_mode")
        if not isinstance(cache_mode, str) or cache_mode == EvaluationCacheMode.DEFAULT.value:
            continue
        cache_entry = cast(
            dict[str, object],
            summary.setdefault(
                cache_mode,
                {
                    "labels": [],
                    "case_count": 0,
                    "succeeded_count": 0,
                    "failed_count": 0,
                    "metric_summary": {},
                    "metric_summary_by_label": {},
                },
            ),
        )
        cast(list[str], cache_entry["labels"]).append(label)
        cache_entry["case_count"] = _safe_count(cache_entry.get("case_count")) + _safe_count(
            entry.get("case_count")
        )
        cache_entry["succeeded_count"] = _safe_count(
            cache_entry.get("succeeded_count")
        ) + _safe_count(entry.get("succeeded_count"))
        cache_entry["failed_count"] = _safe_count(cache_entry.get("failed_count")) + _safe_count(
            entry.get("failed_count")
        )
        metric_summary = entry.get("metric_summary")
        if isinstance(metric_summary, dict):
            cast(dict[str, object], cache_entry["metric_summary_by_label"])[label] = (
                _metric_summary_mapping(metric_summary)
            )
    _finalize_rollup_metric_summary(summary)
    return summary


def _finalize_rollup_metric_summary(summary: dict[str, object]) -> None:
    for entry in summary.values():
        if not isinstance(entry, dict):
            continue
        raw_by_label = entry.get("metric_summary_by_label")
        if not isinstance(raw_by_label, dict):
            continue
        label_summaries = [
            _metric_summary_mapping(value)
            for value in raw_by_label.values()
            if isinstance(value, dict)
        ]
        entry["metric_summary"] = _average_metric_summaries(label_summaries)


def _metric_summary_mapping(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    summary: dict[str, float] = {}
    for key, metric_value in value.items():
        if isinstance(metric_value, bool) or not isinstance(metric_value, int | float):
            continue
        summary[str(key)] = float(metric_value)
    return summary


def _average_metric_summaries(summaries: list[dict[str, float]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for summary in summaries:
        for key, value in summary.items():
            totals[key] = totals.get(key, 0.0) + value
            counts[key] = counts.get(key, 0) + 1
    return {key: round(total / counts[key], 6) for key, total in totals.items() if counts[key]}


def _base_comparison_label(label: str) -> str:
    return label.split("__cache_", 1)[0]


def _graph_quality_summary(by_strategy: dict[str, dict[str, object]]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for label, entry in by_strategy.items():
        if entry.get("retrieval_strategy") != RetrievalStrategy.GRAPH.value:
            continue
        metric_summary = entry.get("metric_summary")
        if not isinstance(metric_summary, dict):
            continue
        summary[label] = {
            "graph_store_provider": entry.get("graph_store_provider"),
            "cache_mode": entry.get("cache_mode"),
            "graph_path_relevance": metric_summary.get("graph_path_relevance"),
            "graph_citation_coverage": metric_summary.get("graph_citation_coverage"),
            "multi_hop_answerability": metric_summary.get("multi_hop_answerability"),
            "entity_relation_quality_summary": metric_summary.get(
                "entity_relation_quality_summary"
            ),
            "p95_latency": metric_summary.get("p95_latency"),
            "fallback_rate": metric_summary.get("fallback_rate"),
        }
    return summary


def _safe_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _strategy_comparison_summary_value(comparison: StrategyComparisonMetric) -> float | None:
    if str(comparison.metric_name) == "p95_latency":
        return comparison.p95
    return comparison.average


def _loaded_case_from_model(case: EvaluationCaseModel) -> LoadedEvaluationCase:
    return LoadedEvaluationCase(
        case=EvaluationCase(
            case_id=case.case_key,
            question=case.question,
            expected_keywords=tuple(_string_list(case.expected_keywords)),
            required_citation=case.required_citation,
            expected_answer=case.expected_answer,
            expected_document_ids=tuple(_int_list(case.expected_document_ids)),
            expected_chunk_ids=tuple(_int_list(case.expected_chunk_ids)),
            metadata_json=case.metadata_json,
        ),
        evaluation_case_id=case.evaluation_case_id,
        case_key=case.case_key,
        metadata_json=case.metadata_json,
        tags=_string_list(case.tags),
    )


def _auxiliary_decision_from_judgment(
    judgment: EvaluationAuxiliaryJudgment | None,
    *,
    case_id: str,
) -> AuxiliaryJudgeDecision | None:
    if judgment is None or judgment.status != "succeeded":
        return None
    try:
        return AuxiliaryJudgeDecision(
            case_id=case_id,
            rubric_version=cast(
                Literal["phase3.grounded_answer_judge.v1"],
                judgment.rubric_version,
            ),
            required_facts_supported=judgment.required_facts_supported,
            citation_support=judgment.citation_support,
            forbidden_claims_absent=judgment.forbidden_claims_absent,
            abstention_correct=judgment.abstention_correct,
            prompt_injection_resisted=judgment.prompt_injection_resisted,
            confidence=float(judgment.confidence) if judgment.confidence is not None else 0.0,
            reason_codes=judgment.reason_codes_json,
        )
    except (ValueError, PydanticValidationError):
        return None


def _manual_dimensions_from_row(
    row: EvaluationHumanCalibration,
) -> EvaluationManualDimensionDecision | None:
    values = (
        row.human_required_facts_supported,
        row.human_citation_support,
        row.human_forbidden_claims_absent,
        row.human_abstention_correct,
        row.human_prompt_injection_resisted,
    )
    if any(value is None for value in values):
        return None
    return EvaluationManualDimensionDecision(
        required_facts_supported=cast(str, values[0]),
        citation_support=cast(str, values[1]),
        forbidden_claims_absent=cast(str, values[2]),
        abstention_correct=cast(str, values[3]),
        prompt_injection_resisted=cast(str, values[4]),
    )


def _manual_calibration_case_contract(
    source: PromotionSourceCase,
) -> ManualCalibrationCaseContract:
    metadata = source.metadata_json if isinstance(source.metadata_json, dict) else {}
    answerable_value = metadata.get("answerable")
    answerable = (
        answerable_value
        if isinstance(answerable_value, bool)
        else "unanswerable" not in source.tags
    )
    return ManualCalibrationCaseContract(
        case_id=source.case_key,
        answerable=answerable,
        required_citation=source.required_citation,
        tags=tuple(source.tags),
    )


def _assert_case_expected_signal(
    case: EvaluationCaseModel,
    values: dict[str, object],
) -> None:
    expected_answer = values.get("expected_answer", case.expected_answer)
    expected_keywords = values.get("expected_keywords", case.expected_keywords)
    if _string_list(expected_keywords) or (isinstance(expected_answer, str) and expected_answer):
        return
    raise ValidationFailed({"expected_signal": "expected_keywords or expected_answer is required"})


def _safe_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        {str(key): item_value for key, item_value in item.items()}
        for item in value
        if isinstance(item, dict)
    ]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [int(item) for item in value if isinstance(item, int) and not isinstance(item, bool)]
