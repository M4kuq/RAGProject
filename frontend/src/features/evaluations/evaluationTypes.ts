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
  | "agentic_router"
  | "fallback_dense";
export type EvaluationRunnableStrategy = "dense" | "sparse" | "hybrid";
export type EvaluationTriggerType = "manual" | "ci" | "scheduled" | "post_deploy" | "online_sampled_trace";

export type EvaluationRunCreateRequest = {
  dataset_name: string;
  evaluation_dataset_id?: number | null;
  case_limit: number | null;
  strategy_type?: EvaluationRunnableStrategy;
  strategies?: EvaluationRunnableStrategy[];
  metrics?: string[];
  top_k?: number | null;
  rerank_top_n?: number | null;
  trigger_type?: EvaluationTriggerType;
};

export type EvaluationRunCreateResponse = {
  evaluation_run_id: number;
  job_id: number;
  status: "queued";
  strategies: EvaluationRunnableStrategy[];
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
  strategies: RetrievalStrategy[];
  metric_names: string[];
  trigger_type: EvaluationTriggerType;
  status: EvaluationStatus;
  case_count: number;
  succeeded_count: number;
  failed_count: number;
  metric_summary: Record<string, number>;
  strategy_comparison: StrategyComparisonMetric[];
  strategy_metrics_summary_json: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
};

export type EvaluationRunDetail = EvaluationRunSummary & {
  items: EvaluationRunItem[];
};

export type StrategyComparisonMetric = {
  schema_version: "phase2.evaluation.v1";
  strategy_type: RetrievalStrategy;
  metric_name: string;
  average: number | null;
  p50: number | null;
  p95: number | null;
  count: number;
  failed_count: number;
  not_applicable_count: number;
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
