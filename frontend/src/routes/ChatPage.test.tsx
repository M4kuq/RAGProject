import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { ChatPage } from "./ChatPage";

function renderChat(initialPath = "/chat", mode: "active" | "temporary" = "active") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  const routePath = mode === "temporary" ? "/chat/temp/:temporaryChatId?" : "/chat/:chatSessionId?";
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/login" element={<main>Login</main>} />
          {mode === "active" ? <Route path="/chat/temp/:temporaryChatId?" element={<main>Temporary route</main>} /> : null}
          <Route path={routePath} element={children} />
          {mode === "temporary" ? <Route path="/chat/:chatSessionId?" element={<main>Active route</main>} /> : null}
          <Route path="/settings" element={<main>Settings</main>} />
          <Route path="/admin/documents" element={<main>Admin</main>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return render(<ChatPage mode={mode} />, { wrapper: Wrapper });
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function meResponse(role: "viewer" | "admin" = "viewer") {
  return {
    data: {
      user_id: 1,
      email: `${role}@example.com`,
      display_name: role === "admin" ? "Admin" : "Viewer",
      role,
      status: "active"
    }
  };
}

function sessionData({
  id = 10,
  mode = "active",
  title = "Demo chat"
}: {
  id?: number;
  mode?: "active" | "archived" | "temporary" | "temporary_expired";
  title?: string;
} = {}) {
  return {
    chat_session_id: id,
    title,
    status: mode === "archived" ? "archived" : "active",
    display_status: mode,
    mode,
    temporary_flag: mode === "temporary" || mode === "temporary_expired",
    ttl_expires_at: mode === "temporary" ? "2099-01-01T00:00:00Z" : null,
    created_at: "2026-05-18T00:00:00Z",
    updated_at: "2026-05-18T00:00:00Z",
    tags: []
  };
}

function sessionResponse(args: Parameters<typeof sessionData>[0] = {}) {
  return { data: sessionData(args) };
}

function historyResponse(sessions = [sessionData()]) {
  return { data: sessions, meta: { pagination: { page: 1, page_size: 50, total: sessions.length, has_next: false } } };
}

function emptyMessages() {
  return { data: [], meta: { pagination: { page: 1, page_size: 100, total: 0, has_next: false } } };
}

function persistedMessagesWithCitation() {
  return {
    data: [
      {
        chat_message_id: 100,
        chat_session_id: 10,
        role: "user",
        content: "What is Phase1?",
        client_message_id: "msg-history",
        edited_flag: false,
        citations: [],
        confidence: null,
        created_at: "2026-05-18T00:00:01Z",
        updated_at: "2026-05-18T00:00:01Z"
      },
      {
        chat_message_id: 101,
        chat_session_id: 10,
        role: "assistant",
        content: "Phase1 uses local RAG components [1].",
        client_message_id: null,
        edited_flag: false,
        citations: [
          {
            citation_id: 201,
            local_citation_id: 1,
            document_chunk_id: 301,
            source_label: "phase1-seed.md",
            snippet: "Phase1 validates a local Docker Compose RAG stack.",
            page_from: null,
            page_to: null,
            section_title: "Architecture",
            old_version_flag: false
          }
        ],
        confidence: { answer_confidence: 0.82, groundedness_score: 0.9, confidence_label: "High" },
        created_at: "2026-05-18T00:00:02Z",
        updated_at: "2026-05-18T00:00:02Z"
      }
    ],
    meta: { pagination: { page: 1, page_size: 100, total: 2, has_next: false } }
  };
}

function askSuccess(replayed = false) {
  return {
    data: {
      chat_session_id: 10,
      user_message: {
        chat_message_id: 100,
        chat_session_id: 10,
        role: "user",
        content: "What is RAG?",
        client_message_id: "msg_fixed",
        created_at: "2026-05-18T00:00:01Z"
      },
      assistant_message: {
        chat_message_id: 101,
        chat_session_id: 10,
        role: "assistant",
        content: "RAG answers with grounded citations [1].",
        linked_retrieval_run_id: 999,
        created_at: "2026-05-18T00:00:02Z"
      },
      citations: [
        {
          citation_id: 201,
          local_citation_id: 1,
          document_chunk_id: 301,
          source_label: "handbook.pdf",
          snippet: "Grounded citation preview",
          page_from: 3,
          page_to: 4,
          section_title: "Intro",
          old_version_flag: true
        }
      ],
      confidence: { answer_confidence: 0.82, groundedness_score: 0.9, confidence_label: "High" },
      retrieval_run_id: 999
    },
    meta: { request_id: "req_1", replayed }
  };
}

function askSuccessWithMessages({
  assistantContent,
  assistantId,
  clientMessageId,
  seconds,
  userContent,
  userId
}: {
  assistantContent: string;
  assistantId: number;
  clientMessageId: string;
  seconds: number;
  userContent: string;
  userId: number;
}) {
  const response = askSuccess();
  response.data.user_message.chat_message_id = userId;
  response.data.user_message.content = userContent;
  response.data.user_message.client_message_id = clientMessageId;
  response.data.user_message.created_at = `2026-05-18T00:00:${String(seconds).padStart(2, "0")}Z`;
  response.data.assistant_message.chat_message_id = assistantId;
  response.data.assistant_message.content = assistantContent;
  response.data.assistant_message.created_at = `2026-05-18T00:00:${String(seconds + 1).padStart(2, "0")}Z`;
  return response;
}

afterEach(() => {
  document.cookie = "rag_csrf=; Max-Age=0; path=/";
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

test("shows temporary chat action only on the blank new chat screen", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse([]));
      return jsonResponse({ data: [] });
    })
  );

  renderChat();

  expect(await screen.findByRole("heading", { name: "New chat" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Temporary chat" }));

  expect(await screen.findByText("Temporary route")).toBeInTheDocument();
});

test("keeps saved chats visible while viewing the temporary chat entry", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse([sessionData()]));
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/temp", "temporary");

  expect(await screen.findByRole("heading", { name: "New chat" })).toBeInTheDocument();
  const savedChat = screen.getByRole("link", { name: /Demo chat/ });
  expect(savedChat).toHaveAttribute("href", "/chat/10");

  fireEvent.click(savedChat);
  expect(await screen.findByText("Active route")).toBeInTheDocument();
});

