import { useMemo, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ChatModeBanner } from "../components/chat/ChatModeBanner";
import { MessageInput } from "../components/chat/MessageInput";
import { MessageList } from "../components/chat/MessageList";
import { useCurrentUser } from "../features/auth/authHooks";
import { generateClientMessageId, mergeMessages, UiMessage } from "../features/chat/chatState";
import { ChatMessage, ChatMode, ChatSession, RagAskResult } from "../features/chat/chatTypes";
import {
  useAskRagMutation,
  useChatMessages,
  useChatSession,
  useCreateChatSession,
  useCreateTemporaryChat
} from "../features/chat/chatHooks";
import { ApiError } from "../lib/apiClient";
import { queryKeys } from "../lib/queryKeys";

const DEFAULT_TOP_K = 20;
const DEFAULT_RERANK_TOP_N = 5;

function parseId(value: string | undefined): number | null {
  if (!value) {
    return null;
  }
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function titleFromMessage(message: string): string {
  const normalized = message.replace(/\s+/g, " ").trim();
  return normalized.length > 40 ? `${normalized.slice(0, 39)}...` : normalized || "New chat";
}

function readonlyReason(mode: ChatMode): string | null {
  if (mode === "archived") {
    return "アーカイブ済みのため送信できません。";
  }
  if (mode === "temporary_expired") {
    return "一時チャットの期限が切れたため送信できません。";
  }
  return null;
}

function safeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.code === "auth_required") {
      return "ログインが必要です。";
    }
    if (error.status === 403 || error.code === "permission_denied") {
      return "権限または CSRF の確認に失敗しました。";
    }
    if (error.status === 404 || error.code === "resource_not_found") {
      return "チャットが見つかりません。";
    }
    if (error.code === "request_in_progress") {
      return "この質問は処理中です。完了まで待ってから再読み込みしてください。";
    }
    if (error.code === "client_message_conflict") {
      return "送信状態が競合しました。再読み込みしてからもう一度送信してください。";
    }
    if (error.code === "archived_session_readonly") {
      return "アーカイブ済みのため送信できません。";
    }
    if (error.code === "temporary_session_expired") {
      return "一時チャットの期限が切れたため送信できません。";
    }
    if (error.code === "no_context_found") {
      return "回答に使える根拠が見つかりませんでした。";
    }
    if (error.status === 500 || error.status === 503) {
      return "回答生成に失敗しました。時間をおいて再送してください。";
    }
  }
  return "送信に失敗しました。時間をおいて再送してください。";
}

function isRequestInProgress(error: unknown): boolean {
  return error instanceof ApiError && error.code === "request_in_progress";
}

function isNoContext(error: unknown): boolean {
  return error instanceof ApiError && error.code === "no_context_found";
}

function mergePersistedUserMessage(current: ChatMessage[] | undefined, result: RagAskResult): ChatMessage[] {
  const existing = current ?? [];
  const returnedUser = {
    ...result.data.user_message,
    edited_flag: false,
    updated_at: result.data.user_message.updated_at ?? result.data.user_message.created_at
  };
  const withoutReturned = existing.filter(
    (message) =>
      message.chat_message_id !== returnedUser.chat_message_id &&
      message.client_message_id !== returnedUser.client_message_id
  );
  return [...withoutReturned, returnedUser];
}

