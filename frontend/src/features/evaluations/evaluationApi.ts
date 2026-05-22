import { apiFetch } from "../../lib/apiClient";
import type { ApiResponse } from "../../types/api";
import type {
  EvaluationRunCreateRequest,
  EvaluationRunCreateResponse,
  EvaluationRunDetail,
  EvaluationRunSummary,
  PagedEvaluationRuns
} from "./evaluationTypes";

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

export async function createEvaluationRun(
  payload: EvaluationRunCreateRequest
): Promise<EvaluationRunCreateResponse> {
  const response = await apiFetch<ApiResponse<EvaluationRunCreateResponse>>("/api/v1/evaluations/runs", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return response.data;
}

export async function listEvaluationRuns(params: {
  page: number;
  page_size: number;
}): Promise<PagedEvaluationRuns> {
  const response = await apiFetch<ApiResponse<EvaluationRunSummary[]>>(
    `/api/v1/evaluations/runs${toQuery(params)}`
  );
  return { items: response.data, pagination: response.meta?.pagination };
}

export async function getEvaluationRunDetail(evaluationRunId: number): Promise<EvaluationRunDetail> {
  const response = await apiFetch<ApiResponse<EvaluationRunDetail>>(
    `/api/v1/evaluations/runs/${evaluationRunId}`
  );
  return response.data;
}
