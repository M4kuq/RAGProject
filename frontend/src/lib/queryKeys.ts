import type { DocumentListParams } from "../features/documents/documentTypes";
import type { JobListParams } from "../features/jobs/jobTypes";

export const queryKeys = {
  currentUser: ["auth", "me"] as const,
  chatHistory: ["chat", "history"] as const,
  chatSession: (chatSessionId: number | null) => ["chat", "session", chatSessionId] as const,
  chatMessages: (chatSessionId: number | null) => ["chat", "messages", chatSessionId] as const,
  auth: {
    me: ["auth", "me"] as const
  },
  documents: {
    all: ["documents"] as const,
    list: (params: DocumentListParams) => ["documents", "list", params] as const,
    detail: (logicalDocumentId: number) => ["documents", "detail", logicalDocumentId] as const,
    versions: (logicalDocumentId: number) => ["documents", "versions", logicalDocumentId] as const,
    version: (logicalDocumentId: number, documentVersionId: number) =>
      ["documents", "version", logicalDocumentId, documentVersionId] as const,
    chunks: (logicalDocumentId: number, documentVersionId: number, page: number, pageSize: number) =>
      ["documents", "chunks", logicalDocumentId, documentVersionId, page, pageSize] as const
  },
  jobs: {
    all: ["jobs"] as const,
    list: (params: JobListParams) => ["jobs", "list", params] as const,
    detail: (jobId: number) => ["jobs", "detail", jobId] as const
  }
};