test("renders a ChatGPT-style shell with saved chats in the sidebar", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse("admin"));
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse([sessionData(), sessionData({ id: 11, title: "Model notes" })]));
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/10");

  expect(await screen.findByRole("heading", { name: "Demo chat" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New chat" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Hide sidebar" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Temporary chat" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("model")).toHaveValue("lmstudio:qwen3.5-9b");
  expect(screen.getByRole("link", { name: /Demo chat/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Model notes/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Chat actions for Demo chat" }));
  const actionMenu = screen.getByRole("menu");
  expect(actionMenu).toHaveClass("floating");
  expect(actionMenu.closest(".chat-thread-list")).toBeNull();
  expect(screen.getByRole("menuitem", { name: "編集" })).toHaveClass("chat-menu-item");
  expect(screen.getByRole("menuitem", { name: "削除" })).toHaveClass("danger");
  expect(screen.getByRole("link", { name: "Admin" })).toBeInTheDocument();
  expect(screen.getByText(/Each chat keeps its own history in Postgres/)).toBeInTheDocument();
});

test("can collapse the chat sidebar and keep the composer visible", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      return jsonResponse({ data: [] });
    })
  );

  const rendered = renderChat("/chat/10");
  await screen.findByRole("heading", { name: "Demo chat" });
  fireEvent.click(screen.getByRole("button", { name: "Hide sidebar" }));

  expect(rendered.container.querySelector(".chatgpt-layout.sidebar-collapsed")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New chat" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Show sidebar" })).toBeInTheDocument();
  expect(screen.getByLabelText("message")).toBeInTheDocument();
});

