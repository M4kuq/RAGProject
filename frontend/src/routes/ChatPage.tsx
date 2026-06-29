import { type MouseEvent, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ChatModeBanner } from "../components/chat/ChatModeBanner";
import { MessageInput } from "../components/chat/MessageInput";
import { MessageList } from "../components/chat/MessageList";
import { useCurrentUser } from "../features/auth/authHooks";
import { generateClientMessageId, mergeMessages, UiMessage } from "../features/chat/chatState";
import { ChatMessage, ChatMode, ChatSession, RagAskResult, RagStrategy } from "../features/chat/chatTypes";
import {
  useArchiveSession,
  useAskRagMutation,
  useChatHistory,
  useChatMessages,
  useChatSession,
  useCreateChatSession,
  useCreateTemporaryChat,
  useDeleteSession,
  useUpdateChatSessionTitle
} from "../features/chat/chatHooks";
import { ApiError } from "../lib/apiClient";
import { queryKeys } from "../lib/queryKeys";

const DEFAULT_TOP_K = 20;
const DEFAULT_RERANK_TOP_N = 5;
const DEFAULT_MODEL = "lmstudio:qwen3.5-9b";
const MODEL_OPTIONS = [
  { value: DEFAULT_MODEL, label: "Local Qwen3.5" },
  { value: "openai:gpt-5.5", label: "GPT 5.5" },
  { value: "openai:gpt-5.4", label: "GPT 5.4" },
  { value: "anthropic:claude-sonnet-4-20250514", label: "Claude" },
  { value: "gemini:gemini-2.5-flash", label: "Gemini" }
];
const RAG_STRATEGY_OPTIONS = [
  {
    value: "llm_tool_orchestrator" as const,
    label: "Auto",
    description: "LLM Agentic RAG: the model chooses dense, sparse, or hybrid retrieval tools within a budget."
  },
  {
    value: "langchain_agentic" as const,
    label: "LangChain Agentic",
    description: "LangChain Agentic RAG: the same retrieval tools are orchestrated through LangChain runnables."
  },
  {
    value: "langgraph_agentic" as const,
    label: "LangGraph Agentic",
    description: "LangGraph Agentic RAG: the same retrieval tools are orchestrated through a LangGraph StateGraph."
  },
  {
    value: "graph_neo4j" as const,
    label: "GraphRAG",
    description: "Neo4j graph retrieval with safe fallback when the read model is unavailable."
  },
  {
    value: "graph_postgres" as const,
    label: "GraphRAG (Postgres)",
    description: "PostgreSQL graph retrieval using the source-of-truth graph index."
  },
  {
    value: "dense" as const,
    label: "Normal RAG",
    description: "Dense vector retrieval followed by answer generation."
  },
  {
    value: "hybrid" as const,
    label: "Hybrid RAG",
    description: "Dense vector retrieval plus sparse keyword retrieval with score fusion."
  },
  {
    value: "agentic_router" as const,
    label: "Agentic Router",
    description: "LLM plannerで検索戦略を選び、失敗時はルールベースに戻して回答生成します。"
  }
];
const MODEL_STORAGE_KEY = "rag_selected_model";

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
    return "This chat is archived and can only be read.";
  }
  if (mode === "temporary_expired") {
    return "This temporary chat has expired.";
  }
  return null;
}

function safeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "csrf_invalid" || error.code === "csrf_missing") {
      return "Your session protection token expired. Reload and try again.";
    }
    if (error.status === 401 || error.code === "auth_required") {
      return "Please sign in again.";
    }
    if (error.status === 403 || error.code === "permission_denied") {
      return "You do not have permission, or the CSRF check failed.";
    }
    if (error.status === 404 || error.code === "resource_not_found") {
      return "This chat could not be found.";
    }
    if (error.code === "request_in_progress") {
      return "This question is already being processed. Wait for it to finish, then reload.";
    }
    if (error.code === "unsupported_model") {
      return "This model is not available in the current local configuration.";
    }
    if (error.code === "strategy_not_enabled") {
      return "This RAG mode is not enabled in the current local configuration.";
    }
    if (error.code === "client_message_conflict") {
      return "The message state conflicted with another request. Reload and try again.";
    }
    if (error.code === "archived_session_readonly") {
      return "This chat is archived and can only be read.";
    }
    if (error.code === "temporary_session_expired") {
      return "This temporary chat has expired.";
    }
    if (error.code === "no_context_found") {
      return "No usable context was found for this question.";
    }
    if (error.status === 500 || error.status === 503) {
      return "The answer could not be generated. Wait a moment and try again.";
    }
  }
  return "The message could not be sent. Wait a moment and try again.";
}

