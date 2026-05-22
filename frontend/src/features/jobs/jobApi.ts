import { apiFetch } from "../../lib/apiClient";
import type { ApiResponse } from "../../types/api";
import type { JobDetail, JobItem, JobListParams, JobRetryResponse, PagedJobs } from "./jobTypes";

function toQuery(params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      query.set(key, String(value));
    }
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

export async function listJobs(params: JobListParams): Promise<PagedJobs> {
  const response = await apiFetch<ApiResponse<JobItem[]>>(`/api/v1/jobs${toQuery(params)}`);
  return { items: response.data, pagination: response.meta?.pagination };
}

export async function getJobDetail(jobId: number): Promise<JobDetail> {
  const response = await apiFetch<ApiResponse<JobDetail>>(`/api/v1/jobs/${jobId}`);
  return response.data;
}

export async function retryJob(jobId: number): Promise<JobRetryResponse> {
  const response = await apiFetch<ApiResponse<JobRetryResponse>>(`/api/v1/jobs/${jobId}/retry`, {
    method: "POST"
  });
  return response.data;
}

