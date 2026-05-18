export const queryKeys = {
  currentUser: ["auth", "me"] as const,
  chatHistory: ["chat", "history"] as const,
  chatSession: (chatSessionId: number | null) => ["chat", "session", chatSessionId] as const,
  chatMessages: (chatSessionId: number | null) => ["chat", "messages", chatSessionId] as const
};
