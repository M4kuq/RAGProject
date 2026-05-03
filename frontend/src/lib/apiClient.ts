const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const CSRF_COOKIE_NAME = "rag_csrf";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let csrfToken: string | null = null;

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

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!headers.has("content-type") && init.body && !isFormBody(init.body)) {
    headers.set("content-type", "application/json");
  }
  if (UNSAFE_METHODS.has(method) && !headers.has("x-csrf-token")) {
    const token = csrfToken ?? readCookie(CSRF_COOKIE_NAME);
    if (token) {
      headers.set("x-csrf-token", token);
    }
  }

  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...init,
    headers
  });
  const json = await response.json().catch(() => ({}));
  updateCsrfToken(json);
  if (!response.ok) {
    const message = json.error?.message ?? response.statusText;
    throw new Error(message);
  }
  return json as T;
}
