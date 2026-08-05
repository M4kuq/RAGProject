import { describe, expect, test } from "vitest";
import { getRedirectTarget } from "./LoginPage";

describe("getRedirectTarget", () => {
  test("keeps a same-origin application route", () => {
    expect(
      getRedirectTarget({
        from: { pathname: "/admin/documents", search: "?page=2", hash: "#pending" }
      })
    ).toBe("/admin/documents?page=2#pending");
  });

  test.each([
    ["protocol-relative path", "//attacker.example"],
    ["backslash path", "/\\attacker.example"],
    ["absolute URL", "https://attacker.example"],
    ["control-character path", "/safe\n//attacker.example"]
  ])("rejects a %s", (_label, pathname) => {
    expect(getRedirectTarget({ from: { pathname } })).toBe("/chat");
  });

  test("ignores malformed search and hash state", () => {
    expect(
      getRedirectTarget({
        from: { pathname: "/settings", search: "redirect=//attacker.example", hash: "fragment" }
      })
    ).toBe("/settings");
  });
});
