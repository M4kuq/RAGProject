import type { PaginationMeta } from "../../types/api";

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";

export type JobListParams = {
  status?: string;
  job_type?: string;
  target_type?: string;
  target_id?: number;
  page: number;
  page_size: number;
};

export type JobPayloadView = {
  payload: Record<string, unknown>;
  payload_redacted: true;
};

export type JobItem = {
  job_id: number;
  job_type: string;
  status: JobStatus;
  priority: number;
  target_type: string | null;
  target_id: number | null;
  retry_of_job_id: number | null;
  retry_count: number;
  created_by: number | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  error_code: string | null;
  error_message: string | null;
  payload_view: JobPayloadView;
};

export type JobDetail = JobItem & {
  locked_at: string | null;
  lease_expires_at: string | null;
  result_json: Record<string, unknown> | null;
  source_job_id: number | null;
  active_retry_job_id: number | null;
};

export type JobRetryResponse = {
  result_code: "retry_created";
  job_id: number;
  source_job_id: number;
  status: "queued";
  retry_count: number;
};

export type PagedJobs = {
  items: JobItem[];
  pagination?: PaginationMeta;
};

