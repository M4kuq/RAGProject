import { useMutation, useQuery } from "@tanstack/react-query";
import { queryKeys } from "../../lib/queryKeys";
import {
  getRetrievalRunGraphTrace,
  getRetrievalRunDebugDetail,
  listRetrievalRunDebugHistory,
  runRagDebugSearch
} from "./retrievalDebugApi";
import type { RagSearchDebugRequest } from "./retrievalDebugTypes";

export function useRagDebugSearch() {
  return useMutation({
    mutationFn: (payload: RagSearchDebugRequest) => runRagDebugSearch(payload)
  });
}

export function useRetrievalRunDebugDetail(retrievalRunId: number | null) {
  return useQuery({
    queryKey: queryKeys.retrievalDebug.run(retrievalRunId),
    queryFn: () => getRetrievalRunDebugDetail(retrievalRunId as number),
    enabled: retrievalRunId !== null && Number.isFinite(retrievalRunId)
  });
}

export function useRetrievalRunGraphTrace(retrievalRunId: number | null) {
  return useQuery({
    queryKey: queryKeys.retrievalDebug.graphTrace(retrievalRunId),
    queryFn: () => getRetrievalRunGraphTrace(retrievalRunId as number),
    enabled: retrievalRunId !== null && Number.isFinite(retrievalRunId)
  });
}

export function useRetrievalRunDebugHistory() {
  return useQuery({
    queryKey: queryKeys.retrievalDebug.history,
    queryFn: listRetrievalRunDebugHistory
  });
}
