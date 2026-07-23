import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function evaluationRunSummary(evaluationRunId: number, datasetName: string) {
  return {
    evaluation_run_id: evaluationRunId,
    job_id: evaluationRunId + 100,
    evaluation_dataset_id: null,
    dataset_name: datasetName,
    strategy_type: "dense",
    strategies: ["dense"],
    metric_names: ["recall_at_k", "p95_latency"],
    trigger_type: "manual",
    status: "succeeded",
    case_count: 2,
    succeeded_count: 2,
    failed_count: 0,
    metric_summary: { recall_at_k: 0.8, p95_latency: 200 },
    strategy_comparison: [],
    strategy_metrics_summary_json: null,
    total_estimated_cost_usd: 0.123456,
    total_input_tokens: 100,
    total_output_tokens: 40,
    total_tokens: 140,
    avg_generation_latency_ms: 80,
    generation_providers: ["fake"],
    generation_models: ["fake-rag-answer"],
    requested_generation_provider: null,
    requested_generation_model: null,
    error_code: null,
    error_message: null,
    started_at: "2026-06-01T00:00:00Z",
    finished_at: "2026-06-01T00:00:01Z",
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:01Z"
  };
}

function evaluationComparisonPayload() {
  return {
    base_run: evaluationRunSummary(10, "base_dataset"),
    candidate_run: {
      ...evaluationRunSummary(11, "candidate_dataset"),
      metric_summary: { recall_at_k: 0.7, p95_latency: 100 },
      total_estimated_cost_usd: 0.073456,
      total_tokens: 100,
      avg_generation_latency_ms: 40,
      generation_models: ["fake-rag-answer-v2"],
      requested_generation_model: "fake-rag-answer-v2"
    },
    generation: {
      base_estimated_cost_usd: 0.123456,
      candidate_estimated_cost_usd: 0.073456,
      cost_delta: -0.05,
      cost_direction: "improved",
      cost_lower_is_better: true,
      base_total_tokens: 140,
      candidate_total_tokens: 100,
      tokens_delta: -40,
      tokens_direction: "improved",
      tokens_lower_is_better: true,
      base_avg_generation_latency_ms: 80,
      candidate_avg_generation_latency_ms: 40,
      latency_delta: -40,
      latency_direction: "improved",
      latency_lower_is_better: true,
      base_providers: ["fake"],
      base_models: ["fake-rag-answer"],
      candidate_providers: ["fake"],
      candidate_models: ["fake-rag-answer-v2"]
    },
    metrics: [
      {
        metric_name: "recall_at_k",
        base_score: 0.8,
        candidate_score: 0.7,
        delta: -0.1,
        direction: "regressed",
        lower_is_better: false
      },
      {
        metric_name: "p95_latency",
        base_score: 200,
        candidate_score: 100,
        delta: -100,
        direction: "improved",
        lower_is_better: true
      },
      {
        metric_name: "mrr",
        base_score: 0.5,
        candidate_score: 0.5,
        delta: 0,
        direction: "unchanged",
        lower_is_better: false
      }
    ],
    cases: [
      {
        case_id: "shared_pass",
        question_hash: "a".repeat(64),
        case_snapshot_hash: "b".repeat(64),
        comparison_label: "dense",
        base_status: "succeeded",
        candidate_status: "failed",
        transition: "regressed",
        metric_deltas: { recall_at_k: -0.1, p95_latency: -100 }
      },
      {
        case_id: "candidate_only",
        question_hash: "c".repeat(64),
        case_snapshot_hash: "d".repeat(64),
        comparison_label: "dense",
        base_status: null,
        candidate_status: "succeeded",
        transition: "added",
        metric_deltas: {}
      }
    ],
    summary: {
      improved_metric_count: 1,
      regressed_metric_count: 1,
      unchanged_metric_count: 1,
      regressed_case_count: 1,
      improved_case_count: 0,
      common_case_count: 1,
      base_only_case_count: 0,
      candidate_only_case_count: 1
    }
  };
}

function evaluationMetricCatalogPayload() {
  return {
    schema_version: "phase3.evaluation_metric_taxonomy.v1",
    metrics: [
      {
        metric_name: "recall_at_k",
        category: "retrieval",
        display_name: "Recall at K",
        description: "Retrieval recall.",
        higher_is_better: true,
        value_unit: "ratio",
        alias_of: null
      },
      {
        metric_name: "mrr",
        category: "retrieval",
        display_name: "MRR",
        description: "Mean reciprocal rank.",
        higher_is_better: true,
        value_unit: "ratio",
        alias_of: null
      },
      {
        metric_name: "faithfulness",
        category: "answer",
        display_name: "Faithfulness",
        description: "Answer-only faithfulness.",
        higher_is_better: true,
        value_unit: "ratio",
        alias_of: null
      },
      {
        metric_name: "citation_presence",
        category: "citation",
        display_name: "Citation presence",
        description: "Citation presence.",
        higher_is_better: true,
        value_unit: "ratio",
        alias_of: null
      },
      {
        metric_name: "p95_latency",
        category: "performance",
        display_name: "P95 latency",
        description: "P95 latency.",
        higher_is_better: false,
        value_unit: "ms",
        alias_of: null
      }
    ]
  };
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
  vi.unstubAllEnvs();
});

test("AdminSidebar renders document review and job links", () => {
  render(
    <MemoryRouter>
      <AdminSidebar />
    </MemoryRouter>
  );

  expect(screen.getByRole("link", { name: "ドキュメント" })).toHaveAttribute("href", "/admin/documents");
  expect(screen.getByRole("link", { name: "承認" })).toHaveAttribute("href", "/admin/documents/review");
  expect(screen.getByRole("link", { name: "検索デバッグ" })).toHaveAttribute(
    "href",
    "/admin/retrieval-debug"
  );
  expect(screen.getByRole("link", { name: "ジョブ" })).toHaveAttribute("href", "/admin/jobs");
});

