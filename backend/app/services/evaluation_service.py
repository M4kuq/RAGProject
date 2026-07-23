Warning: truncated output (original token count: 57831)
Total output lines: 5912

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
                calculated_human_pass = grounded_answer_pass(contract, human_decision…27831 tokens truncated…if _is_cacheable_target(target):
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