function isRequestInProgress(error: unknown): boolean {
  return error instanceof ApiError && error.code === "request_in_progress";
}

function isNoContext(error: unknown): boolean {
  return error instanceof ApiError && error.code === "no_context_found";
}

function compareMessageTimeline(left: ChatMessage, right: ChatMessage): number {
  const leftTime = new Date(left.created_at).getTime();
  const rightTime = new Date(right.created_at).getTime();
  if (leftTime !== rightTime && !Number.isNaN(leftTime) && !Number.isNaN(rightTime)) {
    return leftTime - rightTime;
  }
  return left.chat_message_id - right.chat_message_id;
}

function mergePersistedAskMessages(current: ChatMessage[] | undefined, result: RagAskResult): ChatMessage[] {
  const existing = current ?? [];
  const returnedUser: ChatMessage = {
    ...result.data.user_message,
    edited_flag: false,
    updated_at: result.data.user_message.updated_at ?? result.data.user_message.created_at
  };
  const returnedAssistant: ChatMessage = {
    ...result.data.assistant_message,
    client_message_id: null,
    citations: result.data.citations,
    confidence: result.data.confidence,
    retrieval_summary: result.data.retrieval_summary,
    edited_flag: false,
    replayed: Boolean(result.meta.replayed),
    updated_at: result.data.assistant_message.updated_at ?? result.data.assistant_message.created_at
  };
  const withoutReturned = existing.filter(
    (message) =>
      message.chat_message_id !== returnedUser.chat_message_id &&
      message.chat_message_id !== returnedAssistant.chat_message_id &&
      message.client_message_id !== returnedUser.client_message_id
  );
  return [...withoutReturned, returnedUser, returnedAssistant].sort(compareMessageTimeline);
}

function chatPath(mode: "active" | "temporary", chatSessionId?: number): string {
  const root = mode === "temporary" ? "/chat/temp" : "/chat";
  return chatSessionId ? `${root}/${chatSessionId}` : root;
}

