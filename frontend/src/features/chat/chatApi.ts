import { apiFetch } from "../../lib/apiClient";
import {
  ChatHistoryResponse,
  ChatMessage,
  ChatMessagesResponse,
  ChatSession,
  CreateChatSessionRequest,
  RagAskRequest,
  RagAskResult
} from "./chatTypes";

type ApiResponse<T> = {
  data: T;
};

export async function fetchChatHistory(): Promise<ChatSession[]> {
  const response = await apiFetch<ChatHistoryResponse>("/api/v1/chat/sessions?page=1&page_size=50");
  return response.data;
}

export async function fetchChatSession(chatSessionId: number): Promise<ChatSession> {
  const response = await apiFetch<ApiResponse<ChatSession>>(`/api/v1/chat/sessions/${chatSessionId}`);
  return response.data;
}

export async function fetchChatMessages(chatSessionId: number): Promise<ChatMessage[]> {
  const response = await apiFetch<ChatMessagesResponse>(
    `/api/v1/chat/sessions/${chatSessionId}/messages?page=1&page_size=100`
  );
  return response.data;
}

export async function createChatSession(payload: CreateChatSessionRequest): Promise<ChatSession> {
  const response = await apiFetch<ApiResponse<ChatSession>>("/api/v1/chat/sessions", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return response.data;
}

export async function updateChatSessionTitle(chatSessionId: number, title: string): Promise<ChatSession> {
  const response = await apiFetch<ApiResponse<ChatSession>>(`/api/v1/chat/sessions/${chatSessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ title })
  });
  return response.data;
}

export async function archiveSession(chatSessionId: number): Promise<void> {
  await apiFetch<ApiResponse<{ result_code: string }>>(`/api/v1/chat/sessions/${chatSessionId}/archive`, {
    method: "POST"
  });
}

export async function deleteSession(chatSessionId: number): Promise<void> {
  await apiFetch<ApiResponse<{ result_code: string }>>(`/api/v1/chat/sessions/${chatSessionId}`, {
    method: "DELETE"
  });
}

export async function askRag(payload: RagAskRequest): Promise<RagAskResult> {
  return apiFetch<RagAskResult>("/api/v1/rag/ask", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}
