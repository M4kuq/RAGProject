import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../../lib/queryKeys";
import { createEvaluationRun, getEvaluationRunDetail, listEvaluationRuns } from "./evaluationApi";
import type { EvaluationRunCreateRequest } from "./evaluationTypes";

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
