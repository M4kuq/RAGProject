import { afterEach, beforeEach, expect, test, vi } from "vitest";

let apiFetch: (path: string, init?: RequestInit) => Promise<unknown>;
let ApiErrorClass: typeof import("./apiClient").ApiError;

beforeEach(async () => {
  vi.resetModules();
  ({ apiFetch, ApiError: ApiErrorClass } = await import("./apiClient"));
});

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

test("updates csrf header from login response body", async () => {
  document.cookie = "rag_csrf=pre-auth-token";
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ data: { user: { user_id: 1 }, csrf_token: "session-token" } }), {
        status: 200
      })
    )
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: { ok: true } }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  await apiFetch("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email: "admin@example.com", password: "password" })
  });
  await apiFetch("/api/v1/rag/ask", {
    method: "POST",
    body: JSON.stringify({ question: "What is RAG?" })
  });

  const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
  expect(new Headers(init.headers).get("x-csrf-token")).toBe("session-token");
});

test("refreshes csrf token before unsafe requests when page state was reloaded", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: { csrf_token: "refreshed-token" } }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: { ok: true } }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  await apiFetch("/api/v1/rag/ask", {
    method: "POST",
    body: JSON.stringify({ question: "What is RAG?" })
  });

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/auth/csrf");
  const [, init] = fetchMock.mock.calls[1] as [string, RequestInit];
  expect(new Headers(init.headers).get("x-csrf-token")).toBe("refreshed-token");
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

test("maps API errors to safe client errors", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        error: {
          code: "job_active_retry_exists",
          message: "An active retry already exists.",
          details: { active_retry_job_id: 301 }
        }
      }),
      { status: 409 }
    )
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(apiFetch("/api/v1/jobs/300/retry", { method: "POST" })).rejects.toMatchObject({
    code: "job_active_retry_exists",
    status: 409,
    message: "An active retry already exists."
  });
  await expect(apiFetch("/api/v1/jobs/300/retry", { method: "POST" })).rejects.toBeInstanceOf(ApiErrorClass);
});
