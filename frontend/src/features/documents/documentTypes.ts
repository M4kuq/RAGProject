import type { PaginationMeta } from "../../types/api";

export type DocumentStatus = "active" | "archived";
export type DocumentVersionStatus = "processing" | "ready" | "failed" | "archived";
export type DocumentDisplayStatus = "active" | "pending_review" | "processing" | "failed" | "archived";

export type DocumentListParams = {
  status?: string;
  display_status?: string;
  q?: string;
  page: number;
  page_size: number;
};

export type DocumentVersionSummary = {
  document_version_id: number;
  version_no: number;
  status: DocumentVersionStatus;
  is_active: boolean;
  display_status: DocumentDisplayStatus;
  file_name: string | null;
  mime_type: string | null;
  file_size_bytes: number | null;
  page_count: number | null;
  content_hash: string | null;
  error_code: string | null;
  chunk_count: number | null;
  created_at: string;
  updated_at: string;
};

export type DocumentItem = {
  logical_document_id: number;
  document_name: string;
  title: string;
  status: DocumentStatus;
  display_status: DocumentDisplayStatus;
  latest_version: DocumentVersionSummary | null;
  active_version: DocumentVersionSummary | null;
  created_at: string;
  updated_at: string;
};

export type DocumentDetail = DocumentItem & {
  versions: DocumentVersionSummary[];
};

export type DocumentVersionDetail = DocumentVersionSummary & {
  logical_document_id: number;
};

export type DocumentChunkItem = {
  document_chunk_id: number;
  document_version_id: number;
  chunk_index: number;
  preview: string;
  preview_truncated: boolean;
  page_from: number | null;
  page_to: number | null;
  section_title: string | null;
  token_count: number | null;
  char_count: number | null;
  modality: "text";
  chunk_hash: string | null;
  created_at: string;
};

export type DocumentUploadResponse = {
  logical_document_id: number;
  document_version_id: number;
  job_id: number;
  ingest_status: "queued";
  version_status: DocumentVersionStatus;
  display_status: DocumentDisplayStatus;
  result_code: "created";
  document: DocumentItem;
  version: DocumentVersionDetail;
};

export type DocumentVersionCreateResponse = {
  status: "created" | "duplicate_content_skipped";
  logical_document_id: number;
  document_version_id: number | null;
  job_id: number | null;
  ingest_status: "queued" | null;
  version_status: DocumentVersionStatus | null;
  display_status: DocumentDisplayStatus | null;
  matched_document_version_id: number | null;
  matched_version_no: number | null;
  reason: "duplicate_content" | null;
  version: DocumentVersionDetail | null;
};

export type DocumentApproveResponse = {
  logical_document_id: number;
  document_version_id: number;
  version_no: number;
  status: DocumentVersionStatus;
  is_active: boolean;
  display_status: DocumentDisplayStatus;
  previous_active_document_version_id: number | null;
  result_code: "approved" | "already_active";
  active_version: DocumentVersionDetail;
  qdrant_mirror_job_id: number | null;
};

export type DocumentArchiveResponse = {
  logical_document_id: number;
  status: "archived";
  display_status: "archived";
  result_code: "archived" | "already_archived";
  retrieval_eligible: false;
  qdrant_mirror_job_id: number | null;
};

export type PagedResult<T> = {
  items: T[];
  pagination?: PaginationMeta;
};

