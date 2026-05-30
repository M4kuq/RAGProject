import { UiMessage } from "../../features/chat/chatState";
import { RagAskRetrievalSummary } from "../../features/chat/chatTypes";
import { CitationPanel } from "./CitationPanel";
import { ConfidenceBadge } from "./ConfidenceBadge";

const STRATEGY_LABELS: Record<string, string> = {
  agentic_router: "Agentic Router",
  dense: "Normal RAG",
  hybrid: "Hybrid RAG",
  llm_tool_orchestrator: "Auto",
  sparse: "Sparse RAG"
};

const TOOL_LABELS: Record<string, string> = {
  dense_search: "dense",
  hybrid_search: "hybrid",
  inspect_retrieval_trace: "trace",
  sparse_search: "sparse"
};

function RetrievalModeBadge({ summary }: { summary?: RagAskRetrievalSummary | null }) {
  if (!summary) {
    return null;
  }
  const base = STRATEGY_LABELS[summary.strategy_type] ?? summary.strategy_type;
  const tools = summary.tools_used
    .filter((tool) => tool !== "finalize_answer")
    .map((tool) => TOOL_LABELS[tool] ?? tool)
    .filter(Boolean);
  const detail = tools.length ? `${base}: ${Array.from(new Set(tools)).join(" + ")}` : base;
  return (
    <span className="retrieval-mode-badge" title={`retrieval_run_id=${summary.retrieval_run_id}`}>
      {detail}
    </span>
  );
}

export function MessageList({ messages }: { messages: UiMessage[] }) {
  if (messages.length === 0) {
    return <section className="messages empty">No messages yet.</section>;
  }
  return (
    <section className="messages" aria-label="messages">
      {messages.map((message) => {
        const key = `${message.role}-${message.chat_message_id}`;
        if (message.role === "assistant") {
          return (
            <article key={key} className={`message assistant ${message.status ?? ""}`} aria-busy={message.status === "loading"}>
              {message.status === "loading" ? (
                <p>Generating answer...</p>
              ) : (
                <>
                  <div className="message-header">
                    <strong>Assistant</strong>
                    <RetrievalModeBadge summary={message.retrieval_summary} />
                    <ConfidenceBadge confidence={message.confidence} />
                    {message.replayed ? <span className="replay-badge">replayed</span> : null}
                  </div>
                  <p>{message.content}</p>
                  <CitationPanel citations={message.citations} />
                </>
              )}
            </article>
          );
        }
        return (
          <article key={key} className={`message user ${message.status ?? ""}`}>
            <div className="message-header">
              <strong>You</strong>
              {message.status === "optimistic" ? <span className="pending-badge">sending</span> : null}
            </div>
            <p>{message.content}</p>
          </article>
        );
      })}
    </section>
  );
}
