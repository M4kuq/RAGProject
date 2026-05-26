import { expect, test } from "vitest";
import { formatUnknownValue, redactValue, safeRecord } from "./redaction";

test("retrieval debug redaction removes forbidden trace keys and secret-like values", () => {
  const redacted = safeRecord({
    query_hash: "a".repeat(64),
    raw_prompt: "full prompt must not render",
    nested: {
      apikey: "sk-uncovered-secret",
      cookie: "cookie-must-not-render",
      content_text: "raw chunk text must not render",
      csrf: "csrf-must-not-render",
      private_key: "private-key-must-not-render",
      safe_assignment: "DATABASE_PASSWORD=secret-value",
      session_id: "session-must-not-render",
      source_label: "phase2.md"
    }
  });

  expect(redacted.query_hash).toBe("a".repeat(64));
  expect(redacted.raw_prompt).toBeUndefined();
  expect((redacted.nested as Record<string, unknown>).apikey).toBeUndefined();
  expect((redacted.nested as Record<string, unknown>).cookie).toBeUndefined();
  expect((redacted.nested as Record<string, unknown>).content_text).toBeUndefined();
  expect((redacted.nested as Record<string, unknown>).csrf).toBeUndefined();
  expect((redacted.nested as Record<string, unknown>).private_key).toBeUndefined();
  expect((redacted.nested as Record<string, unknown>).safe_assignment).toBe("[redacted]");
  expect((redacted.nested as Record<string, unknown>).session_id).toBeUndefined();
  expect((redacted.nested as Record<string, unknown>).source_label).toBe("phase2.md");
  expect(JSON.stringify(redacted)).not.toContain("full prompt");
  expect(JSON.stringify(redacted)).not.toContain("raw chunk");
  expect(JSON.stringify(redacted)).not.toContain("secret-value");
  expect(JSON.stringify(redacted)).not.toContain("csrf-must-not-render");
  expect(JSON.stringify(redacted)).not.toContain("session-must-not-render");
  expect(JSON.stringify(redacted)).not.toContain("cookie-must-not-render");
});

test("retrieval debug value formatting avoids unsafe primitive display", () => {
  expect(formatUnknownValue("OPENAI_API_KEY=sk-secret")).toBe("[redacted]");
  expect(formatUnknownValue("version_token")).toBe("version_token");
  expect(formatUnknownValue("+1 555 111 2222")).toBe("[redacted]");
  expect(redactValue(["safe", "token=secret-value", "version_token"])).toEqual([
    "safe",
    "[redacted]",
    "version_token"
  ]);
  expect(formatUnknownValue(-1)).toBe("-1");
});