test("AdminSidebar highlights review without also highlighting documents", () => {
  render(
    <MemoryRouter initialEntries={["/admin/documents/review"]}>
      <AdminSidebar />
    </MemoryRouter>
  );

  expect(screen.getByRole("link", { name: "承認" })).toHaveClass("active");
  expect(screen.getByRole("link", { name: "承認" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "ドキュメント" })).not.toHaveClass("active");
});

test("AdminSidebar keeps documents active for document detail routes", () => {
  render(
    <MemoryRouter initialEntries={["/admin/documents/123/versions/456"]}>
      <AdminSidebar />
    </MemoryRouter>
  );

  expect(screen.getByRole("link", { name: "ドキュメント" })).toHaveClass("active");
  expect(screen.getByRole("link", { name: "承認" })).not.toHaveClass("active");
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
  expect(await screen.findByRole("heading", { name: "ドキュメント" })).toBeInTheDocument();
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

test("dashboard links to evaluations without showing the run action", async () => {
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

  expect(await screen.findByRole("heading", { name: "ダッシュボード" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "評価" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /直近評価/ })).toHaveAttribute("href", "/admin/evaluations");
  expect(screen.queryByRole("button", { name: "評価を実行" })).not.toBeInTheDocument();
});

test("keeps the existing admin evaluation run action on the evaluations page", async () => {
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
      if (url.includes("/api/v1/evaluations/runs")) {
        return jsonResponse({ data: [], meta: { pagination: { page: 1, page_size: 20, total: 0, has_next: false } } });
      }
      if (url.includes("/api/v1/evaluations/datasets")) {
        return jsonResponse({ data: [], meta: { pagination: { page: 1, page_size: 50, total: 0, has_next: false } } });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/evaluations");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "評価" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "評価を実行" })).toBeInTheDocument();
});

test("evaluation create form submits requested generation provider and model", async () => {
  const createPayloads: Array<Record<string, unknown>> = [];
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
      if (url.endsWith("/api/v1/evaluations/runs") && init?.method === "POST") {
        createPayloads.push(JSON.parse(String(init.body)));
        return jsonResponse({ data: { evaluation_run_id: 42, job_id: 142, status: "queued", strategies: ["dense"] } }, 202);
      }
      if (url.includes("/api/v1/evaluations/runs")) {
        return jsonResponse({ data: [], meta: { pagination: { page: 1, page_size: 20, total: 0, has_next: false } } });
      }
      if (url.includes("/api/v1/evaluations/datasets")) {
        return jsonResponse({ data: [], meta: { pagination: { page: 1, page_size: 50, total: 0, has_next: false } } });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/evaluations");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "評価" })).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "生成 provider" }), {
    target: { value: "openai" }
  });
  fireEvent.change(screen.getByRole("textbox", { name: "生成 model" }), {
    target: { value: "gpt-4.1-mini" }
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "llm_tool_orchestrator" }));
  fireEvent.click(screen.getByRole("button", { name: "評価を実行" }));

  await waitFor(() => expect(createPayloads).toHaveLength(1));
  expect(createPayloads[0]).toMatchObject({
    generation_provider: "openai",
    generation_model: "gpt-4.1-mini"
  });
  expect(JSON.stringify(createPayloads[0]).toLowerCase()).not.toContain("api_key");
});

test("evaluation create form blocks generation selection without answer strategy", async () => {
  const createPayloads: Array<Record<string, unknown>> = [];
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
      if (url.endsWith("/api/v1/evaluations/runs") && init?.method === "POST") {
        createPayloads.push(JSON.parse(String(init.body)));
        return jsonResponse({ data: { evaluation_run_id: 42, job_id: 142, status: "queued", strategies: ["dense"] } }, 202);
      }
      if (url.includes("/api/v1/evaluations/runs")) {
        return jsonResponse({ data: [], meta: { pagination: { page: 1, page_size: 20, total: 0, has_next: false } } });
      }
      if (url.includes("/api/v1/evaluations/datasets")) {
        return jsonResponse({ data: [], meta: { pagination: { page: 1, page_size: 50, total: 0, has_next: false } } });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/evaluations");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "評価" })).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "生成 model" }), {
    target: { value: "gpt-4.1-mini" }
  });

  expect(
    screen.getByText(/provider\/model 比較には llm_tool_orchestrator/)
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "評価を実行" })).toBeDisabled();
  expect(createPayloads).toHaveLength(0);
});

