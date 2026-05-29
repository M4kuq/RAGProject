import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../../lib/queryKeys";
import {
  approveDocumentVersion,
  archiveDocument,
  compareDocumentVersions,
  getDocumentDetail,
  getDocumentVersionDetail,
  ingestDocumentUrl,
  listDocumentChunks,
  listDocuments,
  listDocumentVersions,
  uploadDocument,
  uploadDocumentVersion
} from "./documentApi";
import type { DocumentListParams } from "./documentTypes";

export function useDocuments(params: DocumentListParams) {
  return useQuery({
    queryKey: queryKeys.documents.list(params),
    queryFn: () => listDocuments(params)
  });
}

export function useDocumentDetail(logicalDocumentId: number) {
  return useQuery({
    queryKey: queryKeys.documents.detail(logicalDocumentId),
    queryFn: () => getDocumentDetail(logicalDocumentId),
    enabled: Number.isFinite(logicalDocumentId)
  });
}

export function useDocumentVersions(logicalDocumentId: number) {
  return useQuery({
    queryKey: queryKeys.documents.versions(logicalDocumentId),
    queryFn: () => listDocumentVersions(logicalDocumentId),
    enabled: Number.isFinite(logicalDocumentId)
  });
}

export function useDocumentVersionDetail(logicalDocumentId: number, documentVersionId: number) {
  return useQuery({
    queryKey: queryKeys.documents.version(logicalDocumentId, documentVersionId),
    queryFn: () => getDocumentVersionDetail(logicalDocumentId, documentVersionId),
    enabled: Number.isFinite(logicalDocumentId) && Number.isFinite(documentVersionId)
  });
}

export function useDocumentVersionCompare(
  logicalDocumentId: number,
  baseVersionId: number | null,
  targetVersionId: number | null,
  enabled = true
) {
  return useQuery({
    queryKey: queryKeys.documents.compare(logicalDocumentId, baseVersionId, targetVersionId),
    queryFn: () =>
      compareDocumentVersions({
        logicalDocumentId,
        baseVersionId: baseVersionId ?? 0,
        targetVersionId: targetVersionId ?? 0
      }),
    enabled:
      enabled &&
      Number.isFinite(logicalDocumentId) &&
      baseVersionId !== null &&
      targetVersionId !== null
  });
}

export function useDocumentChunks(logicalDocumentId: number, documentVersionId: number, page: number, pageSize: number) {
  return useQuery({
    queryKey: queryKeys.documents.chunks(logicalDocumentId, documentVersionId, page, pageSize),
    queryFn: () => listDocumentChunks(logicalDocumentId, documentVersionId, page, pageSize),
    enabled: Number.isFinite(logicalDocumentId) && Number.isFinite(documentVersionId)
  });
}

export function useUploadDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadDocument,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
    }
  });
}

export function useIngestDocumentUrl() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ingestDocumentUrl,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
    }
  });
}

export function useUploadDocumentVersion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: uploadDocumentVersion,
    onSuccess: (_result, variables) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.detail(variables.logicalDocumentId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
    }
  });
}

export function useApproveDocumentVersion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: approveDocumentVersion,
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.detail(result.logical_document_id) });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.documents.version(result.logical_document_id, result.document_version_id)
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
    }
  });
}

export function useArchiveDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: archiveDocument,
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.documents.detail(result.logical_document_id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
    }
  });
}
