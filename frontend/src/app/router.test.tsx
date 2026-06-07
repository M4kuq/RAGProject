import { render, screen, within } from "@testing-library/react";
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
  window.history.pushState({}, "", "/");
  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );
  expect(screen.getByRole("link", { name: "Chat" })).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "User settings" })).toHaveAttribute("href", "/settings");
  expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
});

test("hides application navigation on login page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) {
        return Promise.resolve(
          new Response(JSON.stringify({ error: { code: "unauthorized", message: "Login required." } }), {
            status: 401
          })
        );
      }
      return Promise.resolve(new Response(JSON.stringify({ data: [] }), { status: 200 }));
    })
  );
  window.history.pushState({}, "", "/login");
  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "RAGProject" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Chat" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "User settings" })).not.toBeInTheDocument();
});

test("redirects authenticated users away from login page", async () => {
  window.history.pushState({}, "", "/login");
  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "New chat" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "RAGProject" })).not.toBeInTheDocument();
});

test("keeps admin navigation in the chat sidebar for admin users", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith("/api/v1/auth/me")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" } }),
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
  window.history.pushState({}, "", "/");

  const rendered = render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  const adminLink = await screen.findByRole("link", { name: "Admin" });
  expect(adminLink).toHaveAttribute("href", "/admin/documents");

  const topNav = rendered.container.querySelector(".topnav");
  expect(topNav).toBeInTheDocument();
  expect(within(topNav as HTMLElement).queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
});
