import { expect, test } from "vitest";
import { formatUnknownValue, redactValue, safeRecord } from "./redaction";

test("retrieval debug redaction removes forbidden trace keys and secret-like values", () => {
  const redacted = safeRecord({
    query_hash: "a".repeat(64),
    raw_prompt: "full prompt must not render",
    nested: {
      content_text: "raw chunk text must not render",
      safe_assignment: "DATABASE_PASSWORD=secret-value",
      source_label: "phase2.md"
    }
  });

  expect(redacted.query_hash).toBe("a".repeat(64));
  expect(redacted.raw_prompt).toBeUndefined();
  expect((redacted.nested as Record<string, unknown>).content_text).toBeUndefined();
  expect((redacted.nested as Record<string, unknown>).safe_assignment).toBe("[redacted]");
  expect((redacted.nested as Record<string, unknown>).source_label).toBe("phase2.md");
  expect(JSON.stringify(redacted)).not.toContain("full prompt");
  expect(JSON.stringify(redacted)).not.toContain("raw chunk");
  expect(JSON.stringify(redacted)).not.toContain("secret-value");
});

test("retrieval debug value formatting avoids unsafe primitive display", () => {
  expect(formatUnknownValue("OPENAI_API_KEY=sk-secret")).toBe("[redacted]");
  expect(formatUnknownValue(-1)).toBe("-1");
  expect(redactValue(["safe", "token=secret-value"])).toEqual(["safe", "[redacted]"]);
});
