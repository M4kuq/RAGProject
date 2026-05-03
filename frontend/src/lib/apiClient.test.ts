import { afterEach, expect, test, vi } from "vitest";

import { apiFetch } from "./apiClient";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "rag_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
});

test("adds csrf header to unsafe requests", async () => {
  document.cookie = "rag_csrf=test-token";
  const fetchMock = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ data: { ok: true } }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  await apiFetch("/api/v1/rag/ask", {
    method: "POST",
    body: JSON.stringify({ question: "What is RAG?" })
  });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(new Headers(init.headers).get("x-csrf-token")).toBe("test-token");
});

test("keeps form data content type unset", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify({ data: { ok: true } }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  await apiFetch("/api/v1/documents", {
    method: "POST",
    body: new FormData()
  });

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(new Headers(init.headers).has("content-type")).toBe(false);
});
