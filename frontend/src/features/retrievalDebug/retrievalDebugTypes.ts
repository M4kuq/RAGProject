import type { RetrievalStrategy } from "../evaluations/evaluationTypes";

export type SupportedRetrievalDebugStrategy = "dense" | "sparse" | "hybrid" | "agentic_router";
export type FusionMethod = "rrf" | "weighted";

export type RagSearchDebugRequest = {
  query: string;
  top_k: number;
  rerank_top_n: number;
  strategy: SupportedRetrievalDebugStrategy;
};

export type RetrievalScoreSummary = {
  requested_top_k: number;
  qdrant_candidate_count: number;
  sparse_candidate_count?: number | null;
  post_filter_candidate_count: number;
  selected_count: number;
  excluded_by_rdb_check_count: number;
  top1_retrieval_score?: number | null;
  top3_avg_retrieval_score?: number | null;
  top1_rerank_score?: number | null;
  [key: string]: unknown;
};

export type RagSearchDebugItem = {
  retrieval_run_item_id: number;
  document_chunk_id: number;
  source_label: string;
  snippet: string;
  page_from: number | null;
  page_to: number | null;
  retrieval_score: number;
  rerank_score: number | null;
  rank_order: number;
  rerank_order: number | null;
  selected_flag: boolean;
  payload_snapshot: Record<string, unknown>;
};

export type RagSearchDebugResponse = {
  retrieval_run_id: number;
  status: "succeeded";
  retrieval_score_summary: RetrievalScoreSummary;
  items: RagSearchDebugItem[];
};

export type RetrievalRunDebugSummary = {
  retrieval_run_id: number;
  origin_type: "chat" | "standalone";
  chat_session_id: number | null;
  request_message_id: number | null;
  status: "running" | "succeeded" | "failed";
  strategy_type: RetrievalStrategy;
  error_code: string | null;
  query_hash: string | null;
  top_k: number | null;
  retrieval_score_summary: Record<string, unknown> | null;
  query_plan_json: Record<string, unknown> | null;
  strategy_decision_json: Record<string, unknown> | null;
  latency_breakdown_json: Record<string, unknown> | null;
  retrieval_settings_json: Record<string, unknown> | null;
  rerank_score_top1: number | null;
  answer_confidence: number | null;
  groundedness_score: number | null;
  confidence_label: "High" | "Medium" | "Low" | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type RetrievalRunDebugItem = {
  retrieval_run_item_id: number;
  document_chunk_id: number;
  retrieval_score: number;
  rerank_score: number | null;
  rank_order: number;
  rerank_order: number | null;
  selected_flag: boolean;
  retrieval_source: string | null;
  payload_snapshot: Record<string, unknown> | null;
  score_breakdown_json: Record<string, unknown> | null;
  source_label: string | null;
  page_from: number | null;
  page_to: number | null;
  old_version_flag: boolean | null;
  created_at: string;
};

export type RetrievalRunDebugDetail = {
  retrieval_run: RetrievalRunDebugSummary;
  items: RetrievalRunDebugItem[];
};

export type RetrievalRunDebugHistory = {
  items: RetrievalRunDebugSummary[];
};
