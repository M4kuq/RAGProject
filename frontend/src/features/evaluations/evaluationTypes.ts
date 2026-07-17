import type { PaginationMeta } from "../../types/api";
import type { JobStatus } from "../jobs/jobTypes";

export type EvaluationStatus = JobStatus;
export type RetrievalStrategy =
  | "dense"
  | "sparse"
  | "hybrid"
  | "multi_query_dense"
  | "multi_query_hybrid"
  | "metadata_filtered"
  | "version_aware"
  | "graph"
  | "agentic_router"
  | "llm_tool_orchestrator"
  | "langchain_agentic"
  | "langgraph_agentic"
  | "fallback_dense";
export type EvaluationRunnableStrategy =
  | "dense"
  | "sparse"
  | "hybrid"
  | "graph"
  | "graph_postgres"
  | "graph_neo4j"
  | "agentic_router"
  | "llm_tool_orchestrator"
  | "langchain_agentic"
  | "langgraph_agentic";
export type EvaluationTriggerType = "manual" | "ci" | "scheduled" | "post_deploy" | "online_sampled_trace";
export type EvaluationCacheMode = "default" | "disabled" | "cold" | "warm";
export type EvaluationGenerationProvider =
  | "fake"
  | "ollama"
  | "lmstudio"
  | "openai"
  | "anthropic"
  | "gemini";

export type EvaluationMetricCategory =
  | "retrieval"
  | "answer"
  | "citation"
  | "routing"
  | "graph"
  | "performance";

export type EvaluationMetricCatalogItem = {
  metric_name: string;
  category: EvaluationMetricCategory;
  display_name: string;
  description: string;
  higher_is_better: boolean;
  value_unit: "ratio" | "ms" | "count";
  alias_of: string | null;
};

export type EvaluationMetricCatalog = {
  schema_version: "phase3.evaluation_metric_taxonomy.v1";
  metrics: EvaluationMetricCatalogItem[];
};

export type JudgeOutcome = "pass" | "fail" | "uncertain" | "not_applicable";
export type JudgeReasonCode =
  | "missing_required_fact"
  | "unsupported_claim"
  | "citation_missing"
  | "citation_mismatch"
  | "incorrect_abstention"
  | "failed_to_abstain"
  | "prompt_injection_followed"
  | "low_confidence"
  | "judge_uncertain";
export type HumanDisagreementCategory =
  | "auxiliary_false_positive"
  | "auxiliary_false_negative"
  | "rubric_ambiguity"
  | "gold_case_defect";

export type AuxiliaryJudgeDecision = {
  case_id: string;
  rubric_version: "phase3.grounded_answer_judge.v1";
  required_facts_supported: JudgeOutcome;
  citation_support: JudgeOutcome;
  forbidden_claims_absent: JudgeOutcome;
  abstention_correct: JudgeOutcome;
  prompt_injection_resisted: JudgeOutcome;
  confidence: number;
  reason_codes: JudgeReasonCode[];
};

export type EvaluationHumanCalibrationUpsertRequest = {
  auxiliary_decision: AuxiliaryJudgeDecision;
  human_pass: boolean;
  disagreement_category: HumanDisagreementCategory | null;
  human_reason_codes: JudgeReasonCode[];
};

export type EvaluationHumanCalibrationTarget = {
  evaluation_run_item_id: number;
  case_id: string;
  strategy_type: RetrievalStrategy;
  status: EvaluationStatus;
  answerable: boolean;
  required_citation: boolean;
  prompt_injection: boolean;
};

export type EvaluationHumanCalibrationRecord = {
  evaluation_human_calibration_id: number;
  evaluation_run_item_id: number;
  auxiliary_decision: AuxiliaryJudgeDecision;
  human_calibration: {
    case_id: string;
    rubric_version: "phase3.grounded_answer_judge.v1";
    auxiliary_pass: boolean;
    human_pass: boolean;
    disagreement_category: HumanDisagreementCategory | null;
    reason_codes: JudgeReasonCode[];
  };
  reviewed_by: number;
  created_at: string;
  updated_at: string;
};

export type EvaluationHumanCalibrationSummary = {
  schema_version: "phase3.human_calibration.v1";
  evaluation_run_id: number;
  eligible_count: number;
  reviewed_count: number;
  agreement_rate: number | null;
  targets: EvaluationHumanCalibrationTarget[];
  records: EvaluationHumanCalibrationRecord[];
};

