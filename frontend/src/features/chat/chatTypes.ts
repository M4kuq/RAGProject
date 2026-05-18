export type ChatMode = "active" | "archived" | "temporary" | "temporary_expired";
export type ChatMessageRole = "user" | "assistant" | "system";

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
  edited_flag?: boolean;
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

export type RagAskConfidence = {
  answer_confidence: number;
  groundedness_score: number;
  confidence_label: "High" | "Medium" | "Low";
};

export type RagAskRequest = {
  chat_session_id: number;
  client_message_id: string;
  message: string;
  top_k?: number;
  rerank_top_n?: number;
};

export type RagAskResponse = {
  chat_session_id: number;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  citations: RagAskCitation[];
  confidence: RagAskConfidence | null;
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