export function ChatPage({ mode }: { mode: "active" | "temporary" }) {
  const params = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const routeSessionId = parseId(mode === "temporary" ? params.temporaryChatId : params.chatSessionId);
  const currentUser = useCurrentUser();
  const sessionQuery = useChatSession(routeSessionId);
  const messagesQuery = useChatMessages(routeSessionId);
  const createChat = useCreateChatSession();
  const createTemporaryChat = useCreateTemporaryChat();
  const askMutation = useAskRagMutation();
  const [question, setQuestion] = useState("");
  const [localMessages, setLocalMessages] = useState<UiMessage[]>([]);
  const [notice, setNotice] = useState<string | null>(null);

  const session = sessionQuery.data;
  const displayMode: ChatMode = session?.mode ?? (mode === "temporary" ? "temporary" : "active");
  const disabledReason = readonlyReason(displayMode);
  const routeSessionUnavailableReason =
    routeSessionId !== null && !session
      ? sessionQuery.isError
        ? safeErrorMessage(sessionQuery.error)
        : "チャット情報を読み込んでいます。"
      : null;
  const inputDisabledReason = disabledReason ?? routeSessionUnavailableReason;
  const messages = useMemo(
    () => mergeMessages(messagesQuery.data ?? [], localMessages),
    [localMessages, messagesQuery.data]
  );
  const isSending = askMutation.isPending || createChat.isPending || createTemporaryChat.isPending;

  async function ensureSession(message: string): Promise<ChatSession> {
    if (session) {
      return session;
    }
    if (routeSessionId !== null) {
      throw new ApiError({
        code: sessionQuery.isError ? "session_unavailable" : "session_loading",
        message: "Chat session is not ready.",
        requestId: null,
        status: sessionQuery.isError ? 404 : 409
      });
    }
    const creator = mode === "temporary" ? createTemporaryChat : createChat;
    const created = await creator.mutateAsync({
      title: titleFromMessage(message),
      temporary_flag: mode === "temporary"
    });
    navigate(mode === "temporary" ? `/chat/temp/${created.chat_session_id}` : `/chat/${created.chat_session_id}`, {
      replace: true
    });
    return created;
  }

  async function submitQuestion() {
    const message = question.trim();
    if (!message || inputDisabledReason || isSending) {
      return;
    }

    setNotice(null);
    const clientMessageId = generateClientMessageId();
    const now = new Date().toISOString();
    const optimisticUser: UiMessage = {
      chat_message_id: `optimistic-${clientMessageId}`,
      chat_session_id: routeSessionId,
      role: "user",
      content: message,
      client_message_id: clientMessageId,
      created_at: now,
      updated_at: now,
      edited_flag: false,
      status: "optimistic"
    };
    const loadingAssistant: UiMessage = {
      chat_message_id: `loading-${clientMessageId}`,
      chat_session_id: routeSessionId,
      role: "assistant",
      content: "",
      client_message_id: null,
      created_at: now,
      updated_at: now,
      edited_flag: false,
      status: "loading"
    };

    setQuestion("");
    setLocalMessages((current) => [...current, optimisticUser, loadingAssistant]);

    let targetSessionId: number | null = session?.chat_session_id ?? routeSessionId;
    try {
      const targetSession = await ensureSession(message);
      targetSessionId = targetSession.chat_session_id;
      const result = await askMutation.mutateAsync({
        chat_session_id: targetSession.chat_session_id,
        client_message_id: clientMessageId,
        message,
        top_k: DEFAULT_TOP_K,
        rerank_top_n: DEFAULT_RERANK_TOP_N
      });
      queryClient.setQueryData(queryKeys.chatMessages(targetSession.chat_session_id), (current: ChatMessage[] | undefined) =>
        mergePersistedUserMessage(current, result)
      );
      setLocalMessages((current) =>
        current.filter(
          (item) =>
            item.chat_message_id !== `optimistic-${clientMessageId}` && item.chat_message_id !== `loading-${clientMessageId}`
        )
      );
      setLocalMessages((current) => [
        ...current,
        {
          ...result.data.assistant_message,
          client_message_id: null,
          edited_flag: false,
          updated_at: result.data.assistant_message.updated_at ?? result.data.assistant_message.created_at,
          citations: result.data.citations,
          confidence: result.data.confidence,
          replayed: Boolean(result.meta.replayed),
          status: "persisted"
        }
      ]);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
      }
      if (isRequestInProgress(error)) {
        setLocalMessages((current) =>
          current.filter(
            (item) =>
              item.chat_message_id !== `optimistic-${clientMessageId}` && item.chat_message_id !== `loading-${clientMessageId}`
          )
        );
      } else if (isNoContext(error)) {
        setLocalMessages((current) => current.filter((item) => item.chat_message_id !== `loading-${clientMessageId}`));
        if (targetSessionId !== null) {
          queryClient.invalidateQueries({ queryKey: queryKeys.chatMessages(targetSessionId) });
        }
      } else {
        setLocalMessages((current) => current.filter((item) => item.chat_message_id !== `loading-${clientMessageId}`));
      }
      setNotice(safeErrorMessage(error));
    }
  }

  if (currentUser.error instanceof ApiError && currentUser.error.status === 401) {
    return <Navigate to="/login" replace />;
  }

  return (
    <main className="workspace">
      <header className="chat-header">
        <div>
          <h1>{session?.title ?? (mode === "temporary" ? "Temporary Chat" : "Chat")}</h1>
          <p>{currentUser.data ? `${currentUser.data.display_name} / ${currentUser.data.role}` : "認証確認中"}</p>
        </div>
        {displayMode !== "active" ? <span className="mode-badge">{displayMode}</span> : null}
      </header>
      <ChatModeBanner mode={displayMode} />
      {sessionQuery.isError ? <p className="error">{safeErrorMessage(sessionQuery.error)}</p> : null}
      {notice ? (
        <p className="error" role="alert">
          {notice}
        </p>
      ) : null}
      {messagesQuery.isLoading ? <p className="notice">メッセージを読み込んでいます...</p> : null}
      <MessageList messages={messages} />
      <MessageInput
        disabled={Boolean(inputDisabledReason) || currentUser.isLoading || currentUser.isError}
        disabledReason={inputDisabledReason}
        isSending={isSending}
        onChange={setQuestion}
        onSubmit={submitQuestion}
        value={question}
      />
    </main>
  );
}
