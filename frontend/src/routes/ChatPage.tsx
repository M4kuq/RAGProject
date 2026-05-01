import { FormEvent, useState } from "react";
import { apiFetch } from "../lib/apiClient";

export function ChatPage({ mode }: { mode: "active" | "temporary" }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string>("");

  async function ask(event: FormEvent) {
    event.preventDefault();
    const result = await apiFetch<{ data: { answer: string } }>("/api/v1/rag/ask", {
      method: "POST",
      body: JSON.stringify({ question, client_message_id: crypto.randomUUID() })
    });
    setAnswer(result.data.answer);
  }

  return (
    <main className="workspace">
      <header>
        <h1>{mode === "temporary" ? "Temporary Chat" : "Chat"}</h1>
      </header>
      <section className="messages">{answer ? <article className="message assistant">{answer}</article> : null}</section>
      <form onSubmit={ask} className="composer">
        <textarea value={question} onChange={(event) => setQuestion(event.target.value)} />
        <button type="submit">Send</button>
      </form>
    </main>
  );
}
