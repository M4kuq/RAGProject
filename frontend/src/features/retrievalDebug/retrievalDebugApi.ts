import { apiFetch } from "../../lib/apiClient";
import type { ApiResponse } from "../../types/api";
import type {
  RagSearchDebugRequest,
  RagSearchDebugResponse,
  RetrievalRunDebugDetail
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
