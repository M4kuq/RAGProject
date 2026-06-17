import { apiFetch } from "../../lib/apiClient";
import type { ApiResponse } from "../../types/api";
import type {
  GraphRunDebugTrace,
  RagSearchDebugRequest,
  RagSearchDebugResponse,
  RetrievalRunDebugDetail,
  RetrievalRunDebugHistory
} from "./retrievalDebugTypes";

export async function runRagDebugSearch(
  payload: RagSearchDebugRequest
): Promise<RagSearchDebugResponse> {
  const response = await apiFetch<ApiResponse<RagSearchDebugResponse>>("/api/v1/rag/search", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return response.data;
}

export async function getRetrievalRunDebugDetail(
  retrievalRunId: number
): Promise<RetrievalRunDebugDetail> {
  const response = await apiFetch<ApiResponse<RetrievalRunDebugDetail>>(
    `/api/v1/rag/retrieval-runs/${retrievalRunId}`
  );
  return response.data;
}

export async function getRetrievalRunGraphTrace(retrievalRunId: number): Promise<GraphRunDebugTrace> {
  const response = await apiFetch<ApiResponse<GraphRunDebugTrace>>(
    `/api/v1/rag/retrieval-runs/${retrievalRunId}/graph-trace`
  );
  return response.data;
}

export async function listRetrievalRunDebugHistory(): Promise<RetrievalRunDebugHistory> {
  const response = await apiFetch<ApiResponse<RetrievalRunDebugHistory>>(
    "/api/v1/rag/retrieval-runs?limit=20"
  );
  return response.data;
}
