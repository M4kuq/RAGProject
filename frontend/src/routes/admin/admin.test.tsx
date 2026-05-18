import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AdminSidebar } from "../../components/admin/AdminSidebar";
import { DocumentUploadForm } from "../../components/admin/DocumentUploadForm";
import { JobPayloadView } from "../../components/admin/JobPayloadView";
import { AppProviders } from "../../app/providers";
import { AppRouter } from "../../app/router";
import { queryClient } from "../../lib/queryClient";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  queryClient.clear();
  window.history.pushState({}, "", "/");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("AdminSidebar renders document review and job links", () => {
  render(
    <MemoryRouter>
      <AdminSidebar />
    </MemoryRouter>
  );

  expect(screen.getByRole("link", { name: "Documents" })).toHaveAttribute("href", "/admin/documents");
  expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute("href", "/admin/documents/review");
  expect(screen.getByRole("link", { name: "Jobs" })).toHaveAttribute("href", "/admin/jobs");
});

test("viewer cannot enter admin route and does not see admin navigation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 2, email: "viewer@example.com", display_name: "Viewer", role: "viewer" }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/documents");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "Forbidden" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
});

test("document list renders filters, statuses and safe escaped text", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
        });
      }
      if (url.includes("/api/v1/documents")) {
        return jsonResponse({
          data: [
            {
              logical_document_id: 1000,
              document_name: "<script>alert(1)</script>",
              title: "<b>Guide</b>",
              status: "active",
              display_status: "pending_review",
              latest_version: { document_version_id: 2000, version_no: 2 },
              active_version: null,
              created_at: "2026-04-30T00:00:00Z",
              updated_at: "2026-04-30T00:00:00Z"
            }
          ],
          meta: { pagination: { page: 1, page_size: 20, total: 1, has_next: false } }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/documents");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "Documents" })).toBeInTheDocument();
  expect(screen.getByLabelText("status")).toBeInTheDocument();
  expect(screen.getByText("pending_review")).toBeInTheDocument();
  expect(await screen.findByText("<b>Guide</b>")).toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
});

test("upload form validates extension before reading or sending file", async () => {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <DocumentUploadForm />
    </QueryClientProvider>
  );

  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Unsafe file" } });
  fireEvent.change(screen.getByLabelText("file"), {
    target: { files: [new File(["payload"], "payload.exe", { type: "application/octet-stream" })] }
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(".pdf");
});

test("upload success shows created document and job summary", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      jsonResponse(
        {
          data: {
            logical_document_id: 1000,
            document_version_id: 2001,
            job_id: 300,
            ingest_status: "queued",
            version_status: "processing",
            display_status: "processing",
            result_code: "created",
            document: {},
            version: {}
          }
        },
        201
      )
    )
  );
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <DocumentUploadForm />
    </QueryClientProvider>
  );

  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Guide" } });
  fireEvent.change(screen.getByLabelText("file"), {
    target: { files: [new File(["payload"], "guide.txt", { type: "text/plain" })] }
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload" }));

  expect(await screen.findByText(/Uploaded document #1000/)).toHaveTextContent("job #300");
});

test("job payload view redacts secret and absolute path fields", () => {
  render(
    <JobPayloadView
      payload={{
        logical_document_id: 1000,
        token: "secret-token",
        storage_path: "C:\\storage\\uploads\\raw.txt",
        safe_label: "Document ingest"
      }}
    />
  );

  expect(screen.getByText("logical_document_id")).toBeInTheDocument();
  expect(screen.getByText("safe_label")).toBeInTheDocument();
  expect(screen.queryByText("secret-token")).not.toBeInTheDocument();
  expect(screen.queryByText("C:\\storage\\uploads\\raw.txt")).not.toBeInTheDocument();
});

test("failed job retry sends mutation with csrf header", async () => {
  document.cookie = "rag_csrf=test-token";
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith("/api/v1/auth/me")) {
      return jsonResponse({
        data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
      });
    }
    if (url.endsWith("/api/v1/jobs/300/retry")) {
      expect(new Headers(init?.headers).get("x-csrf-token")).toBe("test-token");
      return jsonResponse({ data: { result_code: "retry_created", job_id: 301, source_job_id: 300, status: "queued", retry_count: 1 } }, 201);
    }
    if (url.includes("/api/v1/jobs")) {
      return jsonResponse({
        data: [
          {
            job_id: 300,
            job_type: "document_ingest",
            status: "failed",
            priority: 100,
            target_type: "document_version",
            target_id: 2000,
            retry_of_job_id: null,
            retry_count: 0,
            created_by: 1,
            started_at: null,
            finished_at: null,
            created_at: "2026-04-30T00:00:00Z",
            updated_at: "2026-04-30T00:00:00Z",
            error_code: "embedding_failed",
            error_message: "Embedding failed",
            payload_view: { payload: { logical_document_id: 1000 }, payload_redacted: true }
          }
        ],
        meta: { pagination: { page: 1, page_size: 20, total: 1, has_next: false } }
      });
    }
    return jsonResponse({ data: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  window.history.pushState({}, "", "/admin/jobs");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/jobs/300/retry"), expect.any(Object)));
});