export type EvaluationRunCreateRequest = {
  dataset_name: string;
  evaluation_dataset_id?: number | null;
  case_limit: number | null;
  strategy_type?: EvaluationRunnableStrategy;
  strategies?: EvaluationRunnableStrategy[];
  cache_modes?: EvaluationCacheMode[];
  metrics?: string[];
  top_k?: number | null;
  rerank_top_n?: number | null;
  generation_provider?: EvaluationGenerationProvider | null;
  generation_model?: string | null;
  trigger_type?: EvaluationTriggerType;
};

export type EvaluationRunCreateResponse = {
  evaluation_run_id: number;
  job_id: number;
  status: "queued";
  strategies: string[];
};

export type EvaluationMetricResult = {
  metric_name: string;
  metric_score: number | null;
  metric_value: number | null;
  metric_label: string | null;
  details: Record<string, unknown> | null;
  metric_detail_json: Record<string, unknown> | null;
  strategy_type: RetrievalStrategy;
};

export type EvaluationRunItem = {
  evaluation_run_item_id: number;
  evaluation_case_id: number | null;
  retrieval_run_id: number | null;
  strategy_type: RetrievalStrategy;
  status: EvaluationStatus;
  faithfulness_score: number | null;
  groundedness_score: number | null;
  citation_coverage: number | null;
  context_precision: number | null;
  latency_ms: number | null;
  generation_provider: string | null;
  generation_model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  estimated_cost_usd: number | null;
  generation_latency_ms: number | null;
  latency_breakdown_json: Record<string, unknown> | null;
  metric_summary_json: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  case_id: string | null;
  case_key: string | null;
  metrics: EvaluationMetricResult[];
};

export type EvaluationRunSummary = {
  evaluation_run_id: number;
  job_id: number | null;
  evaluation_dataset_id: number | null;
  dataset_name: string;
  strategy_type: RetrievalStrategy;
  strategies: string[];
  metric_names: string[];
  trigger_type: EvaluationTriggerType;
  status: EvaluationStatus;
  case_count: number;
  succeeded_count: number;
  failed_count: number;
  metric_summary: Record<string, number>;
  strategy_comparison: StrategyComparisonMetric[];
  strategy_metrics_summary_json: Record<string, unknown> | null;
  total_estimated_cost_usd: number | null;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_tokens: number | null;
  avg_generation_latency_ms: number | null;
  generation_providers: string[];
  generation_models: string[];
  requested_generation_provider: string | null;
  requested_generation_model: string | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
};

export type EvaluationRunDetail = EvaluationRunSummary & {
  items: EvaluationRunItem[];
  failure_candidates: EvaluationFailureCandidate[];
};

export type EvaluationComparisonDirection =
  | "improved"
  | "regressed"
  | "unchanged"
  | "not_applicable";

export type EvaluationCaseTransition = "improved" | "regressed" | "unchanged" | "added" | "removed";

export type EvaluationMetricComparison = {
  metric_name: string;
  base_score: number | null;
  candidate_score: number | null;
  delta: number | null;
  direction: EvaluationComparisonDirection;
  lower_is_better: boolean;
};

export type EvaluationGenerationComparison = {
  base_estimated_cost_usd: number | null;
  candidate_estimated_cost_usd: number | null;
  cost_delta: number | null;
  cost_direction: EvaluationComparisonDirection;
  cost_lower_is_better: boolean;
  base_total_tokens: number | null;
  candidate_total_tokens: number | null;
  tokens_delta: number | null;
  tokens_direction: EvaluationComparisonDirection;
  tokens_lower_is_better: boolean;
  base_avg_generation_latency_ms: number | null;
  candidate_avg_generation_latency_ms: number | null;
  latency_delta: number | null;
  latency_direction: EvaluationComparisonDirection;
  latency_lower_is_better: boolean;
  base_providers: string[];
  base_models: string[];
  candidate_providers: string[];
  candidate_models: string[];
};

export type EvaluationCaseComparison = {
  case_id: string;
  question_hash: string | null;
  case_snapshot_hash: string | null;
  comparison_label: string | null;
  base_status: EvaluationStatus | null;
  candidate_status: EvaluationStatus | null;
  transition: EvaluationCaseTransition;
  metric_deltas: Record<string, number | null>;
};

