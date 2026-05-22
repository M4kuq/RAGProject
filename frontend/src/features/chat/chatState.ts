import { ChatMessage, RagAskConfidence, RagAskCitation } from "./chatTypes";

export type UiMessage = {
  chat_message_id: number | string;
  chat_session_id: number | null;
  role: "user" | "assistant" | "system";
  content: string;
  client_message_id: string | null;
  created_at: string;
  updated_at?: string;
  edited_flag?: boolean;
  status?: "persisted" | "optimistic" | "loading";
  citations?: RagAskCitation[];
  confidence?: RagAskConfidence | null;
  replayed?: boolean;
};

export function generateClientMessageId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `msg_${crypto.randomUUID()}`;
  }
  return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

export function mergeMessages(baseMessages: ChatMessage[], localMessages: UiMessage[]): UiMessage[] {
  const persistedClientIds = new Set(
    baseMessages.map((message) => message.client_message_id).filter((value): value is string => Boolean(value))
  );
  const localNumericIds = new Set(
    localMessages
      .map((message) => message.chat_message_id)
      .filter((value): value is number => typeof value === "number")
  );
  const baseWithoutLocalOverrides = baseMessages.filter(
    (message) => !localNumericIds.has(message.chat_message_id)
  );
  const localOnly = localMessages.filter(
    (message) =>
      message.status === "loading" ||
      !message.client_message_id ||
      !persistedClientIds.has(message.client_message_id)
  );
  return [...baseWithoutLocalOverrides, ...localOnly];
}
