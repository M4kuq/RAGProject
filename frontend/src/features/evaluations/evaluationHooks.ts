import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../../lib/queryKeys";
import {
  createEvaluationRun,
  exportEvaluationDataset,
  getEvaluationDataset,
  getEvaluationRunDetail,
  importEvaluationDataset,
  listEvaluationCases,
  listEvaluationDatasets,
  listEvaluationRuns,
  promoteEvaluationFailures
} from "./evaluationApi";
import type {
  EvaluationDatasetManifest,
  EvaluationFailurePromotionRequest,
  EvaluationRunCreateRequest
} from "./evaluationTypes";

export function useEvaluationRuns(params: { page: number; page_size: number }) {
  return useQuery({
    queryKey: queryKeys.evaluations.list(params),
    queryFn: () => listEvaluationRuns(params)
  });
}

export function useEvaluationRunDetail(evaluationRunId: number) {
  return useQuery({
    queryKey: queryKeys.evaluations.detail(evaluationRunId),
    queryFn: () => getEvaluationRunDetail(evaluationRunId),
    enabled: Number.isFinite(evaluationRunId)
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

export function useEvaluationDatasets(params: { page: number; page_size: number }) {
  return useQuery({
    queryKey: queryKeys.evaluations.datasets(params),
    queryFn: () => listEvaluationDatasets(params)
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
