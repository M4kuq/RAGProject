import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../../lib/queryKeys";
import {
  archiveSession,
  askRag,
  createChatSession,
  fetchChatHistory,
  fetchChatMessages,
  fetchChatSession
} from "./chatApi";
import { ChatSession, CreateChatSessionRequest, RagAskRequest, RagAskResult } from "./chatTypes";

export function useChatHistory() {
  return useQuery({
    queryKey: queryKeys.chatHistory,
    queryFn: fetchChatHistory,
    retry: false
  });
}

export function useChatSession(chatSessionId: number | null) {
  return useQuery({
    queryKey: queryKeys.chatSession(chatSessionId),
    queryFn: () => fetchChatSession(chatSessionId as number),
    enabled: chatSessionId !== null,
    retry: false
  });
}

export function useChatMessages(chatSessionId: number | null) {
  return useQuery({
    queryKey: queryKeys.chatMessages(chatSessionId),
    queryFn: () => fetchChatMessages(chatSessionId as number),
    enabled: chatSessionId !== null,
    retry: false
  });
}

export function useAskRagMutation() {
  const queryClient = useQueryClient();
  return useMutation<RagAskResult, Error, RagAskRequest>({
    mutationFn: askRag,
    onSuccess: (result) => {
      queryClient.setQueryData(queryKeys.chatSession(result.data.chat_session_id), (current: ChatSession | undefined) =>
        current ? { ...current, updated_at: result.data.assistant_message.updated_at ?? current.updated_at } : current
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.chatHistory });
    }
  });
}

export function useCreateChatSession() {
  const queryClient = useQueryClient();
  return useMutation<ChatSession, Error, CreateChatSessionRequest>({
    mutationFn: createChatSession,
    onSuccess: (session) => {
      queryClient.setQueryData(queryKeys.chatSession(session.chat_session_id), session);
      queryClient.invalidateQueries({ queryKey: queryKeys.chatHistory });
    }
  });
}

export function useCreateTemporaryChat() {
  return useCreateChatSession();
}

export function useArchiveSession() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: archiveSession,
    onSuccess: (_, chatSessionId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.chatSession(chatSessionId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.chatHistory });
    }
  });
}
