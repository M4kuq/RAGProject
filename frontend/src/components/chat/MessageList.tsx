import { UiMessage } from "../../features/chat/chatState";
import { CitationPanel } from "./CitationPanel";
import { ConfidenceBadge } from "./ConfidenceBadge";

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