test("evaluation list opens comparison for two selected runs", async () => {
  const comparisonRequests: string[] = [];
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
      if (url.includes("/api/v1/evaluations/runs/compare")) {
        comparisonRequests.push(url);
        return jsonResponse({ data: evaluationComparisonPayload() });
      }
      if (url.includes("/api/v1/evaluations/runs")) {
        return jsonResponse({
          data: [evaluationRunSummary(10, "base_dataset"), evaluationRunSummary(11, "candidate_dataset")],
          meta: { pagination: { page: 1, page_size: 20, total: 2, has_next: false } }
        });
      }
      if (url.includes("/api/v1/evaluations/datasets")) {
        return jsonResponse({ data: [], meta: { pagination: { page: 1, page_size: 50, total: 0, has_next: false } } });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/evaluations");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "評価" })).toBeInTheDocument();
  fireEvent.click(await screen.findByLabelText("比較対象 run #10 を選択"));
  fireEvent.click(await screen.findByLabelText("比較対象 run #11 を選択"));
  fireEvent.click(screen.getByRole("button", { name: "比較" }));

  expect(await screen.findByRole("heading", { name: "評価 run 比較" })).toBeInTheDocument();
  await waitFor(() =>
    expect(comparisonRequests.some((url) => url.includes("base=10") && url.includes("candidate=11"))).toBe(true)
  );
});

test("evaluation comparison page shows direction colors and lower-is-better hints", async () => {
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
      if (url.endsWith("/api/v1/evaluations/metric-catalog")) {
        return jsonResponse({ data: evaluationMetricCatalogPayload() });
      }
      if (url.includes("/api/v1/evaluations/runs/compare")) {
        return jsonResponse({ data: evaluationComparisonPayload() });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/evaluations/compare?base=10&candidate=11");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "評価 run 比較" })).toBeInTheDocument();
  const generationTable = await screen.findByRole("table", { name: "generation 差分" });
  const costRow = within(generationTable).getByText("推定コスト").closest("tr");
  expect(costRow).toHaveClass("comparison-direction-improved");
  expect(within(costRow as HTMLElement).getByText("-$0.050000")).toBeInTheDocument();
  expect(within(costRow as HTMLElement).getByText("低いほど良い")).toBeInTheDocument();
  const tokenRow = within(generationTable).getByText("総トークン").closest("tr");
  expect(tokenRow).toHaveClass("comparison-direction-improved");
  expect(within(tokenRow as HTMLElement).getByText("-40")).toBeInTheDocument();
  expect(screen.getAllByText(/fake-rag-answer-v2/).length).toBeGreaterThan(0);

  const metricTable = await screen.findByRole("table", { name: "metric 差分" });
  const p95Row = within(metricTable).getByText("p95_latency").closest("tr");
  expect(p95Row).toHaveClass("comparison-direction-improved");
  expect(within(p95Row as HTMLElement).getByText("改善")).toBeInTheDocument();
  expect(within(p95Row as HTMLElement).getByText("性能")).toBeInTheDocument();
  expect(within(p95Row as HTMLElement).getByText("低いほど良い")).toBeInTheDocument();

  const recallRow = within(metricTable).getByText("recall_at_k").closest("tr");
  expect(recallRow).toHaveClass("comparison-direction-regressed");
  expect(within(recallRow as HTMLElement).getByText("悪化")).toBeInTheDocument();
  expect(within(recallRow as HTMLElement).getByText("検索品質")).toBeInTheDocument();

  const caseTable = screen.getByRole("table", { name: "case 差分" });
  const regressedCaseRow = within(caseTable).getByText("shared_pass").closest("tr");
  expect(regressedCaseRow).toHaveClass("comparison-direction-regressed");
  expect(within(regressedCaseRow as HTMLElement).getByText("回帰")).toBeInTheDocument();
  expect(within(caseTable).getByText("candidate_only")).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("raw question");
  expect(document.body).not.toHaveTextContent("token=secret");
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
  fireEvent.click(screen.getByRole("button", { name: "エクスポート" }));
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
      if (url.endsWith("/api/v1/evaluations/metric-catalog")) {
        return jsonResponse({ data: evaluationMetricCatalogPayload() });
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
            metric_summary: { recall_at_k: 0.75 },
            strategy_comparison: [
              {
                schema_version: "phase2.evaluation.v1",
                strategy_type: "agentic_router",
                metric_name: "recall_at_k",
                average: 0.75,
                p50: 0.75,
                p95: 0.75,
                count: 1,
                failed_count: 0,
                not_applicable_count: 0
              }
            ],
            strategy_metrics_summary_json: null,
            total_estimated_cost_usd: 0.123456,
            total_input_tokens: 100,
            total_output_tokens: 50,
            total_tokens: 150,
            avg_generation_latency_ms: 42,
            generation_providers: ["fake"],
            generation_models: ["fake-rag-answer"],
            requested_generation_provider: null,
            requested_generation_model: null,
            error_code: null,
            error_message: null,
            started_at: "2026-05-01T00:00:00Z",
            finished_at: "2026-05-01T00:00:01Z",
            created_at: "2026-05-01T00:00:00Z",
            updated_at: "2026-05-01T00:00:01Z",
            items: [
              {
                evaluation_run_item_id: 700,
                evaluation_case_id: null,
                retrieval_run_id: null,
                strategy_type: "agentic_router",
                status: "succeeded",
                faithfulness_score: 0.8,
                groundedness_score: 0.7,
                citation_coverage: 0.6,
                context_precision: 0.5,
                latency_ms: 120,
                generation_provider: "fake",
                generation_model: "fake-rag-answer",
                input_tokens: 100,
                output_tokens: 50,
                total_tokens: 150,
                estimated_cost_usd: 0.123456,
                generation_latency_ms: 42,
                latency_breakdown_json: null,
                metric_summary_json: null,
                error_code: null,
                error_message: null,
                case_id: null,
                case_key: "fixture_case",
                metrics: [
                  {
                    metric_name: "recall_at_k",
                    metric_score: 0.75,
                    metric_value: null,
                    metric_label: null,
                    details: null,
                    metric_detail_json: null,
                    strategy_type: "agentic_router"
                  },
                  {
                    metric_name: "mrr",
                    metric_score: 0.5,
                    metric_value: null,
                    metric_label: null,
                    details: null,
                    metric_detail_json: null,
                    strategy_type: "agentic_router"
                  }
                ]
              }
            ],
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

  expect(await screen.findByRole("heading", { name: "評価 #77" })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "recall_at_k の説明" }).length).toBeGreaterThan(1);
  expect(screen.getAllByText("$0.123456").length).toBeGreaterThan(0);
  expect(screen.getAllByText("fake-rag-answer").length).toBeGreaterThan(0);
  expect(screen.getAllByText("入力 100 / 出力 50 / 合計 150").length).toBeGreaterThan(0);
  const metricDetailList = document.querySelector(".metric-detail-list");
  expect(metricDetailList).toBeInTheDocument();
  expect(metricDetailList?.querySelectorAll(".metric-detail-item")).toHaveLength(2);
  expect(metricDetailList?.querySelectorAll(".metric-detail-group")).toHaveLength(1);
  expect(screen.getAllByText("検索品質").length).toBeGreaterThan(1);
  expect(screen.getByText(/失敗した評価 item/)).toBeInTheDocument();
  expect(screen.getByText(/strategy 期待値だけ/)).toBeInTheDocument();
  expect(await screen.findByRole("option", { name: "promoted_failures" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "archived_failures" })).not.toBeInTheDocument();
  fireEvent.change(await screen.findByLabelText("追加先 dataset"), {
    target: { value: "42" }
  });
  fireEvent.click(screen.getByRole("button", { name: "主要な失敗を選択" }));
  fireEvent.click(screen.getByRole("button", { name: "選択した失敗を追加" }));

  await waitFor(() => expect(promoteRequests.length).toBe(1));
  const body = JSON.parse(String(promoteRequests[0].body));
  expect(body.target_dataset_id).toBe(42);
  expect(body.promotion_keys).toEqual(["promotion-key-retrieval", "promotion-key-known"]);
  expect(await screen.findByText("1 件を追加し、0 件を skip しました。")).toBeInTheDocument();
});

test("evaluation detail can create a target dataset and promote selected failures", async () => {
  const createDatasetRequests: RequestInit[] = [];
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
      if (url.endsWith("/api/v1/evaluations/datasets") && init?.method === "POST") {
        createDatasetRequests.push(init);
        return jsonResponse({
          data: {
            evaluation_dataset_id: 99,
            dataset_name: "failure_promoted_run_78",
            description: "Failure promotion target for evaluation run #78.",
            version: "v1",
            source_type: "feedback_promoted",
            status: "active",
            metadata_json: { source: "failure_promotion_target", source_evaluation_run_id: 78 },
            case_count: 0,
            created_by: 1,
            created_at: "2026-05-01T00:00:00Z",
            updated_at: "2026-05-01T00:00:00Z"
          }
        });
      }
      if (url.includes("/api/v1/evaluations/datasets")) {
        return jsonResponse({
          data: [],
          meta: { pagination: { page: 1, page_size: 100, total: 0, has_next: false } }
        });
      }
      if (url.endsWith("/api/v1/evaluations/runs/78/promote-failures")) {
        promoteRequests.push(init ?? {});
        return jsonResponse({
          data: {
            evaluation_run_id: 78,
            target_dataset_id: 99,
            created_count: 1,
            skipped_count: 0,
            items: [
              {
                promotion_key: "promotion-key-no-context",
                failure_type: "no_context",
                strategy_type: "hybrid",
                evaluation_run_item_id: 780,
                evaluation_case_id: null,
                promoted_case_id: 901,
                case_key: "failure_case",
                result_code: "created"
              }
            ]
          }
        });
      }
      if (url.endsWith("/api/v1/evaluations/runs/78")) {
        return jsonResponse({
          data: {
            evaluation_run_id: 78,
            job_id: 89,
            evaluation_dataset_id: null,
            dataset_name: "fixture_only_run",
            strategy_type: "hybrid",
            strategies: ["hybrid"],
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
                evaluation_run_id: 78,
                evaluation_run_item_id: 780,
                evaluation_case_id: null,
                case_key: "fixture_case",
                question_hash: "c".repeat(64),
                strategy_type: "hybrid",
                failure_type: "no_context",
                severity: "high",
                failure_reason_codes: ["no_context"],
                metric_snapshot: {},
                recommended_tags: ["failure_promoted"],
                promotion_key: "promotion-key-no-context"
              }
            ]
          }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/evaluations/78");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "評価 #78" })).toBeInTheDocument();
  expect(await screen.findByText(/有効な追加先 dataset がありません/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "選択した失敗を追加" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "追加先を作成" }));

  await waitFor(() => expect(createDatasetRequests.length).toBe(1));
  const createBody = JSON.parse(String(createDatasetRequests[0].body));
  expect(createBody.dataset_name).toBe("failure_promoted_run_78");
  expect(await screen.findByRole("option", { name: "failure_promoted_run_78" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("checkbox", { name: /失敗候補 no_context/ }));
  fireEvent.click(screen.getByRole("button", { name: "選択した失敗を追加" }));

  await waitFor(() => expect(promoteRequests.length).toBe(1));
  const promoteBody = JSON.parse(String(promoteRequests[0].body));
  expect(promoteBody.target_dataset_id).toBe(99);
  expect(promoteBody.promotion_keys).toEqual(["promotion-key-no-context"]);
  expect(await screen.findByText("1 件を追加し、0 件を skip しました。")).toBeInTheDocument();
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
      if (url.includes("/api/v1/rag/retrieval-runs?")) {
        return jsonResponse({ data: { items: [] } });
      }
      if (url.endsWith("/api/v1/rag/retrieval-runs/600/graph-trace")) {
        return jsonResponse({
          data: {
            schema_version: "phase3.graph_citation_trace.v1",
            retrieval_run_id: 600,
            graph_path_count: 1,
            valid_path_count: 1,
            citable_path_count: 1,
            excluded_path_count: 0,
            citation_source_count: 1,
            coverage: {
              path_count: 1,
              valid_path_count: 1,
              citable_path_count: 1,
              excluded_path_count: 0,
              source_chunk_count: 1,
              resolved_source_chunk_count: 1,
              citable_source_chunk_count: 1,
              citation_source_count: 1,
              source_chunk_coverage_ratio: 1,
              citation_coverage_ratio: 1,
              reason_codes: []
            },
            paths: [
              {
                graph_retrieval_path_id: 700,
                path_id: "gp_safe_1",
                provider: "postgres",
                validation_status: "valid",
                reason_codes: [],
                safe_metadata: { validation_status: "valid" },
                source_chunk_ids: [300],
                depth: 1,
                path_score: 0.91,
                safe_entity_labels: ["FastAPI", "PostgreSQL"],
                relation_types: ["uses"],
                node_refs: [
                  {
                    provider: "postgres",
                    node_id: "1",
                    entity_id: 1,
                    safe_label: "FastAPI",
                    entity_type: "technology"
                  }
                ],
                relation_refs: [
                  {
                    provider: "postgres",
                    relation_id: "10",
                    source_node_id: "1",
                    target_node_id: "2",
                    relation_type: "uses",
                    safe_label: "uses"
                  }
                ],
                source_mappings: [
                  {
                    source_chunk_id: 300,
                    document_chunk_id: 300,
                    retrieval_run_item_id: 900,
                    selected_flag: true,
                    old_version_flag: false,
                    citation_ids: [1000],
                    local_citation_ids: [1]
                  }
                ],
                raw_evidence_text: "raw graph evidence must not appear"
              }
            ],
            raw_prompt: "raw prompt must not appear"
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
              context_budget_json: {
                schema_version: "phase2.context_budget.v1",
                enabled: true,
                budget: {
                  max_context_tokens: 6000,
                  reserve_answer_tokens: 1000,
                  max_context_items: 12,
                  max_tokens_per_item: 1200,
                  min_citation_candidates: 1,
                  token_estimator: "heuristic",
                  preserve_source_diversity: true,
                  drop_low_score_first: true
                },
                usage: {
                  estimated_prompt_tokens: 4,
                  estimated_context_tokens: 80,
                  estimated_total_input_tokens: 84,
                  reserve_answer_tokens: 1000,
                  remaining_context_tokens: 5920,
                  budget_exhausted: false
                },
                items: {
                  candidate_count: 2,
                  selected_count: 1,
                  dropped_count: 1,
                  citation_candidate_count: 2,
                  source_count: 1
                },
                drop_reasons: { max_items_exceeded: 1 },
                sources: {
                  source_count: 1,
                  by_source: [
                    {
                      source_group_key: "logical_document:10",
                      source_label: "phase2.md",
                      candidate_count: 2,
                      selected_count: 1,
                      dropped_count: 1,
                      estimated_tokens: 80
                    }
                  ]
                },
                selected_item_refs: [
                  {
                    retrieval_run_item_id: 900,
                    document_chunk_id: 300,
                    source_label: "phase2.md",
                    rank: 1,
                    estimated_tokens: 80,
                    char_count: 320,
                    reason: "high_score"
                  }
                ],
                dropped_item_refs: [
                  {
                    retrieval_run_item_id: 901,
                    document_chunk_id: 301,
                    source_label: "phase2-budget.md",
                    rank: 2,
                    estimated_tokens: 90,
                    char_count: 360,
                    drop_reason: "max_items_exceeded"
                  }
                ],
                raw_prompt: "raw prompt must not appear",
                full_context: "full context must not appear"
              },
              context_compression_json: {
                schema_version: "phase2.context_compression.v1",
                enabled: true,
                method: "deterministic_evidence_pack",
                policy: {
                  max_items: 12,
                  max_items_per_source: 4,
                  max_chars_per_item: 1200,
                  max_total_chars: 6000,
                  near_duplicate_threshold: 0.85,
                  preserve_citation_candidates: true,
                  group_by_source: true
                },
                input: {
                  candidate_context_items: 2,
                  selected_context_items: 1,
                  input_estimated_tokens: 80,
                  input_char_count: 320
                },
                output: {
                  evidence_group_count: 1,
                  evidence_item_count: 1,
                  output_estimated_tokens: 60,
                  output_char_count: 240,
                  compression_ratio: 0.75,
                  citation_candidate_count: 1
                },
                drops: { near_duplicate_removed: 1 },
                evidence_groups: [
                  {
                    source_group_key: "logical_document:10",
                    source_label: "phase2.md",
                    item_count: 1,
                    selected_item_count: 1,
                    estimated_tokens: 60,
                    top_score: 0.73,
                    evidence_item_refs: ["e1"]
                  }
                ],
                evidence_item_refs: [
                  {
                    evidence_item_id: "e1",
                    retrieval_run_item_id: 900,
                    document_chunk_id: 300,
                    local_citation_id: 1,
                    source_label: "phase2.md",
                    rank: 1,
                    source_group_key: "logical_document:10",
                    evidence_text_hash: "a".repeat(64),
                    original_char_count: 320,
                    output_char_count: 240,
                    estimated_tokens: 60,
                    citation_candidate: true,
                    compression_method: "bounded_excerpt",
                    compression_reason: "bounded_excerpt",
                    evidence_text_for_generation: "raw evidence text must not appear"
                  }
                ],
                dropped_item_refs: [
                  {
                    retrieval_run_item_id: 901,
                    document_chunk_id: 301,
                    source_label: "phase2-budget.md",
                    rank: 2,
                    estimated_tokens: 90,
                    original_char_count: 360,
                    drop_reason: "near_duplicate_removed"
                  }
                ],
                raw_prompt: "raw prompt must not appear",
                full_context: "full context must not appear"
              },
              tool_result_compression_json: {
                schema_version: "phase2.tool_result_compression.v1",
                enabled: true,
                budget: {
                  max_items_per_tool: 8,
                  max_total_items_per_turn: 20,
                  max_snippet_chars: 500,
                  max_tokens_per_tool: 1200,
                  max_total_tool_result_tokens: 3000,
                  token_estimator: "heuristic",
                  drop_low_score_first: true,
                  group_by_source: true,
                  reject_oversized_output: true
                },
                summary: {
                  tool_call_count: 2,
                  search_tool_call_count: 1,
                  original_item_count: 3,
                  output_item_count: 1,
                  dropped_item_count: 2,
                  estimated_tokens_before: 400,
                  estimated_tokens_after: 80,
                  compression_ratio: 0.2,
                  budget_exhausted: false,
                  repeated_result_count: 0,
                  oversized_rejected_count: 0
                },
                drop_reasons: { max_items_limit: 2 },
                by_tool: [
                  {
                    tool_call_id: "tc_1",
                    tool_name: "dense_search",
                    status: "succeeded",
                    original_item_count: 3,
                    output_item_count: 1,
                    dropped_item_count: 2,
                    estimated_tokens_before: 400,
                    estimated_tokens_after: 80,
                    compression_ratio: 0.2,
                    drop_reasons: { max_items_limit: 2 },
                    compression_methods: { max_chars_per_snippet: 1 },
                    budget_exhausted: false,
                    repeated_result: false,
                    oversized_rejected: false
                  }
                ],
                item_refs: [
                  {
                    tool_call_id: "tc_1",
                    tool_name: "dense_search",
                    retrieval_run_item_id: 900,
                    document_chunk_id: 300,
                    source_label: "phase2.md",
                    rank: 1,
                    retrieval_score: 0.73,
                    citation_candidate: true,
                    snippet_hash: "b".repeat(64),
                    original_char_count: 320,
                    snippet_char_count: 240,
                    estimated_tokens: 80,
                    source_group_key: "logical_document:10",
                    compression_method: "max_chars_per_snippet",
                    snippet: "raw tool snippet must not appear"
                  }
                ],
                dropped_item_refs: [],
                raw_tool_payload: "raw tool payload must not appear",
                raw_prompt: "raw prompt must not appear"
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

  expect(await screen.findByRole("heading", { name: "検索デバッグ" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "dense" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "sparse" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "hybrid" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "graph" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "graph" })).toBeDisabled();
  expect(screen.getByRole("option", { name: "agentic_router" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "multi_query_hybrid" })).toBeDisabled();

  fireEvent.change(screen.getByLabelText("検索クエリ"), { target: { value: "hybrid retrieval" } });
  fireEvent.change(screen.getByLabelText("検索方式"), { target: { value: "hybrid" } });
  fireEvent.click(screen.getByRole("button", { name: "検索を実行" }));

  await waitFor(() => expect(searchRequests.length).toBe(1));
  expect(JSON.parse(String(searchRequests[0].body)).strategy).toBe("hybrid");
  expect((await screen.findAllByText("#600")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("dense_sparse_single_query")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("comparison")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("multi_query_hybrid")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/planned_only/)).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/file_extension/)).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/explicit_strategy_hybrid/)).length).toBeGreaterThan(0);
  expect(await screen.findByText("42 ms")).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Context Budget（文脈予算）" })).toBeInTheDocument();
  expect((await screen.findAllByText("6000")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("80")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("max_items_exceeded")).length).toBeGreaterThan(0);
  expect(await screen.findByRole("heading", { name: "Evidence Pack（根拠パック）" })).toBeInTheDocument();
  expect((await screen.findAllByText("deterministic_evidence_pack")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("near_duplicate_removed")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("bounded_excerpt")).length).toBeGreaterThan(0);
  expect(await screen.findByRole("heading", { name: "Tool Result Compression（tool 結果圧縮）" })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "graph trace" })).toBeInTheDocument();
  expect((await screen.findAllByText("gp_safe_1")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("source_chunk_coverage")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("dense_search")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("max_chars_per_snippet")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("phase2-budget.md")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("0.730")).length).toBeGreaterThan(0);
  expect(await screen.findByText("hybrid retrieval safe snippet")).toBeInTheDocument();
  expect(await screen.findByText("recall_at_k")).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("raw prompt must not appear");
  expect(document.body).not.toHaveTextContent("full context must not appear");
  expect(document.body).not.toHaveTextContent("raw chunk text must not appear");
  expect(document.body).not.toHaveTextContent("raw evidence text must not appear");
  expect(document.body).not.toHaveTextContent("raw graph evidence must not appear");
  expect(document.body).not.toHaveTextContent("raw tool snippet must not appear");
  expect(document.body).not.toHaveTextContent("raw tool payload must not appear");
  expect(document.body).not.toHaveTextContent("OPENAI_API_KEY");
  expect(document.body).not.toHaveTextContent("sk-secret");
});

test("retrieval debug loads run history and refreshes selected trace", async () => {
  let historyRequests = 0;
  let detailRequests = 0;
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
      if (url.includes("/api/v1/rag/retrieval-runs?")) {
        historyRequests += 1;
        return jsonResponse({
          data: {
            items: [
              {
                retrieval_run_id: 601,
                origin_type: "chat",
                chat_session_id: 11,
                request_message_id: 21,
                status: "succeeded",
                strategy_type: "llm_tool_orchestrator",
                error_code: null,
                query_hash: "d".repeat(64),
                top_k: 8,
                retrieval_score_summary: {
                  selected_count: 2,
                  fallback_used: true,
                  fallback_reason: "llm_tool_additional_search",
                  fallback_strategy: "hybrid",
                  retrieval_call_count: 2
                },
                query_plan_json: null,
                strategy_decision_json: {
                  selected_strategy: "hybrid",
                  execution_strategy: "hybrid",
                  fallback_used: false,
                  tools_used: ["dense_search", "hybrid_search"],
                  raw_prompt: "raw prompt must not appear"
                },
                latency_breakdown_json: { total_ms: 120 },
                retrieval_settings_json: { top_k: 8 },
                rerank_score_top1: null,
                answer_confidence: 0.8,
                groundedness_score: 1,
                confidence_label: "High",
                started_at: "2026-05-01T00:00:00Z",
                finished_at: "2026-05-01T00:00:01Z",
                created_at: "2026-05-01T00:00:00Z"
              }
            ]
          }
        });
      }
      if (url.endsWith("/api/v1/rag/retrieval-runs/601/graph-trace")) {
        return jsonResponse({
          data: {
            schema_version: "phase3.graph_citation_trace.v1",
            retrieval_run_id: 601,
            graph_path_count: 0,
            valid_path_count: 0,
            citable_path_count: 0,
            excluded_path_count: 0,
            citation_source_count: 0,
            coverage: {
              path_count: 0,
              valid_path_count: 0,
              citable_path_count: 0,
              excluded_path_count: 0,
              source_chunk_count: 0,
              resolved_source_chunk_count: 0,
              citable_source_chunk_count: 0,
              citation_source_count: 0,
              source_chunk_coverage_ratio: 1,
              citation_coverage_ratio: 1,
              reason_codes: []
            },
            paths: []
          }
        });
      }
      if (url.endsWith("/api/v1/rag/retrieval-runs/601")) {
        detailRequests += 1;
        return jsonResponse({
          data: {
            retrieval_run: {
              retrieval_run_id: 601,
              origin_type: "chat",
              chat_session_id: 11,
              request_message_id: 21,
              status: "succeeded",
              strategy_type: "llm_tool_orchestrator",
              error_code: null,
              query_hash: "d".repeat(64),
              top_k: 8,
              retrieval_score_summary: {
                selected_count: 2,
                fallback_used: true,
                fallback_reason: "llm_tool_additional_search",
                fallback_strategy: "hybrid",
                retrieval_call_count: 2
              },
              query_plan_json: { query_mode: "llm_tool_orchestrator", raw_prompt: "raw prompt must not appear" },
              strategy_decision_json: {
                selected_strategy: "hybrid",
                execution_strategy: "hybrid",
                fallback_used: false,
                tools_used: ["dense_search", "hybrid_search"]
              },
              latency_breakdown_json: { total_ms: 120 },
              retrieval_settings_json: { top_k: 8 },
              rerank_score_top1: null,
              answer_confidence: 0.8,
              groundedness_score: 1,
              confidence_label: "High",
              started_at: "2026-05-01T00:00:00Z",
              finished_at: "2026-05-01T00:00:01Z",
              created_at: "2026-05-01T00:00:00Z"
            },
            items: []
          }
        });
      }
      if (url.includes("/api/v1/evaluations/runs")) {
        return jsonResponse({ data: [], meta: { pagination: { page: 1, page_size: 5, total: 0, has_next: false } } });
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

  expect(await screen.findByRole("heading", { name: "最近の検索 run" })).toBeInTheDocument();
  expect((await screen.findAllByText("#601")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("llm_tool_orchestrator")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText("llm_tool_additional_search")).length).toBeGreaterThan(0);
  expect(await screen.findByText(/bounded retrieval tool/)).toBeInTheDocument();
  expect((await screen.findAllByText("tools_used")).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/dense_search/)).length).toBeGreaterThan(0);
  expect((await screen.findAllByText(/hybrid_search/)).length).toBeGreaterThan(0);
  expect(await screen.findByText("120 ms")).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("raw prompt must not appear");
  await waitFor(() => expect(detailRequests).toBeGreaterThanOrEqual(1));

  fireEvent.click(screen.getByRole("button", { name: "trace を更新" }));

  await waitFor(() => expect(historyRequests).toBeGreaterThanOrEqual(2));
  await waitFor(() => expect(detailRequests).toBeGreaterThanOrEqual(2));
});

