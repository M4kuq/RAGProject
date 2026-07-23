import { apiFetch } from "../../lib/apiClient";
import type { ApiResponse } from "../../types/api";
import type {
  EvaluationCase,
  EvaluationCorpusPrepareResult,
  EvaluationCorpusReadiness,
  EvaluationDataset,
  EvaluationDatasetCreateRequest,
  EvaluationDatasetImportResult,
  EvaluationDatasetManifest,
  EvaluationDatasetValidation,
  EvaluationFailurePromotionRequest,
  EvaluationFailurePromotionResponse,
  EvaluationHumanCalibrationRecord,
  EvaluationHumanCalibrationSummary,
  EvaluationHumanCalibrationUpsertRequest,
  EvaluationMetricCatalog,
  EvaluationRunCreateRequest,
  EvaluationRunCreateResponse,
  EvaluationRunComparison,
  EvaluationRunDetail,
  EvaluationRunSummary,
  PagedEvaluationCases,
  PagedEvaluationDatasets,
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

export async function getEvaluationMetricCatalog(): Promise<EvaluationMetricCatalog> {
  const response = await apiFetch<ApiResponse<EvaluationMetricCatalog>>(
    "/api/v1/evaluations/metric-catalog"
  );
  return response.data;
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

export async function createEvaluationDataset(
  payload: EvaluationDatasetCreateRequest
): Promise<EvaluationDataset> {
  const response = await apiFetch<ApiResponse<EvaluationDataset>>("/api/v1/evaluations/datasets", {
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

export async function getEvaluationHumanCalibrations(
  evaluationRunId: number
): Promise<EvaluationHumanCalibrationSummary> {
  const response = await apiFetch<ApiResponse<EvaluationHumanCalibrationSummary>>(
    `/api/v1/evaluations/runs/${evaluationRunId}/human-calibrations`
  );
  return response.data;
}

export async function upsertEvaluationHumanCalibration(
  evaluationRunId: number,
  evaluationRunItemId: number,
  payload: EvaluationHumanCalibrationUpsertRequest
): Promise<EvaluationHumanCalibrationRecord> {
  const response = await apiFetch<ApiResponse<EvaluationHumanCalibrationRecord>>(
    `/api/v1/evaluations/runs/${evaluationRunId}/human-calibrations/${evaluationRunItemId}`,
    {
      method: "PUT",
      body: JSON.stringify(payload)
    }
  );
  return response.data;
}

export async function compareEvaluationRuns(
  baseRunId: number,
  candidateRunId: number
): Promise<EvaluationRunComparison> {
  const response = await apiFetch<ApiResponse<EvaluationRunComparison>>(
    `/api/v1/evaluations/runs/compare${toQuery({
      base: baseRunId,
      candidate: candidateRunId
    })}`
  );
  return response.data;
}

export async function promoteEvaluationFailures(
  evaluationRunId: number,
  payload: EvaluationFailurePromotionRequest
): Promise<EvaluationFailurePromotionResponse> {
  const response = await apiFetch<ApiResponse<EvaluationFailurePromotionResponse>>(
    `/api/v1/evaluations/runs/${evaluationRunId}/promote-failures`,
    {
      method: "POST",
      body: JSON.stringify(payload)
    }
  );
  return response.data;
}

export async function listEvaluationDatasets(params: {
  page: number;
  page_size: number;
}): Promise<PagedEvaluationDatasets> {
  const response = await apiFetch<ApiResponse<EvaluationDataset[]>>(
    `/api/v1/evaluations/datasets${toQuery(params)}`
  );
  return { items: response.data, pagination: response.meta?.pagination };
}

export async function getEvaluationDataset(evaluationDatasetId: number): Promise<EvaluationDataset> {
  const response = await apiFetch<ApiResponse<EvaluationDataset>>(
    `/api/v1/evaluations/datasets/${evaluationDatasetId}`
  );
  return response.data;
}

export async function listEvaluationCases(
  evaluationDatasetId: number,
  params: { page: number; page_size: number }
): Promise<PagedEvaluationCases> {
  const response = await apiFetch<ApiResponse<EvaluationCase[]>>(
    `/api/v1/evaluations/datasets/${evaluationDatasetId}/cases${toQuery(params)}`
  );
  return { items: response.data, pagination: response.meta?.pagination };
}

export async function exportEvaluationDataset(
  evaluationDatasetId: number
): Promise<EvaluationDatasetManifest> {
  const response = await apiFetch<ApiResponse<EvaluationDatasetManifest>>(
    `/api/v1/evaluations/datasets/${evaluationDatasetId}/export`
  );
  return response.data;
}

export async function validateEvaluationDataset(
  manifest: EvaluationDatasetManifest
): Promise<EvaluationDatasetValidation> {
  const response = await apiFetch<ApiResponse<EvaluationDatasetValidation>>(
    "/api/v1/evaluations/datasets/validate",
    {
      method: "POST",
      body: JSON.stringify(manifest)
    }
  );
  return response.data;
}

export async function importEvaluationDataset(
  manifest: EvaluationDatasetManifest
): Promise<EvaluationDatasetImportResult> {
  const response = await apiFetch<ApiResponse<EvaluationDatasetImportResult>>(
    "/api/v1/evaluations/datasets/import",
    {
      method: "POST",
      body: JSON.stringify(manifest)
    }
  );
  return response.data;
}

export async function prepareEvaluationDatasetCorpus(
  evaluationDatasetId: number
): Promise<EvaluationCorpusPrepareResult> {
  const response = await apiFetch<ApiResponse<EvaluationCorpusPrepareResult>>(
    `/api/v1/evaluations/datasets/${evaluationDatasetId}/corpus/prepare`,
    { method: "POST" }
  );
  return response.data;
}

export async function getEvaluationDatasetCorpusReadiness(
  evaluationDatasetId: number
): Promise<EvaluationCorpusReadiness> {
  const response = await apiFetch<ApiResponse<EvaluationCorpusReadiness>>(
    `/api/v1/evaluations/datasets/${evaluationDatasetId}/corpus/readiness`
  );
  return response.data;
}
