import { isUnsafeDisplayValue, truncateText } from "../../lib/format";

const SENSITIVE_KEY_PATTERN = /(token|secret|password|csrf|session|cookie|raw|content|prompt|payload_json|path)/i;

function safeEntries(payload: Record<string, unknown>): Array<[string, string]> {
  return Object.entries(payload)
    .filter(([key, value]) => !SENSITIVE_KEY_PATTERN.test(key) && !isUnsafeDisplayValue(value))
    .map(([key, value]) => {
      if (value === null || value === undefined) {
        return [key, "-"];
      }
      if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
        return [key, truncateText(String(value), 80)];
      }
      return [key, "[redacted object]"];
    });
}

export function JobPayloadView({ payload }: { payload: Record<string, unknown> }) {
  const entries = safeEntries(payload);
  if (entries.length === 0) {
    return <p className="muted">No safe payload fields.</p>;
  }
  return (
    <dl className="detail-grid">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

