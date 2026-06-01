const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const CSRF_COOKIE_NAME = "rag_csrf";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let csrfToken: string | null = null;

export function resetApiClientStateForTests(): void {
  if (import.meta.env.MODE === "test") {
    csrfToken = null;
  }
}

export class ApiError extends Error {
  code: string;
  status: number;
  requestId: string | null;
  details: unknown;

  constructor({
    code,
    details = null,
    message,
    requestId,
    status
  }: {
    code: string;
    details?: unknown;
    message: string;
    requestId: string | null;
    status: number;
  }) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.details = details;
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const prefix = `${name}=`;
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : null;
}

function isFormBody(body: BodyInit | null | undefined): boolean {
  return typeof FormData !== "undefined" && body instanceof FormData;
}

function updateCsrfToken(json: unknown): void {
  if (
    typeof json === "object" &&
    json !== null &&
    "data" in json &&
    typeof json.data === "object" &&
    json.data !== null &&
    "csrf_token" in json.data &&
    typeof json.data.csrf_token === "string"
  ) {
    csrfToken = json.data.csrf_token;
  }
}

function readError(json: unknown, status: number, fallback: string): ApiError {
  if (
    typeof json === "object" &&
    json !== null &&
    "error" in json &&
    typeof json.error === "object" &&
    json.error !== null
  ) {
    const error = json.error as Record<string, unknown>;
    const meta =
      "meta" in json && typeof json.meta === "object" && json.meta !== null
        ? (json.meta as Record<string, unknown>)
        : {};
    return new ApiError({
      code: typeof error.code === "string" ? error.code : "error",
      details: "details" in error ? error.details : null,
      message: typeof error.message === "string" ? error.message : fallback,
      requestId: typeof meta.request_id === "string" ? meta.request_id : null,
      status
    });
  }
  return new ApiError({ code: "error", message: fallback, requestId: null, status });
}

function isCsrfError(error: ApiError): boolean {
  return error.status === 403 && (error.code === "csrf_invalid" || error.code === "csrf_missing");
}

async function refreshCsrfToken(): Promise<string | null> {
  const response = await fetch(`${API_BASE}/api/v1/auth/csrf`, {
    credentials: "include"
  });
  const json = await response.json().catch(() => ({}));
  updateCsrfToken(json);
  if (!response.ok) {
    throw readError(json, response.status, response.statusText);
  }
  return csrfToken;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  const hasExplicitCsrfHeader = headers.has("x-csrf-token");
  if (!headers.has("content-type") && init.body && !isFormBody(init.body)) {
    headers.set("content-type", "application/json");
  }
  if (UNSAFE_METHODS.has(method) && !hasExplicitCsrfHeader) {
    let token = csrfToken ?? readCookie(CSRF_COOKIE_NAME);
    if (!token) {
      token = await refreshCsrfToken();
    }
    if (token) {
      headers.set("x-csrf-token", token);
    }
  }

  async function send(currentHeaders: Headers): Promise<{ error: ApiError | null; json: unknown }> {
    const response = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
      ...init,
      headers: new Headers(currentHeaders)
    });
    const json = await response.json().catch(() => ({}));
    updateCsrfToken(json);
    if (!response.ok) {
      return { error: readError(json, response.status, response.statusText), json };
    }
    return { error: null, json };
  }

  let result = await send(headers);
  if (
    result.error &&
    UNSAFE_METHODS.has(method) &&
    !hasExplicitCsrfHeader &&
    isCsrfError(result.error)
  ) {
    csrfToken = null;
    const refreshedToken = await refreshCsrfToken();
    if (refreshedToken) {
      headers.set("x-csrf-token", refreshedToken);
      result = await send(headers);
    }
  }

  if (result.error) {
    throw result.error;
  }
  const json = result.json;
  updateCsrfToken(json);
  return json as T;
}
