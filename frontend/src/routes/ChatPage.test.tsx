import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
          <Route path={routePath} element={children} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return render(<ChatPage mode={mode} />, { wrapper: Wrapper });
}

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function meResponse() {
  return { data: { user_id: 1, email: "viewer@example.com", display_name: "Viewer", role: "viewer", status: "active" } };
}

function sessionResponse(mode: "active" | "archived" | "temporary" | "temporary_expired" = "active") {
  return {
    data: {
      chat_session_id: 10,
      title: "Demo chat",
      status: mode === "archived" ? "archived" : "active",
      display_status: mode,
      mode,
      temporary_flag: mode === "temporary" || mode === "temporary_expired",
      ttl_expires_at: mode === "temporary" ? "2099-01-01T00:00:00Z" : null,
      created_at: "2026-05-18T00:00:00Z",
      updated_at: "2026-05-18T00:00:00Z",
      tags: []
    }
  };
}

function emptyMessages() {
  return { data: [], meta: { pagination: { page: 1, page_size: 100, total: 0, has_next: false } } };
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

afterEach(() => {
  vi.unstubAllGlobals();
});

test("sends rag ask with optimistic user, loading row, citations, confidence and csrf", async () => {
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
    if (path.endsWith("/api/v1/chat/sessions") && method === "POST") return jsonResponse(sessionResponse(), 201);
    if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
    if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
    if (path.includes("/api/v1/rag/ask")) return askPromise;
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat();
  await screen.findByText("Viewer / viewer");
  fireEvent.change(await screen.findByLabelText("message"), { target: { value: "What is RAG?" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("What is RAG?")).toBeInTheDocument();
  expect(screen.getByText("回答を生成しています...")).toBeInTheDocument();
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
  expect(screen.getByText("信頼度 高")).toBeInTheDocument();
  expect(screen.getByText("旧版")).toBeInTheDocument();
  expect(screen.getByText(/handbook\.pdf/)).toBeInTheDocument();
  expect(screen.getByText("Grounded citation preview")).toBeInTheDocument();
  expect(screen.queryByText("999")).not.toBeInTheDocument();
});

test("renders replayed ask as a normal assistant answer with replay badge", async () => {
  vi.stubGlobal("crypto", { randomUUID: () => "fixed" });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.endsWith("/api/v1/chat/sessions") && method === "POST") return jsonResponse(sessionResponse(), 201);
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      if (path.includes("/api/v1/rag/ask")) return jsonResponse(askSuccess(true));
      return jsonResponse({ data: [] });
    })
  );

  renderChat();
  await screen.findByText("Viewer / viewer");
  fireEvent.change(await screen.findByLabelText("message"), { target: { value: "What is RAG?" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("RAG answers with grounded citations [1].")).toBeInTheDocument();
  expect(screen.getByText("再表示")).toBeInTheDocument();
});

test("no_context keeps the user message, removes loading, and shows safe error", async () => {
  vi.stubGlobal("crypto", { randomUUID: () => "no-context" });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.endsWith("/api/v1/chat/sessions") && method === "POST") return jsonResponse(sessionResponse(), 201);
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      if (path.includes("/api/v1/rag/ask")) {
        return jsonResponse({ error: { code: "no_context_found", message: "No context found." }, meta: {} }, 422);
      }
      return jsonResponse(emptyMessages());
    })
  );

  renderChat();
  await screen.findByText("Viewer / viewer");
  fireEvent.change(await screen.findByLabelText("message"), { target: { value: "Unknown topic" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("回答に使える根拠が見つかりませんでした。")).toBeInTheDocument();
  expect(screen.getByText("Unknown topic")).toBeInTheDocument();
  expect(screen.queryByText("回答を生成しています...")).not.toBeInTheDocument();
});

