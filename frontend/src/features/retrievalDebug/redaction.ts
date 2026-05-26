const SENSITIVE_KEY_PARTS = [
  "api-key",
  "api_key",
  "apikey",
  "chunk_text",
  "content_text",
  "credential",
  "cookie",
  "csrf",
  "full_context",
  "password",
  "pii",
  "private_key",
  "prompt",
  "raw_chunk",
  "raw_text",
  "secret",
  "session",
  "token"
];

const SECRET_ASSIGNMENT_PATTERN =
  /(?:^|\s)(?:export\s+)?[A-Z0-9_.-]*(?:api[_-]?key|secret|password|token|credential)[A-Z0-9_.-]*\s*[:=]\s*\S+/i;
const URL_PATTERN = /\b[a-z][a-z0-9+.-]*:\/\//i;
const EMAIL_PATTERN = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i;
const PHONE_PATTERN = /\b(?:\+?\d[\d ._-]{7,}\d)\b/;
const CONTROL_CHARS_PATTERN = /[\u0000-\u001f\u007f]/g;

export function isSensitiveKey(key: string): boolean {
  const normalized = key.toLowerCase();
  return SENSITIVE_KEY_PARTS.some((part) => normalized.includes(part));
}

export function redactString(value: string, maxLength = 255): string {
  const normalized = value.replace(CONTROL_CHARS_PATTERN, " ").replace(/\s+/g, " ").trim();
  if (
    SECRET_ASSIGNMENT_PATTERN.test(normalized) ||
    URL_PATTERN.test(normalized) ||
    EMAIL_PATTERN.test(normalized) ||
    PHONE_PATTERN.test(normalized)
  ) {
    return "[redacted]";
  }
  return normalized.length > maxLength ? `${normalized.slice(0, Math.max(0, maxLength - 3))}...` : normalized;
}

export function redactValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => redactValue(item));
  }
  if (typeof value === "string") {
    return redactString(value);
  }
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>).reduce<Record<string, unknown>>(
      (safe, [key, nested]) => {
        if (!isSensitiveKey(key)) {
          safe[key] = redactValue(nested);
        }
        return safe;
      },
      {}
    );
  }
  return value;
}

export function safeRecord(value: Record<string, unknown> | null | undefined): Record<string, unknown> {
  const redacted = redactValue(value ?? {});
  return redacted && typeof redacted === "object" && !Array.isArray(redacted)
    ? (redacted as Record<string, unknown>)
    : {};
}

export function formatUnknownValue(value: unknown): string {
  const redacted = redactValue(value);
  if (redacted === null || redacted === undefined || redacted === "") {
    return "N/A";
  }
  if (typeof redacted === "number") {
    return Number.isFinite(redacted) ? String(redacted) : "N/A";
  }
  if (typeof redacted === "boolean") {
    return redacted ? "true" : "false";
  }
  if (typeof redacted === "string") {
    return redacted || "N/A";
  }
  return JSON.stringify(redacted);
}