test("renders citation filenames for persisted assistant messages", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(persistedMessagesWithCitation());
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/10");

  expect(await screen.findByText("Phase1 uses local RAG components [1].")).toBeInTheDocument();
  expect(screen.getByText("Confidence High")).toBeInTheDocument();
  expect(screen.getByText(/\[1\] phase1-seed\.md/)).toBeInTheDocument();
  expect(screen.getByText("Architecture")).toBeInTheDocument();
  expect(screen.getByText("Phase1 validates a local Docker Compose RAG stack.")).toBeInTheDocument();
});

test("can delete a saved chat from the sidebar", async () => {
  document.cookie = "rag_csrf=csrf-token";
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse([sessionData()]));
    if (path.endsWith("/api/v1/chat/sessions/10") && method === "GET") return jsonResponse(sessionResponse());
    if (path.endsWith("/api/v1/chat/sessions/10") && method === "DELETE") {
      return jsonResponse({ data: { chat_session_id: 10, result_code: "deleted" } });
    }
    if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat("/chat/10");
  await screen.findByRole("heading", { name: "Demo chat" });
  fireEvent.click(screen.getByRole("button", { name: "Chat actions for Demo chat" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "削除" }));
  const dialog = await screen.findByRole("dialog", { name: "Delete chat" });
  const permanent = within(dialog).getByRole("checkbox", { name: "完全に削除しますか？" });
  expect(permanent).toBeChecked();
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).endsWith("/api/v1/chat/sessions/10") && init?.method === "DELETE"
      )
    ).toBe(true)
  );
  const deleteCall = fetchMock.mock.calls.find(
    ([url, init]) => String(url).endsWith("/api/v1/chat/sessions/10") && init?.method === "DELETE"
  );
  expect(new Headers(deleteCall?.[1]?.headers).get("x-csrf-token")).toBe("csrf-token");
  expect(await screen.findByRole("heading", { name: "New chat" })).toBeInTheDocument();
});

test("can edit a saved chat from the action menu", async () => {
  document.cookie = "rag_csrf=csrf-token";
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse([sessionData()]));
    if (path.endsWith("/api/v1/chat/sessions/10") && method === "GET") return jsonResponse(sessionResponse());
    if (path.endsWith("/api/v1/chat/sessions/10") && method === "PATCH") {
      return jsonResponse(sessionResponse({ title: "Edited chat" }));
    }
    if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat("/chat/10");
  await screen.findByRole("heading", { name: "Demo chat" });
  fireEvent.click(screen.getByRole("button", { name: "Chat actions for Demo chat" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "編集" }));
  const dialog = await screen.findByRole("dialog", { name: "Edit chat" });
  fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Edited chat" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).endsWith("/api/v1/chat/sessions/10") && init?.method === "PATCH"
      )
    ).toBe(true)
  );
  const updateCall = fetchMock.mock.calls.find(
    ([url, init]) => String(url).endsWith("/api/v1/chat/sessions/10") && init?.method === "PATCH"
  );
  expect(new Headers(updateCall?.[1]?.headers).get("x-csrf-token")).toBe("csrf-token");
  expect(updateCall?.[1]?.body).toBe(JSON.stringify({ title: "Edited chat" }));
});

test("can archive instead of hard deleting from the delete dialog", async () => {
  document.cookie = "rag_csrf=csrf-token";
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse([sessionData()]));
    if (path.endsWith("/api/v1/chat/sessions/10") && method === "GET") return jsonResponse(sessionResponse());
    if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
    if (path.endsWith("/api/v1/chat/sessions/10/archive") && method === "POST") {
      return jsonResponse({ data: { chat_session_id: 10, status: "archived", result_code: "archived" } });
    }
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat("/chat/10");
  await screen.findByRole("heading", { name: "Demo chat" });
  fireEvent.click(screen.getByRole("button", { name: "Chat actions for Demo chat" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "削除" }));
  const dialog = await screen.findByRole("dialog", { name: "Delete chat" });
  fireEvent.click(within(dialog).getByRole("checkbox", { name: "完全に削除しますか？" }));
  fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).endsWith("/api/v1/chat/sessions/10/archive") && init?.method === "POST"
      )
    ).toBe(true)
  );
  expect(
    fetchMock.mock.calls.some(
      ([url, init]) => String(url).endsWith("/api/v1/chat/sessions/10") && init?.method === "DELETE"
    )
  ).toBe(false);
});

