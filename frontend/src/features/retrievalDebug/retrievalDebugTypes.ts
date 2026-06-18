import type { RetrievalStrategy } from "../evaluations/evaluationTypes";

export type SupportedRetrievalDebugStrategy =
  | "dense"
  | "sparse"
  | "hybrid"
  | "graph"
  | "agentic_router";
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
  context_budget_json: ContextBudgetTrace | null;
  context_compression_json: EvidencePackTrace | null;
  tool_result_compression_json: ToolResultCompressionTrace | null;
  cache_summary_json: Record<string, unknown> | null;
  rerank_score_top1: number | null;
  answer_confidence: number | null;
  groundedness_score: number | null;
  confidence_label: "High" | "Medium" | "Low" | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type ContextBudgetTrace = {
  schema_version: "phase2.context_budget.v1";
  enabled: boolean;
  budget: {
    max_context_tokens: number;
    reserve_answer_tokens: number;
    max_context_items: number;
    max_tokens_per_item: number;
    min_citation_candidates: number;
    token_estimator: "heuristic";
    preserve_source_diversity: boolean;
    drop_low_score_first: boolean;
  };
  usage: {
    estimated_prompt_tokens: number;
    estimated_context_tokens: number;
    estimated_total_input_tokens: number;
    reserve_answer_tokens: number;
    remaining_context_tokens: number;
    budget_exhausted: boolean;
  };
  items: {
    candidate_count: number;
    selected_count: number;
    dropped_count: number;
    citation_candidate_count: number;
    source_count: number;
  };
  drop_reasons: Record<string, number>;
  sources: {
    source_count: number;
    by_source: {
      source_group_key: string;
      source_label?: string | null;
      candidate_count: number;
      selected_count: number;
      dropped_count: number;
      estimated_tokens: number;
    }[];
  };
  selected_item_refs: ContextBudgetItemRef[];
  dropped_item_refs: ContextBudgetItemRef[];
};

export type ContextBudgetItemRef = {
  retrieval_run_item_id: number;
  document_chunk_id: number;
  source_label?: string | null;
  section_title?: string | null;
  page_from?: number | null;
  page_to?: number | null;
  score?: number | null;
  rank?: number | null;
  rerank_score?: number | null;
  rerank_order?: number | null;
  estimated_tokens: number;
  char_count: number;
  retrieval_source?: string | null;
  reason?: string | null;
  drop_reason?: string | null;
};

export type EvidencePackTrace = {
  schema_version: "phase2.context_compression.v1";
  enabled: boolean;
  method: "deterministic_evidence_pack";
  policy: {
    max_items?: number;
    max_items_per_source?: number;
    max_chars_per_item?: number;
    max_total_chars?: number;
    near_duplicate_threshold?: number;
    preserve_citation_candidates?: boolean;
    group_by_source?: boolean;
  };
  input: {
    candidate_context_items: number;
    selected_context_items: number;
    input_estimated_tokens: number;
    input_char_count: number;
  };
  output: {
    evidence_group_count: number;
    evidence_item_count: number;
    output_estimated_tokens: number;
    output_char_count: number;
    compression_ratio: number;
    citation_candidate_count: number;
  };
  drops: Record<string, number>;
  evidence_groups: EvidenceGroupRef[];
  evidence_item_refs: EvidenceItemRef[];
  dropped_item_refs: DroppedEvidenceRef[];
};

export type EvidenceGroupRef = {
  source_group_key: string;
  source_label?: string | null;
  document_version_id?: number | null;
  logical_document_id?: number | null;
  item_count: number;
  selected_item_count: number;
  estimated_tokens: number;
  top_score?: number | null;
  evidence_item_refs: string[];
};

export type EvidenceItemRef = {
  evidence_item_id: string;
  retrieval_run_item_id: number;
  document_chunk_id: number;
  local_citation_id: number;
  source_label?: string | null;
  section_title?: string | null;
  page_from?: number | null;
  page_to?: number | null;
  score?: number | null;
  rerank_score?: number | null;
  rank?: number | null;
  rerank_order?: number | null;
  source_group_key: string;
  evidence_text_hash: string;
  original_char_count: number;
  output_char_count: number;
  estimated_tokens: number;
  citation_candidate: boolean;
  compression_method: string;
  compression_reason?: string | null;
  retrieval_source?: string | null;
};

export type DroppedEvidenceRef = {
  retrieval_run_item_id: number;
  document_chunk_id: number;
  source_label?: string | null;
  rank?: number | null;
  rerank_order?: number | null;
  estimated_tokens: number;
  original_char_count: number;
  drop_reason: string;
};

