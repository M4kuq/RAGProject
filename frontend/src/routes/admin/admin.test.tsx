import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { AdminSidebar } from "../../components/admin/AdminSidebar";
import { DocumentUploadForm } from "../../components/admin/DocumentUploadForm";
import { JobPayloadView } from "../../components/admin/JobPayloadView";
import { AppProviders } from "../../app/providers";
import { AppRouter } from "../../app/router";
import { resetApiClientStateForTests } from "../../lib/apiClient";
import { queryClient } from "../../lib/queryClient";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  queryClient.clear();
  resetApiClientStateForTests();
  document.cookie = "rag_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
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
  expect(screen.getByRole("link", { name: "Retrieval Debug" })).toHaveAttribute(
    "href",
    "/admin/retrieval-debug"
  );
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

test("unauthenticated admin route redirects to login", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({ error: { code: "unauthorized", message: "Login required." } }, 401);
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

  expect(await screen.findByRole("heading", { name: "RAGProject" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Forbidden" })).not.toBeInTheDocument();
});

test("login returns to the originally requested admin route", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({ error: { code: "unauthorized", message: "Login required." } }, 401);
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "pre-auth" } });
      }
      if (url.endsWith("/api/v1/auth/login")) {
        return jsonResponse({
          data: {
            user: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" },
            csrf_token: "session-token"
          }
        });
      }
      if (url.includes("/api/v1/documents")) {
        return jsonResponse({
          data: [],
          meta: { pagination: { page: 1, page_size: 20, total: 0, has_next: false } }
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

  fireEvent.click(await screen.findByRole("button", { name: "Login" }));
  expect(await screen.findByRole("heading", { name: "Documents" })).toBeInTheDocument();
});

test("admin auth load failure is not shown as forbidden", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({ error: { code: "server_error", message: "Auth unavailable." } }, 500);
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

  expect(await screen.findByRole("heading", { name: "Unable to load user" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Forbidden" })).not.toBeInTheDocument();
});

test("keeps the existing admin evaluation page reachable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("button", { name: "Run evaluation" })).toBeInTheDocument();
});

test("admin evaluation dataset detail shows cases and export", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
      }
      if (url.endsWith("/api/v1/evaluations/datasets/10/export")) {
        return jsonResponse({
          data: {
            schema_version: "phase2.evaluation_dataset.v1",
            dataset: {
              dataset_name: "phase2_strategy_smoke",
              description: "Phase2 dataset",
              version: "v1",
              source_type: "fixture",
              status: "active",
              metadata_json: null
            },
            cases: [],
            metric_specs: []
          }
        });
      }
      if (url.includes("/api/v1/evaluations/datasets/10/cases")) {
        return jsonResponse({
          data: [
            {
              evaluation_case_id: 100,
              evaluation_dataset_id: 10,
              case_key: "dense_case",
              question: "What vector database is used?",
              expected_answer: null,
              expected_keywords: ["Qdrant"],
              expected_document_ids: [],
              expected_chunk_ids: [],
              required_citation: true,
              tags: ["dense"],
              metadata_json: null,
              status: "active",
              created_at: "2026-04-30T00:00:00Z",
              updated_at: "2026-04-30T00:00:00Z"
            }
          ],
          meta: { pagination: { page: 1, page_size: 50, total: 1, has_next: false } }
        });
      }
      if (url.endsWith("/api/v1/evaluations/datasets/10")) {
        return jsonResponse({
          data: {
            evaluation_dataset_id: 10,
            dataset_name: "phase2_strategy_smoke",
            description: "Phase2 dataset",
            version: "v1",
            source_type: "fixture",
            status: "active",
            metadata_json: null,
            case_count: 1,
            created_by: 1,
            created_at: "2026-04-30T00:00:00Z",
            updated_at: "2026-04-30T00:00:00Z"
          }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/evaluations/datasets/10");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "phase2_strategy_smoke" })).toBeInTheDocument();
  expect(await screen.findByText("dense_case")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  expect(await screen.findByText(/phase2.evaluation_dataset.v1/)).toBeInTheDocument();
});

test("evaluation detail promotes fixture failures to selected dataset with backend priority", async () => {
  const promoteRequests: RequestInit[] = [];
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
      }
      if (url.includes("/api/v1/evaluations/datasets")) {
        if (!url.includes("page=2")) {
          return jsonResponse({
            data: [
              {
                evaluation_dataset_id: 41,
                dataset_name: "archived_failures",
                description: "Archived dataset",
                version: "v1",
                source_type: "manual",
                status: "archived",
                metadata_json: null,
                case_count: 0,
                created_by: 1,
                created_at: "2026-05-01T00:00:00Z",
                updated_at: "2026-05-01T00:00:00Z"
              }
            ],
            meta: { pagination: { page: 1, page_size: 100, total: 2, has_next: true } }
          });
        }
        return jsonResponse({
          data: [
            {
              evaluation_dataset_id: 42,
              dataset_name: "promoted_failures",
              description: "Target dataset",
              version: "v1",
              source_type: "manual",
              status: "active",
              metadata_json: null,
              case_count: 0,
              created_by: 1,
              created_at: "2026-05-01T00:00:00Z",
              updated_at: "2026-05-01T00:00:00Z"
            }
          ],
          meta: { pagination: { page: 2, page_size: 100, total: 2, has_next: false } }
        });
      }
      if (url.endsWith("/api/v1/evaluations/runs/77/promote-failures")) {
        promoteRequests.push(init ?? {});
        return jsonResponse({
          data: {
            evaluation_run_id: 77,
            target_dataset_id: 42,
            created_count: 1,
            skipped_count: 0,
            items: [
              {
                promotion_key: "promotion-key-1",
                failure_type: "retrieval_exception",
                strategy_type: "agentic_router",
                evaluation_run_item_id: 700,
                evaluation_case_id: null,
                promoted_case_id: 900,
                case_key: "failure_case",
                result_code: "created"
              }
            ]
          }
        });
      }
      if (url.endsWith("/api/v1/evaluations/runs/77")) {
        return jsonResponse({
          data: {
            evaluation_run_id: 77,
            job_id: 88,
            evaluation_dataset_id: null,
            dataset_name: "phase2_strategy_smoke",
            strategy_type: "agentic_router",
            strategies: ["agentic_router"],
            metric_names: ["no_context_rate"],
            trigger_type: "manual",
            status: "succeeded",
            case_count: 1,
            succeeded_count: 0,
            failed_count: 1,
            metric_summary: {},
            strategy_comparison: [],
            strategy_metrics_summary_json: null,
            error_code: null,
            error_message: null,
            started_at: "2026-05-01T00:00:00Z",
            finished_at: "2026-05-01T00:00:01Z",
            created_at: "2026-05-01T00:00:00Z",
            updated_at: "2026-05-01T00:00:01Z",
            items: [],
            failure_candidates: [
              {
                schema_version: "phase2.evaluation.v1",
                evaluation_run_id: 77,
                evaluation_run_item_id: 700,
                evaluation_case_id: null,
                case_key: "fixture_case",
                question_hash: "a".repeat(64),
                strategy_type: "agentic_router",
                failure_type: "no_context",
                severity: "high",
                failure_reason_codes: ["no_context"],
                metric_snapshot: {},
                recommended_tags: ["failure_promoted"],
                promotion_key: "promotion-key-no-context"
              },
              {
                schema_version: "phase2.evaluation.v1",
                evaluation_run_id: 77,
                evaluation_run_item_id: 700,
                evaluation_case_id: null,
                case_key: "fixture_case",
                question_hash: "a".repeat(64),
                strategy_type: "agentic_router",
                failure_type: "retrieval_exception",
                severity: "high",
                failure_reason_codes: ["rerank_failed"],
                metric_snapshot: {},
                recommended_tags: ["failure_promoted"],
                promotion_key: "promotion-key-retrieval"
              },
              {
                schema_version: "phase2.evaluation.v1",
                evaluation_run_id: 77,
                evaluation_run_item_id: 701,
                evaluation_case_id: null,
                case_key: "unknown_fixture_case",
                question_hash: "b".repeat(64),
                strategy_type: "agentic_router",
                failure_type: "unknown_failure",
                severity: "high",
                failure_reason_codes: ["unknown_failure"],
                metric_snapshot: {},
                recommended_tags: ["failure_promoted"],
                promotion_key: "promotion-key-unknown"
              },
              {
                schema_version: "phase2.evaluation.v1",
                evaluation_run_id: 77,
                evaluation_run_item_id: 701,
                evaluation_case_id: null,
                case_key: "unknown_fixture_case",
                question_hash: "b".repeat(64),
                strategy_type: "agentic_router",
                failure_type: "no_context",
                severity: "high",
                failure_reason_codes: ["no_context"],
                metric_snapshot: {},
                recommended_tags: ["failure_promoted"],
                promotion_key: "promotion-key-known"
              }
            ]
          }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/evaluations/77");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "Evaluation #77" })).toBeInTheDocument();
  expect(await screen.findByRole("option", { name: "promoted_failures" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "archived_failures" })).not.toBeInTheDocument();
  fireEvent.change(await screen.findByLabelText("failure promotion target dataset"), {
    target: { value: "42" }
  });
  fireEvent.click(screen.getByRole("button", { name: "Promote primary failures" }));

  await waitFor(() => expect(promoteRequests.length).toBe(1));
  const body = JSON.parse(String(promoteRequests[0].body));
  expect(body.target_dataset_id).toBe(42);
  expect(body.failure_types).toEqual(["retrieval_exception", "no_context"]);
  expect(await screen.findByText("Promoted 1 case(s), skipped 0.")).toBeInTheDocument();
});

test("retrieval debug runs hybrid search and renders redacted trace details", async () => {
  const searchRequests: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
      }
      if (url.endsWith("/api/v1/rag/search")) {
        searchRequests.push(init ?? {});
        return jsonResponse({
          data: {
            retrieval_run_id: 600,
            status: "succeeded",
            retrieval_score_summary: {
              requested_top_k: 10,
              qdrant_candidate_count: 2,
              sparse_candidate_count: 2,
              post_filter_candidate_count: 1,
              selected_count: 1,
              excluded_by_rdb_check_count: 1,
              top1_retrieval_score: 0.73,
              top3_avg_retrieval_score: 0.73,
              top1_rerank_score: null
            },
            items: [
              {
                retrieval_run_item_id: 900,
                document_chunk_id: 300,
                source_label: "phase2.md",
                snippet: "hybrid retrieval safe snippet",
                page_from: 4,
                page_to: 4,
                retrieval_score: 0.73,
                rerank_score: null,
                rank_order: 1,
                rerank_order: null,
                selected_flag: true,
                payload_snapshot: { source_label: "phase2.md" }
              }
            ]
          }
        });
      }
      if (url.endsWith("/api/v1/rag/retrieval-runs/600")) {
        return jsonResponse({
          data: {
            retrieval_run: {
              retrieval_run_id: 600,
              origin_type: "standalone",
              chat_session_id: null,
              request_message_id: null,
              status: "succeeded",
              strategy_type: "hybrid",
              error_code: null,
              query_hash: "a".repeat(64),
              top_k: 10,
              retrieval_score_summary: {
                selected_count: 1,
                excluded_by_rdb_check_count: 1
              },
              query_plan_json: {
                schema_version: "phase2.trace.v1",
                strategy_type: "hybrid",
                query_mode: "dense_sparse_single_query",
                query_hash: "a".repeat(64),
                intent: "comparison",
                ambiguity_score: 0.1,
                ambiguity_flags: [],
                keyword_heavy_score: 0.65,
                keyword_signals: ["api_endpoint"],
                version_specific_flag: false,
                rewritten_query_preview: "hybrid retrieval",
                sub_query_count: 2,
                sub_queries: [
                  {
                    query_hash: "b".repeat(64),
                    query_preview: "hybrid",
                    intent: "comparison",
                    reason_code: "comparison_component"
                  }
                ],
                metadata_filter_candidates: [
                  {
                    filter_type: "file_extension",
                    field: "source_label",
                    operator: "ends_with",
                    value_preview: ".md",
                    value_hash: "c".repeat(64),
                    confidence: 0.7,
                    reason_code: "file_extension_signal"
                  }
                ],
                candidate_strategies: ["multi_query_hybrid", "hybrid", "dense"],
                recommended_strategy: "multi_query_hybrid",
                safety_flags: ["planned_only"],
                analysis: {
                  schema_version: "phase2.query_plan.v1",
                  intent: "comparison",
                  query_hash: "a".repeat(64),
                  ambiguity_score: 0.1,
                  keyword_heavy_score: 0.65
                },
                raw_prompt: "raw prompt must not appear",
                safe_value: "OPENAI_API_KEY=sk-secret"
              },
              strategy_decision_json: {
                selected_strategy: "hybrid",
                decision_source: "request",
                router_enabled: false,
                fallback_used: false,
                reason_codes: ["explicit_strategy_hybrid"]
              },
              latency_breakdown_json: {
                total_ms: 42,
                query_embedding_ms: 4,
                sparse_search_ms: 5,
                fusion_ms: 3,
                rdb_final_check_ms: 2,
                retrieval_items_persist_ms: 1
              },
              retrieval_settings_json: {
                top_k: 10,
                rerank_top_n: 5,
                embedding_provider: "fake",
                rerank_provider: "fake",
                fusion_method: "rrf",
                router_enabled: false
              },
              rerank_score_top1: null,
              answer_confidence: null,
              groundedness_score: null,
              confidence_label: null,
              started_at: "2026-05-01T00:00:00Z",
              finished_at: "2026-05-01T00:00:01Z",
              created_at: "2026-05-01T00:00:00Z"
            },
            items: [
              {
                retrieval_run_item_id: 900,
                document_chunk_id: 300,
                retrieval_score: 0.73,
                rerank_score: null,
                rank_order: 1,
                rerank_order: null,
                selected_flag: true,
                retrieval_source: "hybrid",
                payload_snapshot: {
                  source_label: "phase2.md",
                  page_from: 4,
                  content_text: "raw chunk text must not appear"
                },
                score_breakdown_json: {
                  retrieval_source: "hybrid",
                  dense_score: 0.7,
                  sparse_score: 0.6,
                  fused_score: 0.73,
                  final_rank: 1,
                  selected_flag: true,
                  raw_chunk_text: "raw chunk text must not appear"
                },
                source_label: "phase2.md",
                page_from: 4,
                page_to: 4,
                old_version_flag: null,
                created_at: "2026-05-01T00:00:00Z"
              }
            ]
          }
        });
      }
      if (url.includes("/api/v1/evaluations/runs")) {
        return jsonResponse({
          data: [
            {
              evaluation_run_id: 10,
              job_id: 20,
              evaluation_dataset_id: 1,
              dataset_name: "phase2_strategy_smoke",
              strategy_type: "dense",
              strategies: ["dense", "sparse", "hybrid"],
              metric_names: ["recall_at_k"],
              trigger_type: "manual",
              status: "succeeded",
              case_count: 3,
              succeeded_count: 3,
              failed_count: 0,
              metric_summary: { recall_at_k: 0.8 },
              strategy_comparison: [
                {
                  schema_version: "phase2.evaluation.v1",
                  strategy_type: "hybrid",
                  metric_name: "recall_at_k",
                  average: 0.8,
                  p50: 0.8,
                  p95: 0.8,
                  count: 1,
                  failed_count: 0,
                  not_applicable_count: 0
                }
              ],
              strategy_metrics_summary_json: null,
              error_code: null,
              error_message: null,
              started_at: "2026-05-01T00:00:00Z",
              finished_at: "2026-05-01T00:00:01Z",
              created_at: "2026-05-01T00:00:00Z",
              updated_at: "2026-05-01T00:00:01Z"
            }
          ],
          meta: { pagination: { page: 1, page_size: 5, total: 1, has_next: false } }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/retrieval-debug");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "Retrieval Debug" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "dense" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "sparse" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "hybrid" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "agentic_router" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "multi_query_hybrid" })).toBeDisabled();

  fireEvent.change(screen.getByLabelText("query"), { target: { value: "hybrid retrieval" } });
  fireEvent.change(screen.getByLabelText("strategy"), { target: { value: "hybrid" } });
  fireEvent.click(screen.getByRole("button", { name: "Run search" }));

  await waitFor(() => expect(searchRequests.length).toBe(1));
  expect(JSON.parse(String(searchRequests[0].body)).strategy).toBe("hybrid");
  expect(await screen.findByText("#600")).toBeInTheDocument();
  expect((await screen.findAllByText("dense_sparse_single_query")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("comparison")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("multi_query_hybrid")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/planned_only/)).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/file_extension/)).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/explicit_strategy_hybrid/)).length).toBeGreaterThan(0);
  expect(await screen.findByText("42 ms")).toBeInTheDocument();
  expect((await screen.findAllByText("0.730")).length).toBeGreaterThan(0);
  expect(await screen.findByText("hybrid retrieval safe snippet")).toBeInTheDocument();
  expect(await screen.findByText("recall_at_k")).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("raw prompt must not appear");
  expect(document.body).not.toHaveTextContent("raw chunk text must not appear");
  expect(document.body).not.toHaveTextContent("OPENAI_API_KEY");
  expect(document.body).not.toHaveTextContent("sk-secret");
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
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
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

test("document detail renders version compare summary and bounded previews", async () => {
  const compareRequests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
      }
      if (url.includes("/api/v1/documents/1000/versions/compare")) {
        compareRequests.push(url);
        return jsonResponse({
          data: {
            logical_document_id: 1000,
            base_version: { document_version_id: 2001, version_no: 1, status: "ready", is_active: false },
            target_version: { document_version_id: 2002, version_no: 2, status: "ready", is_active: true },
            summary: {
              added_chunks: 1,
              removed_chunks: 0,
              changed_chunks: 1,
              unchanged_chunks: 2,
              metadata_changed: true,
              diff_items_returned: 2,
              diff_items_truncated: false
            },
            metadata_diff: [
              { field: "file_name", base_value: "guide-v1.html", target_value: "guide-v2.html", changed: true }
            ],
            chunk_diff_items: [
              {
                diff_type: "changed",
                base_chunk: {
                  document_chunk_id: 3001,
                  chunk_index: 0,
                  source_label: "guide-v1.html / Setup",
                  section_title: "Setup",
                  page_from: null,
                  page_to: null,
                  sheet_name: null,
                  row_from: null,
                  row_to: null,
                  slide_number: null,
                  html_heading_path: "Guide > Setup",
                  xml_path: null,
                  preview: "Old bounded preview",
                  preview_truncated: false
                },
                target_chunk: {
                  document_chunk_id: 3002,
                  chunk_index: 0,
                  source_label: "guide-v2.html / Setup",
                  section_title: "Setup",
                  page_from: null,
                  page_to: null,
                  sheet_name: null,
                  row_from: null,
                  row_to: null,
                  slide_number: null,
                  html_heading_path: "Guide > Setup",
                  xml_path: null,
                  preview: "New bounded preview",
                  preview_truncated: false
                },
                similarity_score: 0.81,
                match_reason: "structural_key"
              }
            ]
          }
        });
      }
      if (url.includes("/api/v1/documents/1000/versions/2002/chunks")) {
        return jsonResponse({
          data: [],
          meta: { pagination: { page: 1, page_size: 20, total: 0, has_next: false } }
        });
      }
      if (url.endsWith("/api/v1/documents/1000")) {
        return jsonResponse({
          data: {
            logical_document_id: 1000,
            document_name: "Guide",
            title: "Guide",
            status: "active",
            display_status: "active",
            latest_version: { document_version_id: 2002, version_no: 2, status: "ready", is_active: true },
            active_version: { document_version_id: 2002, version_no: 2, status: "ready", is_active: true },
            versions: [
              { document_version_id: 2002, version_no: 2, status: "ready", is_active: true, display_status: "active", created_at: "2026-04-30T00:00:00Z", updated_at: "2026-04-30T00:00:00Z" },
              { document_version_id: 2001, version_no: 1, status: "ready", is_active: false, display_status: "pending_review", created_at: "2026-04-29T00:00:00Z", updated_at: "2026-04-29T00:00:00Z" }
            ],
            created_at: "2026-04-28T00:00:00Z",
            updated_at: "2026-04-30T00:00:00Z"
          }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/documents/1000");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "Version Compare" })).toBeInTheDocument();
  expect(await screen.findByText("Select versions and run compare to load the diff.")).toBeInTheDocument();
  expect(compareRequests).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Compare versions" }));
  expect(await screen.findByText("New bounded preview")).toBeInTheDocument();
  expect(compareRequests).toHaveLength(1);
  expect(screen.getByText("file_name")).toBeInTheDocument();
  expect(screen.getByText("Guide > Setup")).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("raw chunk text");
  expect(document.body).not.toHaveTextContent("token=secret");
});

test("document list pagination requests the selected page", async () => {
  const documentRequests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
      }
      if (url.includes("/api/v1/documents")) {
        documentRequests.push(url);
        const page = url.includes("page=2") ? 2 : 1;
        return jsonResponse({
          data: [
            {
              logical_document_id: page,
              document_name: `Guide ${page}`,
              title: `Guide ${page}`,
              status: "active",
              display_status: "active",
              latest_version: null,
              active_version: null,
              created_at: "2026-04-30T00:00:00Z",
              updated_at: "2026-04-30T00:00:00Z"
            }
          ],
          meta: { pagination: { page, page_size: 20, total: 40, has_next: page < 2 } }
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

  fireEvent.click(await screen.findByRole("button", { name: "Next" }));
  await waitFor(() => expect(documentRequests.some((url) => url.includes("page=2"))).toBe(true));
});

test("review page exposes pagination", async () => {
  const reviewRequests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
      }
      if (url.includes("/api/v1/documents")) {
        reviewRequests.push(url);
        const page = url.includes("page=2") ? 2 : 1;
        return jsonResponse({
          data: [
            {
              logical_document_id: page,
              document_name: `Pending ${page}`,
              title: `Pending ${page}`,
              status: "active",
              display_status: "pending_review",
              latest_version: {
                document_version_id: 2000 + page,
                version_no: page,
                status: "ready",
                display_status: "pending_review",
                is_active: false,
                file_name: `pending-${page}.txt`,
                chunk_count: 3,
                created_at: "2026-04-30T00:00:00Z"
              },
              active_version: null,
              created_at: "2026-04-30T00:00:00Z",
              updated_at: "2026-04-30T00:00:00Z"
            }
          ],
          meta: { pagination: { page, page_size: 20, total: 40, has_next: page < 2 } }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/documents/review");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  fireEvent.click(await screen.findByRole("button", { name: "Next" }));
  await waitFor(() => expect(reviewRequests.some((url) => url.includes("page=2"))).toBe(true));
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

test("failed job retry refreshes csrf before mutation", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url.endsWith("/api/v1/auth/me")) {
      return jsonResponse({
        data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
      });
    }
    if (url.endsWith("/api/v1/auth/csrf")) {
      return jsonResponse({ data: { csrf_token: "session-token" } });
    }
    if (url.endsWith("/api/v1/jobs/300/retry")) {
      expect(new Headers(init?.headers).get("x-csrf-token")).toBe("session-token");
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
  expect(await screen.findByRole("button", { name: "Retry queued" })).toBeDisabled();
});

test("job list pagination requests the selected page", async () => {
  const jobRequests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: { user_id: 1, email: "admin@example.com", display_name: "Admin", role: "admin" }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
      }
      if (url.includes("/api/v1/jobs")) {
        jobRequests.push(url);
        const page = url.includes("page=2") ? 2 : 1;
        return jsonResponse({
          data: [
            {
              job_id: 300 + page,
              job_type: "document_ingest",
              status: "succeeded",
              priority: 100,
              target_type: "document_version",
              target_id: 2000 + page,
              retry_of_job_id: null,
              retry_count: 0,
              created_by: 1,
              started_at: null,
              finished_at: null,
              created_at: "2026-04-30T00:00:00Z",
              updated_at: "2026-04-30T00:00:00Z",
              error_code: null,
              error_message: null,
              payload_view: { payload: { logical_document_id: 1000 + page }, payload_redacted: true }
            }
          ],
          meta: { pagination: { page, page_size: 20, total: 40, has_next: page < 2 } }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/jobs");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  fireEvent.click(await screen.findByRole("button", { name: "Next" }));
  await waitFor(() => expect(jobRequests.some((url) => url.includes("page=2"))).toBe(true));
});
