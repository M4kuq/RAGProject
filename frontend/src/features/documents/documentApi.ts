import { apiFetch } from "../../lib/apiClient";
import type { ApiResponse } from "../../types/api";
import type {
  DocumentApproveResponse,
  DocumentArchiveResponse,
  DocumentChunkItem,
  DocumentDetail,
  DocumentItem,
  DocumentListParams,
  DocumentUrlIngestRequest,
  DocumentUploadResponse,
  DocumentVersionCreateResponse,
  DocumentVersionCompareResponse,
  DocumentVersionDetail,
  DocumentVersionSummary,
  PagedResult
} from "./documentTypes";

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

export async function listDocuments(params: DocumentListParams): Promise<PagedResult<DocumentItem>> {
  const response = await apiFetch<ApiResponse<DocumentItem[]>>(`/api/v1/documents${toQuery(params)}`);
  return { items: response.data, pagination: response.meta?.pagination };
}

export async function getDocumentDetail(logicalDocumentId: number): Promise<DocumentDetail> {
  const response = await apiFetch<ApiResponse<DocumentDetail>>(`/api/v1/documents/${logicalDocumentId}`);
  return response.data;
}

export async function listDocumentVersions(logicalDocumentId: number): Promise<DocumentVersionSummary[]> {
  const response = await apiFetch<ApiResponse<DocumentVersionSummary[]>>(
    `/api/v1/documents/${logicalDocumentId}/versions`
  );
  return response.data;
}

export async function getDocumentVersionDetail(
  logicalDocumentId: number,
  documentVersionId: number
): Promise<DocumentVersionDetail> {
  const response = await apiFetch<ApiResponse<DocumentVersionDetail>>(
    `/api/v1/documents/${logicalDocumentId}/versions/${documentVersionId}`
  );
  return response.data;
}

export async function compareDocumentVersions(values: {
  logicalDocumentId: number;
  baseVersionId: number;
  targetVersionId: number;
}): Promise<DocumentVersionCompareResponse> {
  const response = await apiFetch<ApiResponse<DocumentVersionCompareResponse>>(
    `/api/v1/documents/${values.logicalDocumentId}/versions/compare${toQuery({
      base_version_id: values.baseVersionId,
      target_version_id: values.targetVersionId
    })}`
  );
  return response.data;
}

export async function listDocumentChunks(
  logicalDocumentId: number,
  documentVersionId: number,
  page = 1,
  pageSize = 20
): Promise<PagedResult<DocumentChunkItem>> {
  const response = await apiFetch<ApiResponse<DocumentChunkItem[]>>(
    `/api/v1/documents/${logicalDocumentId}/versions/${documentVersionId}/chunks${toQuery({
      page,
      page_size: pageSize
    })}`
  );
  return { items: response.data, pagination: response.meta?.pagination };
}

export async function uploadDocument(values: { title: string; file: File }): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.set("title", values.title);
  formData.set("file", values.file);
  const response = await apiFetch<ApiResponse<DocumentUploadResponse>>("/api/v1/documents", {
    method: "POST",
    body: formData
  });
  return response.data;
}

export async function ingestDocumentUrl(values: DocumentUrlIngestRequest): Promise<DocumentUploadResponse> {
  const response = await apiFetch<ApiResponse<DocumentUploadResponse>>("/api/v1/documents/url", {
    method: "POST",
    body: JSON.stringify(values)
  });
  return response.data;
}

export async function uploadDocumentVersion(values: {
  logicalDocumentId: number;
  file: File;
}): Promise<DocumentVersionCreateResponse> {
  const formData = new FormData();
  formData.set("file", values.file);
  const response = await apiFetch<ApiResponse<DocumentVersionCreateResponse>>(
    `/api/v1/documents/${values.logicalDocumentId}/versions`,
    {
      method: "POST",
      body: formData
    }
  );
  return response.data;
}

export async function approveDocumentVersion(values: {
  logicalDocumentId: number;
  documentVersionId: number;
}): Promise<DocumentApproveResponse> {
  const response = await apiFetch<ApiResponse<DocumentApproveResponse>>(
    `/api/v1/documents/${values.logicalDocumentId}/versions/${values.documentVersionId}/approve`,
    { method: "POST" }
  );
  return response.data;
}

export async function archiveDocument(logicalDocumentId: number): Promise<DocumentArchiveResponse> {
  const response = await apiFetch<ApiResponse<DocumentArchiveResponse>>(
    `/api/v1/documents/${logicalDocumentId}/archive`,
    { method: "POST" }
  );
  return response.data;
}