test("creates a persisted chat before the first rag ask and keeps csrf", async () => {
  document.cookie = "rag_csrf=csrf-token";
  vi.stubGlobal("crypto", { randomUUID: () => "fixed" });
  let resolveAsk: (response: Response) => void = () => undefined;
  const askPromise = new Promise<Response>((resolve) => {
    resolveAsk = resolve;
  });
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse([]));
    if (path.endsWith("/api/v1/chat/sessions") && method === "POST") return jsonResponse(sessionResponse(), 201);
    if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
    if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
    if (path.includes("/api/v1/rag/ask")) return askPromise;
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat();
  await waitFor(() => expect(screen.getByLabelText("message")).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText("model"), { target: { value: "openai:gpt-5.5" } });
  expect(screen.getByLabelText("model")).toHaveValue("openai:gpt-5.5");
  fireEvent.change(screen.getByLabelText("message"), { target: { value: "What is RAG?" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  const createCall = await waitFor(() => {
    const call = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/api/v1/chat/sessions") && init?.method === "POST");
    expect(call).toBeTruthy();
    return call as [string, RequestInit];
  });
  expect(JSON.parse(String(createCall[1].body))).toMatchObject({
    title: "What is RAG?",
    temporary_flag: false
  });
  expect(screen.queryByRole("button", { name: "Temporary chat" })).not.toBeInTheDocument();
  expect(await screen.findByText("What is RAG?")).toBeInTheDocument();
  expect(screen.getByText("Generating answer...")).toBeInTheDocument();

  const askCall = await waitFor(() => {
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/v1/rag/ask"));
    expect(call).toBeTruthy();
    return call as [string, RequestInit];
  });
  expect(JSON.parse(String(askCall[1].body))).toMatchObject({
    chat_session_id: 10,
    client_message_id: "msg_fixed",
    message: "What is RAG?",
    top_k: 20,
    rerank_top_n: 5
  });
  expect(new Headers(askCall[1].headers).get("x-csrf-token")).toBe("csrf-token");

  resolveAsk(new Response(JSON.stringify(askSuccess()), { status: 200 }));

  expect(await screen.findByText("RAG answers with grounded citations [1].")).toBeInTheDocument();
  expect(screen.getByText("Confidence High")).toBeInTheDocument();
  expect(screen.getByText("old version")).toBeInTheDocument();
  expect(screen.getByText(/handbook\.pdf/)).toBeInTheDocument();
  expect(screen.getByText("Grounded citation preview")).toBeInTheDocument();
  expect(screen.queryByText("999")).not.toBeInTheDocument();
});

test("keeps multiple completed ask pairs interleaved by timeline", async () => {
  document.cookie = "rag_csrf=csrf-token";
  const randomUUID = vi.fn().mockReturnValueOnce("one").mockReturnValueOnce("two");
  vi.stubGlobal("crypto", { randomUUID });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      if (path.includes("/api/v1/rag/ask")) {
        const body = JSON.parse(String(init?.body));
        if (body.message === "Question one") {
          return jsonResponse(
            askSuccessWithMessages({
              assistantContent: "Answer one [1].",
              assistantId: 101,
              clientMessageId: body.client_message_id,
              seconds: 1,
              userContent: "Question one",
              userId: 100
            })
          );
        }
        return jsonResponse(
          askSuccessWithMessages({
            assistantContent: "Answer two [1].",
            assistantId: 103,
            clientMessageId: body.client_message_id,
            seconds: 3,
            userContent: "Question two",
            userId: 102
          })
        );
      }
      return jsonResponse({ data: [] });
    })
  );

  const rendered = renderChat("/chat/10");
  await waitFor(() => expect(screen.getByLabelText("message")).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText("message"), { target: { value: "Question one" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("Answer one [1].")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("message"), { target: { value: "Question two" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText("Answer two [1].")).toBeInTheDocument();

  const rows = Array.from(rendered.container.querySelectorAll(".message")).map((element) => element.textContent ?? "");
  expect(rows).toHaveLength(4);
  expect(rows[0]).toContain("Question one");
  expect(rows[1]).toContain("Answer one [1].");
  expect(rows[2]).toContain("Question two");
  expect(rows[3]).toContain("Answer two [1].");
});

test("renders replayed ask as a normal assistant answer with replay badge", async () => {
  vi.stubGlobal("crypto", { randomUUID: () => "fixed" });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      if (path.includes("/api/v1/rag/ask")) return jsonResponse(askSuccess(true));
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/10");
  await waitFor(() => expect(screen.getByLabelText("message")).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText("message"), { target: { value: "What is RAG?" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("RAG answers with grounded citations [1].")).toBeInTheDocument();
  expect(screen.getByText("replayed")).toBeInTheDocument();
});

test("renders fallback confidence and sparse citation fields without crashing", async () => {
  vi.stubGlobal("crypto", { randomUUID: () => "fallbacks" });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      if (path.includes("/api/v1/rag/ask")) {
        const response = askSuccess();
        response.data.confidence = {
          answer_confidence: Number.NaN,
          groundedness_score: Number.NaN,
          confidence_label: "Unexpected"
        };
        response.data.citations[0].source_label = "";
        response.data.citations[0].snippet = "";
        return jsonResponse(response);
      }
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/10");
  await waitFor(() => expect(screen.getByLabelText("message")).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText("message"), { target: { value: "Fallbacks" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("Confidence Unknown")).toBeInTheDocument();
  expect(screen.getByText(/\[1\] source/)).toBeInTheDocument();
});

test("no_context keeps the user message, removes loading, and shows safe error", async () => {
  vi.stubGlobal("crypto", { randomUUID: () => "no-context" });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      if (path.includes("/api/v1/rag/ask")) {
        return jsonResponse({ error: { code: "no_context_found", message: "No context found." }, meta: {} }, 422);
      }
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/10");
  await waitFor(() => expect(screen.getByLabelText("message")).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText("message"), { target: { value: "Unknown topic" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("No usable context was found for this question.")).toBeInTheDocument();
  expect(screen.getByText("Unknown topic")).toBeInTheDocument();
  expect(screen.queryByText("Generating answer...")).not.toBeInTheDocument();
});

test("request_in_progress removes optimistic rows and shows processing notice", async () => {
  vi.stubGlobal("crypto", { randomUUID: () => "running" });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      if (path.includes("/api/v1/rag/ask")) {
        return jsonResponse(
          { error: { code: "request_in_progress", message: "Request is already in progress." }, meta: {} },
          409
        );
      }
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/10");
  await waitFor(() => expect(screen.getByLabelText("message")).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText("message"), { target: { value: "Still running" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText(/already being processed/)).toBeInTheDocument();
  expect(screen.queryByText("Still running")).not.toBeInTheDocument();
  expect(screen.queryByText("Generating answer...")).not.toBeInTheDocument();
});

test("existing route session disables submission until session detail loads", async () => {
  let resolveSession: (response: Response) => void = () => undefined;
  const sessionPromise = new Promise<Response>((resolve) => {
    resolveSession = resolve;
  });
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
    if (path.endsWith("/api/v1/chat/sessions/10/messages?page=1&page_size=100")) return jsonResponse(emptyMessages());
    if (path.endsWith("/api/v1/chat/sessions/10")) return sessionPromise;
    if (path.endsWith("/api/v1/chat/sessions") && init?.method === "POST") return jsonResponse(sessionResponse(), 201);
    if (path.includes("/api/v1/rag/ask")) return jsonResponse(askSuccess());
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat("/chat/10");

  expect(await screen.findByLabelText("message")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/api/v1/chat/sessions") && init?.method === "POST")).toBe(
    false
  );
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/v1/rag/ask"))).toBe(false);

  resolveSession(new Response(JSON.stringify(sessionResponse()), { status: 200 }));
  await waitFor(() => expect(screen.getByLabelText("message")).not.toBeDisabled());
});

test("existing route session error does not create a replacement chat", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
    if (path.endsWith("/api/v1/chat/sessions/10/messages?page=1&page_size=100")) return jsonResponse(emptyMessages());
    if (path.endsWith("/api/v1/chat/sessions/10")) {
      return jsonResponse({ error: { code: "resource_not_found", message: "not found" }, meta: {} }, 404);
    }
    if (path.endsWith("/api/v1/chat/sessions") && init?.method === "POST") return jsonResponse(sessionResponse(), 201);
    if (path.includes("/api/v1/rag/ask")) return jsonResponse(askSuccess());
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat("/chat/10");
  expect((await screen.findAllByText("This chat could not be found.")).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/api/v1/chat/sessions") && init?.method === "POST")).toBe(
    false
  );
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/v1/rag/ask"))).toBe(false);
});

test("invalid route session id disables submission without creating a chat", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse([]));
    if (path.endsWith("/api/v1/chat/sessions") && init?.method === "POST") return jsonResponse(sessionResponse(), 201);
    if (path.includes("/api/v1/rag/ask")) return jsonResponse(askSuccess());
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat("/chat/not-a-number");

  expect(await screen.findByText("The chat id is invalid.")).toBeInTheDocument();
  expect(screen.getByLabelText("message")).toBeDisabled();
  expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/api/v1/chat/sessions") && init?.method === "POST")).toBe(
    false
  );
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/v1/rag/ask"))).toBe(false);
});

test("archived and temporary expired sessions disable the composer", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
      if (path.endsWith("/api/v1/chat/sessions/10/messages?page=1&page_size=100")) return jsonResponse(emptyMessages());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse({ mode: "archived" }));
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/10");

  expect((await screen.findAllByText("This chat is archived and can only be read.")).length).toBeGreaterThan(0);
  expect(screen.getByLabelText("message")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

  vi.unstubAllGlobals();
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse([sessionData({ mode: "temporary_expired" })]));
      if (path.endsWith("/api/v1/chat/sessions/10/messages?page=1&page_size=100")) return jsonResponse(emptyMessages());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse({ mode: "temporary_expired" }));
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/temp/10", "temporary");

  expect(await screen.findByText("This temporary chat has expired and can only be read.")).toBeInTheDocument();
  expect(screen.getAllByLabelText("message")[1]).toBeDisabled();
});

test("reload after failed ask renders only the persisted user message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.includes("/api/v1/chat/sessions?")) return jsonResponse(historyResponse());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.endsWith("/api/v1/chat/sessions/10/messages?page=1&page_size=100")) {
        return jsonResponse({
          data: [
            {
              chat_message_id: 55,
              chat_session_id: 10,
              role: "user",
              content: "Failed question",
              client_message_id: "failed-msg",
              edited_flag: false,
              created_at: "2026-05-18T00:00:00Z",
              updated_at: "2026-05-18T00:00:00Z"
            }
          ],
          meta: { pagination: { page: 1, page_size: 100, total: 1, has_next: false } }
        });
      }
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/10");

  expect(await screen.findByText("Failed question")).toBeInTheDocument();
  expect(screen.queryByText("Assistant")).not.toBeInTheDocument();
  expect(screen.queryByText("Generating answer...")).not.toBeInTheDocument();
});
