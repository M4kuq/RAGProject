import type { DocumentSourceLocator } from "../documents/documentTypes";

export type ChatMode = "active" | "archived" | "temporary" | "temporary_expired";
export type ChatMessageRole = "user" | "assistant" | "system";
export type RagStrategy = "dense" | "hybrid" | "agentic_router" | "llm_tool_orchestrator";

export type ChatSession = {
  chat_session_id: number;
  title: string;
  status: "active" | "archived";
  display_status: ChatMode;
  mode: ChatMode;
  temporary_flag: boolean;
  ttl_expires_at: string | null;
  created_at: string;
  updated_at: string;
  tags?: { chat_session_id: number; tag_name: string; created_at: string | null }[];
};

export type ChatMessage = {
  chat_message_id: number;
  chat_session_id: number;
  role: ChatMessageRole;
  content: string;
  client_message_id: string | null;
  citations?: RagAskCitation[];
  confidence?: RagAskConfidence | null;
  retrieval_summary?: RagAskRetrievalSummary | null;
  edited_flag?: boolean;
  replayed?: boolean;
  created_at: string;
  updated_at?: string;
};

export type RagAskCitation = {
  citation_id: number;
  local_citation_id: number;
  document_chunk_id: number;
  source_label: string;
  snippet: string;
  page_from: number | null;
  page_to: number | null;
  section_title: string | null;
  old_version_flag: boolean;
};

export type RagCitationSource = DocumentSourceLocator & {
  citation_id: number;
  local_citation_id: number;
};

export type RagAskConfidence = {
  answer_confidence: number;
  groundedness_score: number;
  confidence_label: "High" | "Medium" | "Low";
};

export type RagAskRetrievalSummary = {
  retrieval_run_id: number;
  strategy_type: RagStrategy | "sparse";
  selected_strategy: string | null;
  execution_strategy: string | null;
  tools_used: string[];
  fallback_used: boolean | null;
  no_context: boolean | null;
};

export type RagAskRequest = {
  chat_session_id: number;
  client_message_id: string;
  message: string;
  model_key?: string;
  top_k?: number;
  rerank_top_n?: number;
  strategy?: RagStrategy;
};

export type RagAskResponse = {
  chat_session_id: number;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  citations: RagAskCitation[];
  confidence: RagAskConfidence | null;
  retrieval_summary: RagAskRetrievalSummary;
  retrieval_run_id: number;
};

export type RagAskResult = {
  data: RagAskResponse;
  meta: { request_id?: string; replayed?: boolean };
};

export type ChatHistoryResponse = {
  data: ChatSession[];
};

export type ChatMessagesResponse = {
  data: ChatMessage[];
};

export type CreateChatSessionRequest = {
  title?: string;
  temporary_flag?: boolean;
};
