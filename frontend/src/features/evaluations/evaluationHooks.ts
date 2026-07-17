import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../../lib/queryKeys";
import {
  compareEvaluationRuns,
  createEvaluationDataset,
  createEvaluationRun,
  exportEvaluationDataset,
  getEvaluationDataset,
  getEvaluationMetricCatalog,
  getEvaluationRunDetail,
  importEvaluationDataset,
  listEvaluationCases,
  listEvaluationDatasets,
  listEvaluationRuns,
  promoteEvaluationFailures
} from "./evaluationApi";
import type {
  EvaluationDataset,
  EvaluationDatasetCreateRequest,
  EvaluationDatasetManifest,
  EvaluationFailurePromotionRequest,
  EvaluationRunCreateRequest
} from "./evaluationTypes";

const TARGET_DATASET_PAGE_SIZE = 100;

export function useEvaluationRuns(params: { page: number; page_size: number }) {
  return useQuery({
    queryKey: queryKeys.evaluations.list(params),
    queryFn: () => listEvaluationRuns(params)
  });
}

export function useEvaluationMetricCatalog() {
  return useQuery({
    queryKey: queryKeys.evaluations.metricCatalog,
    queryFn: getEvaluationMetricCatalog,
    staleTime: Number.POSITIVE_INFINITY
  });
}

export function useEvaluationRunDetail(evaluationRunId: number) {
  return useQuery({
    queryKey: queryKeys.evaluations.detail(evaluationRunId),
    queryFn: () => getEvaluationRunDetail(evaluationRunId),
    enabled: Number.isFinite(evaluationRunId)
  });
}

export function useEvaluationRunComparison(
  baseRunId: number | null,
  candidateRunId: number | null
) {
  const enabled =
    baseRunId !== null &&
    candidateRunId !== null &&
    Number.isSafeInteger(baseRunId) &&
    Number.isSafeInteger(candidateRunId) &&
    baseRunId > 0 &&
    candidateRunId > 0;
  return useQuery({
    queryKey: queryKeys.evaluations.compare(baseRunId, candidateRunId),
    queryFn: () => compareEvaluationRuns(baseRunId ?? 0, candidateRunId ?? 0),
    enabled
  });
}

export function useCreateEvaluationRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: EvaluationRunCreateRequest) => createEvaluationRun(payload),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluations.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.evaluations.detail(result.evaluation_run_id)
      });
    }
  });
}

export function useCreateEvaluationDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: EvaluationDatasetCreateRequest) => createEvaluationDataset(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluations.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluations.activeDatasets });
    }
  });
}

export function useEvaluationDatasets(params: { page: number; page_size: number }) {
  return useQuery({
    queryKey: queryKeys.evaluations.datasets(params),
    queryFn: () => listEvaluationDatasets(params)
  });
}

export function useActiveEvaluationDatasets() {
  return useQuery({
    queryKey: queryKeys.evaluations.activeDatasets,
    queryFn: async () => {
      const activeDatasets: EvaluationDataset[] = [];
      let page = 1;
      let hasNext = true;

      while (hasNext) {
        const result = await listEvaluationDatasets({
          page,
          page_size: TARGET_DATASET_PAGE_SIZE
        });
        activeDatasets.push(...result.items.filter((dataset) => dataset.status === "active"));
        hasNext = Boolean(result.pagination?.has_next);
        page += 1;
      }

      return activeDatasets;
    }
  });
}

export function useEvaluationDataset(evaluationDatasetId: number) {
  return useQuery({
    queryKey: queryKeys.evaluations.dataset(evaluationDatasetId),
    queryFn: () => getEvaluationDataset(evaluationDatasetId),
    enabled: Number.isFinite(evaluationDatasetId)
  });
}

export function useEvaluationCases(
  evaluationDatasetId: number,
  params: { page: number; page_size: number }
) {
  return useQuery({
    queryKey: queryKeys.evaluations.cases(evaluationDatasetId, params),
    queryFn: () => listEvaluationCases(evaluationDatasetId, params),
    enabled: Number.isFinite(evaluationDatasetId)
  });
}

export function useExportEvaluationDataset(evaluationDatasetId: number) {
  return useQuery({
    queryKey: [...queryKeys.evaluations.dataset(evaluationDatasetId), "export"] as const,
    queryFn: () => exportEvaluationDataset(evaluationDatasetId),
    enabled: false
  });
}

export function useImportEvaluationDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (manifest: EvaluationDatasetManifest) => importEvaluationDataset(manifest),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluations.all });
    }
  });
}

export function usePromoteEvaluationFailures(evaluationRunId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: EvaluationFailurePromotionRequest) =>
      promoteEvaluationFailures(evaluationRunId, payload),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.evaluations.detail(evaluationRunId)
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.evaluations.dataset(result.target_dataset_id)
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.evaluations.all });
    }
  });
}