test("document list renders filters, 状態es and safe escaped text", async () => {
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

  expect(await screen.findByRole("heading", { name: "ドキュメント" })).toBeInTheDocument();
  expect(screen.getByLabelText("状態")).toBeInTheDocument();
  expect(screen.getByText("承認待ち")).toBeInTheDocument();
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

  expect(await screen.findByRole("heading", { name: "版の比較" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "版の比較" })).toHaveAttribute("href", "#version-compare");
  expect(screen.getByText("版ごとのメタデータと短い出典プレビューだけを比較します。本文全体は表示しません。")).toBeInTheDocument();
  expect(await screen.findByText("比較する版を選び、「比較する」を押すと差分を読み込みます。")).toBeInTheDocument();
  expect(compareRequests).toHaveLength(0);
  const compareButton = screen.getByRole("button", { name: "比較する" });
  await waitFor(() => expect(compareButton).not.toBeDisabled());
  fireEvent.click(compareButton);
  await waitFor(() => expect(compareRequests).toHaveLength(1));
  expect(await screen.findByText("New bounded preview")).toBeInTheDocument();
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

  fireEvent.click(await screen.findByRole("button", { name: "次へ" }));
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

  fireEvent.click(await screen.findByRole("button", { name: "次へ" }));
  await waitFor(() => expect(reviewRequests.some((url) => url.includes("page=2"))).toBe(true));
});