function formatUpdatedAt(value: string): string {
  const updated = new Date(value);
  if (Number.isNaN(updated.getTime())) {
    return "";
  }
  return updated.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function readInitialModel(): string {
  if (typeof window === "undefined") {
    return DEFAULT_MODEL;
  }
  const saved = window.localStorage.getItem(MODEL_STORAGE_KEY);
  return MODEL_OPTIONS.some((option) => option.value === saved) ? saved ?? DEFAULT_MODEL : DEFAULT_MODEL;
}

function ChatSidebar({
  activeSessionId,
  currentRole,
  deletingSessionId,
  isAdmin,
  onDeleteChat,
  onEditChat,
  onToggle,
  onNewChat,
  open,
  sessions
}: {
  activeSessionId: number | null;
  currentRole: string | null;
  deletingSessionId: number | null;
  isAdmin: boolean;
  onDeleteChat: (session: ChatSession) => void;
  onEditChat: (session: ChatSession) => void;
  onToggle: () => void;
  onNewChat: () => void;
  open: boolean;
  sessions: ChatSession[];
}) {
  const [actionMenu, setActionMenu] = useState<{
    left: number;
    session: ChatSession;
    top: number;
  } | null>(null);
  const visibleSessions = sessions.filter((item) => !item.temporary_flag);

  function toggleActionMenu(event: MouseEvent<HTMLButtonElement>, session: ChatSession) {
    const rect = event.currentTarget.getBoundingClientRect();
    const menuWidth = 128;
    const viewportWidth = window.innerWidth || 320;
    setActionMenu((current) =>
      current?.session.chat_session_id === session.chat_session_id
        ? null
        : {
            left: Math.max(12, Math.min(rect.right - menuWidth, viewportWidth - menuWidth - 12)),
            session,
            top: Math.max(92, rect.top - 4)
          }
    );
  }

  return (
    <>
      <aside className={`chat-sidebar ${open ? "open" : "collapsed"}`} aria-label="Chat history">
        <div className="sidebar-header">
          <button
            aria-expanded={open}
            aria-label={open ? "Hide sidebar" : "Show sidebar"}
            className={`sidebar-toggle-button ${open ? "is-open" : ""}`}
            onClick={onToggle}
            type="button"
          >
            <span aria-hidden="true" className="hamburger-line" />
            <span aria-hidden="true" className="hamburger-line" />
            <span aria-hidden="true" className="hamburger-line" />
          </button>
          {open ? (
            <button className="new-chat-button" type="button" onClick={onNewChat}>
              New chat
            </button>
          ) : null}
        </div>
        {open ? (
          <>
            <div className="sidebar-section-label">Chats</div>
            <nav className="chat-thread-list" aria-label="Saved chats">
              {visibleSessions.length === 0 ? <p className="sidebar-empty">No chats yet</p> : null}
              {visibleSessions.map((item) => (
                <div
                  className={`chat-thread-row ${item.chat_session_id === activeSessionId ? "active" : ""}`}
                  key={item.chat_session_id}
                >
                  <Link
                    aria-current={item.chat_session_id === activeSessionId ? "page" : undefined}
                    className="chat-thread-link"
                    to={chatPath(item.temporary_flag ? "temporary" : "active", item.chat_session_id)}
                  >
                    <span className="thread-title">{item.title}</span>
                    <span className="thread-meta">{formatUpdatedAt(item.updated_at)}</span>
                  </Link>
                  <button
                    aria-expanded={actionMenu?.session.chat_session_id === item.chat_session_id}
                    aria-label={`Chat actions for ${item.title}`}
                    className="chat-actions-button"
                    disabled={deletingSessionId === item.chat_session_id}
                    onClick={(event) => toggleActionMenu(event, item)}
                    type="button"
                  >
                    ...
                  </button>
                </div>
              ))}
            </nav>
            <div className="sidebar-actions">
              <Link to="/settings">Settings</Link>
              {isAdmin ? <Link to="/admin/documents">Admin</Link> : null}
              {currentRole ? <span className="sidebar-role">{currentRole}</span> : null}
            </div>
          </>
        ) : null}
      </aside>
      {open && actionMenu ? (
        <div
          className="chat-actions-menu floating"
          role="menu"
          style={{ left: actionMenu.left, top: actionMenu.top }}
        >
          <button
            className="chat-menu-item"
            onClick={() => {
              setActionMenu(null);
              onEditChat(actionMenu.session);
            }}
            role="menuitem"
            type="button"
          >
            編集
          </button>
          <button
            className="chat-menu-item danger"
            onClick={() => {
              setActionMenu(null);
              onDeleteChat(actionMenu.session);
            }}
            role="menuitem"
            type="button"
          >
            削除
          </button>
        </div>
      ) : null}
    </>
  );
}

type DeleteChatDialogState = {
  permanent: boolean;
  session: ChatSession;
};

type EditChatDialogState = {
  session: ChatSession;
  title: string;
};

function EditChatModal({
  dialog,
  onCancel,
  onConfirm,
  onTitleChange,
  saving
}: {
  dialog: EditChatDialogState | null;
  onCancel: () => void;
  onConfirm: () => void;
  onTitleChange: (value: string) => void;
  saving: boolean;
}) {
  if (!dialog) {
    return null;
  }

  return (
    <div className="modal-backdrop">
      <form
        aria-labelledby="edit-chat-title"
        aria-modal="true"
        className="edit-chat-modal"
        onSubmit={(event) => {
          event.preventDefault();
          onConfirm();
        }}
        role="dialog"
      >
        <div className="delete-chat-modal-header">
          <h2 id="edit-chat-title">Edit chat</h2>
          <button aria-label="Close edit dialog" disabled={saving} onClick={onCancel} type="button">
            ×
          </button>
        </div>
        <label className="edit-chat-field">
          <span>Title</span>
          <input
            autoFocus
            disabled={saving}
            maxLength={255}
            onChange={(event) => onTitleChange(event.target.value)}
            value={dialog.title}
          />
        </label>
        <div className="delete-chat-actions">
          <button className="secondary-action" disabled={saving} onClick={onCancel} type="button">
            Cancel
          </button>
          <button className="primary-action" disabled={saving || !dialog.title.trim()} type="submit">
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}

function DeleteChatModal({
  deleting,
  dialog,
  onCancel,
  onConfirm,
  onPermanentChange
}: {
  deleting: boolean;
  dialog: DeleteChatDialogState | null;
  onCancel: () => void;
  onConfirm: () => void;
  onPermanentChange: (value: boolean) => void;
}) {
  if (!dialog) {
    return null;
  }

  return (
    <div className="modal-backdrop">
      <section aria-labelledby="delete-chat-title" aria-modal="true" className="delete-chat-modal" role="dialog">
        <div className="delete-chat-modal-header">
          <h2 id="delete-chat-title">Delete chat</h2>
          <button aria-label="Close delete dialog" disabled={deleting} onClick={onCancel} type="button">
            ×
          </button>
        </div>
        <p className="delete-chat-target">{dialog.session.title}</p>
        <label className="delete-chat-checkbox">
          <input
            checked={dialog.permanent}
            disabled={deleting}
            onChange={(event) => onPermanentChange(event.target.checked)}
            type="checkbox"
          />
          <span>完全に削除しますか？</span>
        </label>
        <p className="delete-chat-help">
          {dialog.permanent
            ? "このチャット、メッセージ、関連する検索結果と引用をPostgresから削除します。"
            : "チェックを外すと、チャット一覧から非表示にします。Postgresの履歴は残ります。"}
        </p>
        <div className="delete-chat-actions">
          <button className="secondary-action" disabled={deleting} onClick={onCancel} type="button">
            Cancel
          </button>
          <button className="danger-action" disabled={deleting} onClick={onConfirm} type="button">
            {deleting ? "Deleting..." : "Delete"}
          </button>
        </div>
      </section>
    </div>
  );
}

export function ChatPage({ mode }: { mode: "active" | "temporary" }) {
  const params = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const routeSessionParam = mode === "temporary" ? params.temporaryChatId : params.chatSessionId;
  const hasRouteSessionParam = routeSessionParam !== undefined;
  const routeSessionId = parseId(routeSessionParam);
  const currentUser = useCurrentUser();
  const chatHistory = useChatHistory();
  const sessionQuery = useChatSession(routeSessionId);
  const messagesQuery = useChatMessages(routeSessionId);
  const createChat = useCreateChatSession();
  const createTemporaryChat = useCreateTemporaryChat();
  const archiveSession = useArchiveSession();
  const hardDeleteSession = useDeleteSession();
  const updateChatTitle = useUpdateChatSessionTitle();
  const askMutation = useAskRagMutation();
  const [question, setQuestion] = useState("");
  const [localMessages, setLocalMessages] = useState<UiMessage[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState(readInitialModel);
  const [selectedStrategy, setSelectedStrategy] = useState<RagStrategy>("llm_tool_orchestrator");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [deletingSessionId, setDeletingSessionId] = useState<number | null>(null);
  const [editDialog, setEditDialog] = useState<EditChatDialogState | null>(null);
  const [deleteDialog, setDeleteDialog] = useState<DeleteChatDialogState | null>(null);

  const session = sessionQuery.data;
  const activeSessionId = session?.chat_session_id ?? routeSessionId;
  const displayMode: ChatMode = session?.mode ?? (mode === "temporary" ? "temporary" : "active");
  const disabledReason = readonlyReason(displayMode);
  const routeSessionUnavailableReason =
    hasRouteSessionParam && routeSessionId === null
      ? "The chat id is invalid."
      : routeSessionId !== null && !session
        ? sessionQuery.isError
          ? safeErrorMessage(sessionQuery.error)
          : "Loading this chat..."
        : null;
  const inputDisabledReason = disabledReason ?? routeSessionUnavailableReason;
  const visibleLocalMessages = useMemo(
    () => localMessages.filter((message) => activeSessionId !== null && message.chat_session_id === activeSessionId),
    [activeSessionId, localMessages]
  );
  const messages = useMemo(
    () => mergeMessages(messagesQuery.data ?? [], visibleLocalMessages),
    [messagesQuery.data, visibleLocalMessages]
  );
  const isSending = askMutation.isPending || createChat.isPending || createTemporaryChat.isPending;
  const isAdmin = currentUser.data?.role === "admin";
  const currentRole = currentUser.data ? `${currentUser.data.display_name} / ${currentUser.data.role}` : null;
  const canStartTemporaryChat =
    mode === "active" &&
    !hasRouteSessionParam &&
    !session &&
    localMessages.length === 0 &&
    messages.length === 0 &&
    !isSending;

  async function ensureSession(message: string): Promise<ChatSession> {
    if (session) {
      return session;
    }
    if (hasRouteSessionParam) {
      throw new ApiError({
        code:
          routeSessionId === null ? "invalid_chat_session" : sessionQuery.isError ? "session_unavailable" : "session_loading",
        message: "Chat session is not ready.",
        requestId: null,
        status: routeSessionId === null ? 400 : sessionQuery.isError ? 404 : 409
      });
    }
    const creator = mode === "temporary" ? createTemporaryChat : createChat;
    const created = await creator.mutateAsync({
      title: titleFromMessage(message),
      temporary_flag: mode === "temporary"
    });
    navigate(chatPath(mode, created.chat_session_id), { replace: true });
    return created;
  }

  async function submitQuestion() {
    const message = question.trim();
    if (!message || inputDisabledReason || isSending) {
      return;
    }

    setNotice(null);
    setQuestion("");

    let targetSessionId: number | null = session?.chat_session_id ?? routeSessionId;
    let clientMessageId = "";
    try {
      const targetSession = await ensureSession(message);
      targetSessionId = targetSession.chat_session_id;
      clientMessageId = generateClientMessageId();
      const now = new Date().toISOString();
      const optimisticUser: UiMessage = {
        chat_message_id: `optimistic-${clientMessageId}`,
        chat_session_id: targetSession.chat_session_id,
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
        chat_session_id: targetSession.chat_session_id,
        role: "assistant",
        content: "",
        client_message_id: null,
        created_at: now,
        updated_at: now,
        edited_flag: false,
        status: "loading"
      };

      setLocalMessages((current) => [...current, optimisticUser, loadingAssistant]);

      const result = await askMutation.mutateAsync({
        chat_session_id: targetSession.chat_session_id,
        client_message_id: clientMessageId,
        message,
        model_key: selectedModel,
        top_k: DEFAULT_TOP_K,
        rerank_top_n: DEFAULT_RERANK_TOP_N,
        strategy: selectedStrategy
      });
      queryClient.setQueryData(queryKeys.chatMessages(targetSession.chat_session_id), (current: ChatMessage[] | undefined) =>
        mergePersistedAskMessages(current, result)
      );
      setLocalMessages((current) =>
        current.filter(
          (item) =>
            item.chat_message_id !== `optimistic-${clientMessageId}` && item.chat_message_id !== `loading-${clientMessageId}`
        )
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        navigate("/login", { replace: true });
      }
      if (clientMessageId && isRequestInProgress(error)) {
        setLocalMessages((current) =>
          current.filter(
            (item) =>
              item.chat_message_id !== `optimistic-${clientMessageId}` && item.chat_message_id !== `loading-${clientMessageId}`
          )
        );
      } else if (clientMessageId && isNoContext(error)) {
        setLocalMessages((current) => current.filter((item) => item.chat_message_id !== `loading-${clientMessageId}`));
        if (targetSessionId !== null) {
          queryClient.invalidateQueries({ queryKey: queryKeys.chatMessages(targetSessionId) });
        }
      } else if (clientMessageId) {
        setLocalMessages((current) => current.filter((item) => item.chat_message_id !== `loading-${clientMessageId}`));
      }
      setNotice(safeErrorMessage(error));
      if (!clientMessageId) {
        setQuestion(message);
      }
    }
  }

  function startNewChat() {
    setNotice(null);
    setQuestion("");
    navigate(chatPath(mode));
  }

  function startTemporaryChat() {
    setNotice(null);
    setQuestion("");
    navigate(chatPath("temporary"));
  }

  function requestEditChat(target: ChatSession) {
    if (updateChatTitle.isPending) {
      return;
    }
    setNotice(null);
    setEditDialog({ session: target, title: target.title });
  }

  async function confirmEditChat() {
    if (!editDialog || updateChatTitle.isPending) {
      return;
    }
    const title = editDialog.title.trim();
    if (!title) {
      return;
    }
    try {
      await updateChatTitle.mutateAsync({
        chatSessionId: editDialog.session.chat_session_id,
        title
      });
      setNotice(null);
      setEditDialog(null);
    } catch (error) {
      setNotice(safeErrorMessage(error));
    }
  }

  function requestDeleteChat(target: ChatSession) {
    if (archiveSession.isPending || hardDeleteSession.isPending || target.temporary_flag) {
      return;
    }
    setNotice(null);
    setDeleteDialog({ session: target, permanent: true });
  }

  async function confirmDeleteChat() {
    if (!deleteDialog || archiveSession.isPending || hardDeleteSession.isPending) {
      return;
    }
    const target = deleteDialog.session;
    setDeletingSessionId(target.chat_session_id);
    try {
      if (deleteDialog.permanent) {
        await hardDeleteSession.mutateAsync(target.chat_session_id);
      } else {
        await archiveSession.mutateAsync(target.chat_session_id);
      }
      setNotice(null);
      setDeleteDialog(null);
      if (target.chat_session_id === activeSessionId) {
        setLocalMessages((current) => current.filter((message) => message.chat_session_id !== target.chat_session_id));
        navigate(chatPath(mode));
      }
    } catch (error) {
      setNotice(safeErrorMessage(error));
    } finally {
      setDeletingSessionId(null);
    }
  }

  function changeModel(value: string) {
    setSelectedModel(value);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(MODEL_STORAGE_KEY, value);
    }
  }

  if (currentUser.error instanceof ApiError && currentUser.error.status === 401) {
    return <Navigate to="/login" replace />;
  }

  return (
    <main className={`chatgpt-layout ${sidebarOpen ? "" : "sidebar-collapsed"}`}>
      <ChatSidebar
        activeSessionId={activeSessionId}
        currentRole={currentRole}
        deletingSessionId={deletingSessionId}
        isAdmin={isAdmin}
        onDeleteChat={requestDeleteChat}
        onEditChat={requestEditChat}
        onNewChat={startNewChat}
        onToggle={() => setSidebarOpen((current) => !current)}
        open={sidebarOpen}
        sessions={chatHistory.data ?? []}
      />
      <EditChatModal
        dialog={editDialog}
        onCancel={() => {
          if (!updateChatTitle.isPending) {
            setEditDialog(null);
          }
        }}
        onConfirm={confirmEditChat}
        onTitleChange={(title) => setEditDialog((current) => (current ? { ...current, title } : current))}
        saving={updateChatTitle.isPending}
      />
      <DeleteChatModal
        deleting={deletingSessionId !== null}
        dialog={deleteDialog}
        onCancel={() => {
          if (deletingSessionId === null) {
            setDeleteDialog(null);
          }
        }}
        onConfirm={confirmDeleteChat}
        onPermanentChange={(permanent) =>
          setDeleteDialog((current) => (current ? { ...current, permanent } : current))
        }
      />
      <section className="chat-surface" aria-label="Chat">
        <header className="chat-topbar">
          <div className="chat-title-row">
            <div>
              <h1>{session?.title ?? "New chat"}</h1>
              <p>
                {currentRole ??
                  (currentUser.isLoading ? "Checking session" : currentUser.isError ? "Session check failed" : "Local RAG")}
              </p>
            </div>
          </div>
          <div className="chat-topbar-actions">
            {canStartTemporaryChat ? (
              <button className="temporary-chat-button" onClick={startTemporaryChat} type="button">
                Temporary chat
              </button>
            ) : null}
            {displayMode !== "active" ? <span className="mode-badge">{displayMode}</span> : null}
          </div>
        </header>
        <ChatModeBanner mode={displayMode} />
        {sessionQuery.isError ? <p className="error">{safeErrorMessage(sessionQuery.error)}</p> : null}
        {notice ? (
          <p className="error" role="alert">
            {notice}
          </p>
        ) : null}
        {messagesQuery.isLoading ? <p className="notice">Loading messages...</p> : null}
        <div className={`chat-conversation ${messages.length === 0 ? "empty" : ""}`}>
          {messages.length === 0 ? (
            <section className="chat-welcome" aria-label="New chat prompt">
              <h2>How can I help?</h2>
              <p>Start a new question or pick a saved chat from the sidebar. Each chat keeps its own history in Postgres.</p>
            </section>
          ) : (
            <MessageList messages={messages} />
          )}
        </div>
        {chatHistory.isError ? <p className="notice">Chat history could not be loaded.</p> : null}
        <div className="composer-shell">
          <MessageInput
            disabled={Boolean(inputDisabledReason) || currentUser.isLoading || currentUser.isError}
            disabledReason={inputDisabledReason}
            isSending={isSending}
            modelOptions={MODEL_OPTIONS}
            onChange={setQuestion}
            onModelChange={changeModel}
            onStrategyChange={setSelectedStrategy}
            onSubmit={submitQuestion}
            selectedModel={selectedModel}
            selectedStrategy={selectedStrategy}
            strategyOptions={RAG_STRATEGY_OPTIONS}
            value={question}
          />
        </div>
      </section>
    </main>
  );
}