export type ToolResultCompressionTrace = {
  schema_version: "phase2.tool_result_compression.v1";
  enabled: boolean;
  budget: {
    max_items_per_tool: number;
    max_total_items_per_turn: number;
    max_snippet_chars: number;
    max_tokens_per_tool: number;
    max_total_tool_result_tokens: number;
    token_estimator: "heuristic";
    drop_low_score_first: boolean;
    group_by_source: boolean;
    reject_oversized_output: boolean;
  };
  summary: {
    tool_call_count: number;
    search_tool_call_count: number;
    original_item_count: number;
    output_item_count: number;
    dropped_item_count: number;
    estimated_tokens_before: number;
    estimated_tokens_after: number;
    compression_ratio: number;
    budget_exhausted: boolean;
    repeated_result_count: number;
    oversized_rejected_count: number;
  };
  drop_reasons: Record<string, number>;
  by_tool: ToolResultByToolTrace[];
  item_refs: ToolResultItemRef[];
  dropped_item_refs: DroppedToolResultRef[];
};

export type ToolResultByToolTrace = {
  tool_call_id: string;
  tool_name: string;
  status: "succeeded" | "failed";
  original_item_count: number;
  output_item_count: number;
  dropped_item_count: number;
  estimated_tokens_before: number;
  estimated_tokens_after: number;
  compression_ratio: number;
  drop_reasons: Record<string, number>;
  compression_methods: Record<string, number>;
  budget_exhausted: boolean;
  repeated_result: boolean;
  oversized_rejected: boolean;
  error_code?: string | null;
};

export type ToolResultItemRef = {
  tool_call_id: string;
  tool_name: string;
  retrieval_run_item_id?: number | null;
  document_chunk_id: number;
  source_label?: string | null;
  section_title?: string | null;
  page_from?: number | null;
  page_to?: number | null;
  rank?: number | null;
  retrieval_score?: number | null;
  rerank_score?: number | null;
  fusion_score?: number | null;
  citation_candidate: boolean;
  snippet_hash: string;
  original_char_count: number;
  snippet_char_count: number;
  estimated_tokens: number;
  source_group_key: string;
  compression_method: string;
};

export type DroppedToolResultRef = {
  tool_call_id: string;
  tool_name: string;
  retrieval_run_item_id?: number | null;
  document_chunk_id?: number | null;
  source_label?: string | null;
  rank?: number | null;
  estimated_tokens: number;
  original_char_count: number;
  drop_reason: string;
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

export type GraphDebugNodeRef = {
  provider: string;
  node_id: string;
  entity_id: number | null;
  safe_label: string;
  entity_type: string | null;
};

export type GraphDebugRelationRef = {
  provider: string;
  relation_id: string;
  source_node_id: string | null;
  target_node_id: string | null;
  relation_type: string;
  safe_label: string;
};

export type GraphDebugSourceMapping = {
  source_chunk_id: number;
  document_chunk_id: number;
  retrieval_run_item_id: number;
  selected_flag: boolean;
  old_version_flag: boolean;
  citation_ids: number[];
  local_citation_ids: number[];
};

export type GraphPathDebugTrace = {
  graph_retrieval_path_id: number;
  path_id: string;
  provider: string;
  validation_status: "valid" | "excluded";
  reason_codes: string[];
  safe_metadata: Record<string, unknown>;
  source_chunk_ids: number[];
  depth: number | null;
  path_score: number | null;
  safe_entity_labels: string[];
  relation_types: string[];
  node_refs: GraphDebugNodeRef[];
  relation_refs: GraphDebugRelationRef[];
  source_mappings: GraphDebugSourceMapping[];
};

export type GraphCitationCoverage = {
  path_count: number;
  valid_path_count: number;
  citable_path_count: number;
  excluded_path_count: number;
  source_chunk_count: number;
  resolved_source_chunk_count: number;
  citable_source_chunk_count: number;
  citation_source_count: number;
  source_chunk_coverage_ratio: number;
  citation_coverage_ratio: number;
  reason_codes: string[];
};

export type GraphRunDebugTrace = {
  schema_version: string;
  retrieval_run_id: number;
  graph_path_count: number;
  valid_path_count: number;
  citable_path_count: number;
  excluded_path_count: number;
  citation_source_count: number;
  coverage: GraphCitationCoverage;
  paths: GraphPathDebugTrace[];
};