test("upload form validates extension before reading or sending アップロードファイル", async () => {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <DocumentUploadForm />
    </QueryClientProvider>
  );

  fireEvent.change(screen.getByLabelText("タイトル"), { target: { value: "Unsafe アップロードファイル" } });
  fireEvent.change(screen.getByLabelText("アップロードファイル"), {
    target: { files: [new File(["payload"], "payload.exe", { type: "application/octet-stream" })] }
  });
  fireEvent.click(screen.getByRole("button", { name: "アップロード" }));

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

  fireEvent.change(screen.getByLabelText("タイトル"), { target: { value: "Guide" } });
  fireEvent.change(screen.getByLabelText("アップロードファイル"), {
    target: { files: [new File(["payload"], "guide.txt", { type: "text/plain" })] }
  });
  fireEvent.click(screen.getByRole("button", { name: "アップロード" }));

  expect(await screen.findByText(/ドキュメント #1000/)).toHaveTextContent("ジョブ #300");
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

test("succeeded job detail does not show failure message", async () => {
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
      if (url.endsWith("/api/v1/jobs/27")) {
        return jsonResponse({
          data: {
            job_id: 27,
            job_type: "evaluation_run",
            status: "succeeded",
            priority: 100,
            target_type: "evaluation_run",
            target_id: 3,
            retry_of_job_id: null,
            retry_count: 0,
            created_by: 1,
            started_at: "2026-05-30T23:28:00Z",
            finished_at: "2026-05-30T23:28:00Z",
            created_at: "2026-05-30T23:28:00Z",
            updated_at: "2026-05-30T23:28:00Z",
            locked_at: null,
            lease_expires_at: null,
            result_json: null,
            source_job_id: null,
            active_retry_job_id: null,
            error_code: null,
            error_message: null,
            payload_view: { payload: { evaluation_run_id: 3 }, payload_redacted: true }
          }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/jobs/27");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "ジョブ #27" })).toBeInTheDocument();
  expect(screen.getByText("成功")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "エラー" })).not.toBeInTheDocument();
  expect(screen.queryByText("Job failed.")).not.toBeInTheDocument();
});

test("failed job detail shows safe failure diagnostics", async () => {
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
      if (url.endsWith("/api/v1/jobs/28")) {
        return jsonResponse({
          data: {
            job_id: 28,
            job_type: "evaluation_run",
            status: "failed",
            priority: 100,
            target_type: "evaluation_run",
            target_id: 3,
            retry_of_job_id: null,
            retry_count: 0,
            created_by: 1,
            started_at: "2026-05-30T23:28:00Z",
            finished_at: "2026-05-30T23:29:00Z",
            created_at: "2026-05-30T23:28:00Z",
            updated_at: "2026-05-30T23:29:00Z",
            locked_at: null,
            lease_expires_at: null,
            result_json: null,
            source_job_id: null,
            active_retry_job_id: null,
            error_code: "evaluation_execution_failed",
            error_message: "Evaluation runner timed out after 30s",
            payload_view: { payload: { evaluation_run_id: 3 }, payload_redacted: true }
          }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/jobs/28");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "ジョブ #28" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "失敗理由" })).toBeInTheDocument();
  expect(screen.getAllByText("evaluation_execution_failed").length).toBeGreaterThan(0);
  expect(screen.getAllByText("Evaluation runner timed out after 30s").length).toBeGreaterThan(0);
  expect(screen.getByText("診断ログを表示")).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("session-token");
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

  fireEvent.click(await screen.findByRole("button", { name: "再実行" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/jobs/300/retry"), expect.any(Object)));
  expect(await screen.findByRole("button", { name: "再実行待ち" })).toBeDisabled();
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
              error_code: "stale_error",
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

  fireEvent.click(await screen.findByRole("button", { name: "次へ" }));
  await waitFor(() => expect(jobRequests.some((url) => url.includes("page=2"))).toBe(true));
  expect(screen.queryByText("stale_error")).not.toBeInTheDocument();
});

test("local evaluation form submits NVIDIA catalog and custom model IDs", async () => {
  vi.stubEnv("VITE_ENABLE_NVIDIA_API", "true");
  const createPayloads: Array<Record<string, unknown>> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({
          data: {
            user_id: 1,
            email: "admin@example.com",
            display_name: "Admin",
            role: "admin"
          }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "session-token" } });
      }
      if (url.endsWith("/api/v1/evaluations/runs") && init?.method === "POST") {
        createPayloads.push(JSON.parse(String(init.body)));
        return jsonResponse(
          { data: { evaluation_run_id: 42, job_id: 142, status: "queued", strategies: ["dense"] } },
          202
        );
      }
      if (url.includes("/api/v1/evaluations/runs")) {
        return jsonResponse({
          data: [],
          meta: { pagination: { page: 1, page_size: 20, total: 0, has_next: false } }
        });
      }
      if (url.includes("/api/v1/evaluations/datasets")) {
        return jsonResponse({
          data: [],
          meta: { pagination: { page: 1, page_size: 50, total: 0, has_next: false } }
        });
      }
      return jsonResponse({ data: [] });
    })
  );
  window.history.pushState({}, "", "/admin/evaluations");

  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );

  expect(await screen.findByRole("heading", { name: "\u8a55\u4fa1" })).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "\u751f\u6210 provider" }), {
    target: { value: "nvidia" }
  });
  expect(screen.getByRole("combobox", { name: "\u751f\u6210 model" })).toHaveValue(
    "nvidia/llama-3.3-nemotron-super-49b-v1.5"
  );
  expect(screen.getByRole("status")).toHaveTextContent("NVIDIA");
  expect(
    document.querySelector(
      'datalist#nvidia-generation-models option[value="nvidia/llama-3.3-nemotron-super-49b-v1.5"]'
    )
  ).not.toBeNull();
  const customModel = "mistralai/mixtral-8x22b-instruct-v0.1";
  fireEvent.change(screen.getByRole("combobox", { name: "\u751f\u6210 model" }), {
    target: { value: customModel }
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "llm_tool_orchestrator" }));
  fireEvent.click(screen.getByRole("button", { name: "\u8a55\u4fa1\u3092\u5b9f\u884c" }));

  await waitFor(() => expect(createPayloads).toHaveLength(1));
  expect(createPayloads[0]).toMatchObject({
    generation_provider: "nvidia",
    generation_model: customModel
  });
});
