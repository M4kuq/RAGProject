const ABSOLUTE_PATH_PATTERN = /(^[a-zA-Z]:[\\/])|(^\/[A-Za-z0-9_.-]+\/)/;
const SENSITIVE_TEXT_PATTERN = /(token|secret|password|csrf|session|cookie)\s*[:=]/i;

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function truncateText(value: string | null | undefined, maxLength = 120): string {
  if (!value) {
    return "-";
  }
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, Math.max(0, maxLength - 3))}...`;
}

export function isUnsafeDisplayValue(value: unknown): boolean {
  return typeof value === "string" && ABSOLUTE_PATH_PATTERN.test(value);
}

export function formatSafeText(value: string | null | undefined, maxLength = 120): string {
  if (!value) {
    return "-";
  }
  if (isUnsafeDisplayValue(value) || SENSITIVE_TEXT_PATTERN.test(value)) {
    return "[redacted]";
  }
  return truncateText(value, maxLength);
}
