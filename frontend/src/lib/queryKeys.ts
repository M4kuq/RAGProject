import type { DocumentListParams } from "../features/documents/documentTypes";
import type { JobListParams } from "../features/jobs/jobTypes";

export const queryKeys = {
  currentUser: ["auth", "me"] as const,
  chatHistory: ["chat", "history"] as const,
  chatSession: (chatSessionId: number | null) => ["chat", "session", chatSessionId] as const,
  chatMessages: (chatSessionId: number | null) => ["chat", "messages", chatSessionId] as const,
  auth: {
    me: ["auth", "me"] as const,
    csrf: ["auth", "csrf"] as const
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
  },
  evaluations: {
    all: ["evaluations"] as const,
    list: (params: { page: number; page_size: number }) => ["evaluations", "list", params] as const,
    detail: (evaluationRunId: number) => ["evaluations", "detail", evaluationRunId] as const,
    datasets: (params: { page: number; page_size: number }) =>
      ["evaluations", "datasets", params] as const,
    dataset: (evaluationDatasetId: number) => ["evaluations", "dataset", evaluationDatasetId] as const,
    cases: (evaluationDatasetId: number, params: { page: number; page_size: number }) =>
      ["evaluations", "dataset", evaluationDatasetId, "cases", params] as const
  },
  retrievalDebug: {
    all: ["retrievalDebug"] as const,
    run: (retrievalRunId: number | null) => ["retrievalDebug", "run", retrievalRunId] as const
  }
};
