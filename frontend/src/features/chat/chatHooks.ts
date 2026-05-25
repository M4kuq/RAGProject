import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../../lib/queryKeys";
import {
  archiveSession,
  askRag,
  createChatSession,
  deleteSession,
  fetchChatHistory,
  fetchChatMessages,
  fetchChatSession,
  updateChatSessionTitle
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

export function useUpdateChatSessionTitle() {
  const queryClient = useQueryClient();
  return useMutation<ChatSession, Error, { chatSessionId: number; title: string }>({
    mutationFn: ({ chatSessionId, title }) => updateChatSessionTitle(chatSessionId, title),
    onSuccess: (session) => {
      queryClient.setQueryData(queryKeys.chatSession(session.chat_session_id), session);
      queryClient.setQueryData(queryKeys.chatHistory, (current: ChatSession[] | undefined) =>
        current
          ? current.map((item) => (item.chat_session_id === session.chat_session_id ? { ...item, ...session } : item))
          : current
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.chatHistory });
    }
  });
}

export function useArchiveSession() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: archiveSession,
    onSuccess: (_, chatSessionId) => {
      queryClient.setQueryData(queryKeys.chatHistory, (current: ChatSession[] | undefined) =>
        current ? current.filter((session) => session.chat_session_id !== chatSessionId) : current
      );
      queryClient.invalidateQueries({ queryKey: queryKeys.chatSession(chatSessionId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.chatMessages(chatSessionId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.chatHistory });
    }
  });
}

export function useDeleteSession() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: deleteSession,
    onSuccess: (_, chatSessionId) => {
      queryClient.setQueryData(queryKeys.chatHistory, (current: ChatSession[] | undefined) =>
        current ? current.filter((session) => session.chat_session_id !== chatSessionId) : current
      );
      queryClient.removeQueries({ queryKey: queryKeys.chatSession(chatSessionId) });
      queryClient.removeQueries({ queryKey: queryKeys.chatMessages(chatSessionId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.chatHistory });
    }
  });
}
