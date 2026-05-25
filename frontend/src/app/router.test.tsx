import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { AppProviders } from "./providers";
import { AppRouter } from "./router";
import { queryClient } from "../lib/queryClient";

beforeEach(() => {
  queryClient.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ data: { user_id: 2, email: "viewer@example.com", display_name: "Viewer", role: "viewer" } }),
            { status: 200 }
          )
        );
      }
      if (path.includes("/api/v1/chat/sessions?")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ data: [], meta: { pagination: { page: 1, page_size: 50, total: 0, has_next: false } } }),
            { status: 200 }
          )
        );
      }
      return Promise.resolve(new Response(JSON.stringify({ data: [] }), { status: 200 }));
    })
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders navigation", async () => {
  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );
  expect(screen.getByRole("link", { name: "Chat" })).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "User settings" })).toHaveAttribute("href", "/settings");
});
