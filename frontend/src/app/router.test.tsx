import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { AppProviders } from "./providers";
import { AppRouter } from "./router";
import { queryClient } from "../lib/queryClient";

beforeEach(() => {
  queryClient.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ data: { user_id: 2, email: "viewer@example.com", display_name: "Viewer", role: "viewer" } }), {
          status: 200
        })
      )
    )
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("renders navigation", () => {
  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );
  expect(screen.getByRole("link", { name: "Chat" })).toBeInTheDocument();
});
