import type { PaginationMeta } from "../../types/api";
import type { JobStatus } from "../jobs/jobTypes";

export type EvaluationStatus = JobStatus;

export type EvaluationRunCreateRequest = {
  dataset_name: string;
  case_limit: number;
};

export type EvaluationRunCreateResponse = {
  evaluation_run_id: number;
  job_id: number;
  status: "queued";
};

export type EvaluationMetricResult = {
  metric_name: string;
  metric_score: number | null;
  metric_label: string | null;
  details: Record<string, unknown> | null;
};

export type EvaluationRunItem = {
  evaluation_run_item_id: number;
  retrieval_run_id: number | null;
  status: EvaluationStatus;
  faithfulness_score: number | null;
  groundedness_score: number | null;
  citation_coverage: number | null;
  context_precision: number | null;
  latency_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  case_id: string | null;
  metrics: EvaluationMetricResult[];
};

export type EvaluationRunSummary = {
  evaluation_run_id: number;
  job_id: number | null;
  dataset_name: string;
  status: EvaluationStatus;
  case_count: number;
  succeeded_count: number;
  failed_count: number;
  metric_summary: Record<string, number>;
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

export type PagedEvaluationRuns = {
  items: EvaluationRunSummary[];
  pagination?: PaginationMeta;
};
