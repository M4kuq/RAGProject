import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../../lib/queryKeys";
import { getJobDetail, listJobs, retryJob } from "./jobApi";
import type { JobListParams } from "./jobTypes";

export function useJobs(params: JobListParams) {
  return useQuery({
    queryKey: queryKeys.jobs.list(params),
    queryFn: () => listJobs(params)
  });
}

export function useJobDetail(jobId: number) {
  return useQuery({
    queryKey: queryKeys.jobs.detail(jobId),
    queryFn: () => getJobDetail(jobId),
    enabled: Number.isFinite(jobId)
  });
}

export function useRetryJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: retryJob,
    onSuccess: (result, jobId) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.detail(jobId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.detail(result.job_id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
    }
  });
}