export type EvaluationRunComparisonSummary = {
  improved_metric_count: number;
  regressed_metric_count: number;
  unchanged_metric_count: number;
  regressed_case_count: number;
  improved_case_count: number;
  common_case_count: number;
  base_only_case_count: number;
  candidate_only_case_count: number;
};

export type EvaluationRunComparison = {
  base_run: EvaluationRunSummary;
  candidate_run: EvaluationRunSummary;
  generation: EvaluationGenerationComparison;
  metrics: EvaluationMetricComparison[];
  cases: EvaluationCaseComparison[];
  summary: EvaluationRunComparisonSummary;
};

export type EvaluationFailureSeverity = "low" | "medium" | "high";

export type EvaluationFailureCandidate = {
  schema_version: "phase2.evaluation.v1";
  evaluation_run_id: number;
  evaluation_run_item_id: number;
  evaluation_case_id: number | null;
  case_key: string | null;
  question_hash: string;
  strategy_type: RetrievalStrategy;
  failure_type: string;
  severity: EvaluationFailureSeverity;
  failure_reason_codes: string[];
  metric_snapshot: Record<string, unknown>;
  recommended_tags: string[];
  promotion_key: string;
};

export type EvaluationFailurePromotionRequest = {
  target_dataset_id: number;
  failure_types?: string[] | null;
  promotion_keys?: string[] | null;
  min_severity?: EvaluationFailureSeverity;
  limit?: number;
};

export type EvaluationFailurePromotionResponse = {
  evaluation_run_id: number;
  target_dataset_id: number;
  created_count: number;
  skipped_count: number;
  items: Array<{
    promotion_key: string;
    failure_type: string;
    strategy_type: RetrievalStrategy;
    evaluation_run_item_id: number;
    evaluation_case_id: number | null;
    promoted_case_id: number | null;
    case_key: string | null;
    result_code: "created" | "already_exists" | "source_case_missing" | "source_case_changed";
  }>;
};

export type StrategyComparisonMetric = {
  schema_version: "phase2.evaluation.v1";
  strategy_type: string;
  metric_name: string;
  average: number | null;
  p50: number | null;
  p95: number | null;
  count: number;
  failed_count: number;
  not_applicable_count: number;
  comparison_label?: string | null;
  retrieval_strategy?: RetrievalStrategy | null;
  graph_store_provider?: string | null;
  cache_mode?: EvaluationCacheMode | null;
};

export type PagedEvaluationRuns = {
  items: EvaluationRunSummary[];
  pagination?: PaginationMeta;
};

export type EvaluationDataset = {
  evaluation_dataset_id: number;
  dataset_name: string;
  description: string | null;
  version: string;
  source_type: "manual" | "fixture" | "feedback_promoted" | "imported";
  status: "active" | "archived";
  metadata_json: Record<string, unknown> | null;
  case_count: number;
  created_by: number | null;
  created_at: string;
  updated_at: string;
};

export type EvaluationDatasetCreateRequest = {
  dataset_name: string;
  description?: string | null;
  version?: string;
  source_type?: "manual" | "fixture" | "feedback_promoted" | "imported";
  status?: "active" | "archived";
  metadata_json?: Record<string, unknown> | null;
};

export type EvaluationCase = {
  evaluation_case_id: number;
  evaluation_dataset_id: number;
  case_key: string;
  question: string;
  expected_answer: string | null;
  expected_keywords: string[];
  expected_document_ids: number[];
  expected_chunk_ids: number[];
  required_citation: boolean;
  tags: string[];
  metadata_json: Record<string, unknown> | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
};

export type EvaluationDatasetManifest = {
  schema_version: "phase2.evaluation_dataset.v1";
  dataset: {
    dataset_name: string;
    description: string | null;
    version: string;
    source_type: "manual" | "fixture" | "feedback_promoted" | "imported";
    status: "active" | "archived";
    metadata_json: Record<string, unknown> | null;
  };
  cases: Array<{
    case_key: string;
    question: string;
    expected_answer?: string | null;
    expected_keywords: string[];
    expected_document_ids: number[];
    expected_chunk_ids: number[];
    required_citation: boolean;
    tags: string[];
    metadata_json: Record<string, unknown> | null;
    status: "active" | "archived";
  }>;
  metric_specs: Array<Record<string, unknown>>;
};

export type PagedEvaluationDatasets = {
  items: EvaluationDataset[];
  pagination?: PaginationMeta;
};

export type PagedEvaluationCases = {
  items: EvaluationCase[];
  pagination?: PaginationMeta;
};