test("request_in_progress removes optimistic rows and shows processing notice", async () => {
  vi.stubGlobal("crypto", { randomUUID: () => "running" });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.endsWith("/api/v1/chat/sessions") && method === "POST") return jsonResponse(sessionResponse(), 201);
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse());
      if (path.includes("/api/v1/chat/sessions/10/messages")) return jsonResponse(emptyMessages());
      if (path.includes("/api/v1/rag/ask")) {
        return jsonResponse(
          { error: { code: "request_in_progress", message: "Request is already in progress." }, meta: {} },
          409
        );
      }
      return jsonResponse(emptyMessages());
    })
  );

  renderChat();
  await screen.findByText("Viewer / viewer");
  fireEvent.change(await screen.findByLabelText("message"), { target: { value: "Still running" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText(/処理中です/)).toBeInTheDocument();
  expect(screen.queryByText("Still running")).not.toBeInTheDocument();
  expect(screen.queryByText("回答を生成しています...")).not.toBeInTheDocument();
});

test("existing route session disables submission until session detail loads", async () => {
  let resolveSession: (response: Response) => void = () => undefined;
  const sessionPromise = new Promise<Response>((resolve) => {
    resolveSession = resolve;
  });
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.endsWith("/api/v1/chat/sessions/10/messages?page=1&page_size=100")) return jsonResponse(emptyMessages());
    if (path.endsWith("/api/v1/chat/sessions/10")) return sessionPromise;
    if (path.endsWith("/api/v1/chat/sessions") && method === "POST") return jsonResponse(sessionResponse(), 201);
    if (path.includes("/api/v1/rag/ask")) return jsonResponse(askSuccess());
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat("/chat/10");
  await screen.findByText("Viewer / viewer");

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
    const method = init?.method ?? "GET";
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.endsWith("/api/v1/chat/sessions/10/messages?page=1&page_size=100")) return jsonResponse(emptyMessages());
    if (path.endsWith("/api/v1/chat/sessions/10")) {
      return jsonResponse({ error: { code: "resource_not_found", message: "not found" }, meta: {} }, 404);
    }
    if (path.endsWith("/api/v1/chat/sessions") && method === "POST") return jsonResponse(sessionResponse(), 201);
    if (path.includes("/api/v1/rag/ask")) return jsonResponse(askSuccess());
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat("/chat/10");
  await screen.findByText("Viewer / viewer");
  await waitFor(() => expect(screen.getByLabelText("message")).toBeDisabled());

  fireEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(fetchMock.mock.calls.some(([url, init]) => String(url).endsWith("/api/v1/chat/sessions") && init?.method === "POST")).toBe(
    false
  );
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/v1/rag/ask"))).toBe(false);
});

test("archived and temporary expired sessions disable the composer", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const path = String(input);
    if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
    if (path.endsWith("/api/v1/chat/sessions/10/messages?page=1&page_size=100")) return jsonResponse(emptyMessages());
    if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse("archived"));
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);

  renderChat("/chat/10");

  expect(await screen.findByText("アーカイブ済みのため読み取り専用です。")).toBeInTheDocument();
  const input = await screen.findByLabelText("message");
  expect(input).toBeDisabled();
  expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

  vi.unstubAllGlobals();
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
      if (path.endsWith("/api/v1/chat/sessions/10/messages?page=1&page_size=100")) return jsonResponse(emptyMessages());
      if (path.endsWith("/api/v1/chat/sessions/10")) return jsonResponse(sessionResponse("temporary_expired"));
      return jsonResponse({ data: [] });
    })
  );

  renderChat("/chat/temp/10", "temporary");

  expect(await screen.findByText("一時チャットの期限が切れたため読み取り専用です。")).toBeInTheDocument();
  expect(screen.getAllByLabelText("message")[1]).toBeDisabled();
});

test("reload after failed ask renders only the persisted user message", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) return jsonResponse(meResponse());
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
  expect(screen.queryByText("回答を生成しています...")).not.toBeInTheDocument();
});
