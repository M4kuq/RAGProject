import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { resetApiClientStateForTests } from "../../lib/apiClient";
import { HumanCalibrationPanel } from "./HumanCalibrationPanel";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderPanel(datasetName = "gold_answer_quality_v2") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  render(
    <QueryClientProvider client={client}>
      <HumanCalibrationPanel datasetName={datasetName} evaluationRunId={91} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  resetApiClientStateForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("Gold v2の安全な校正対象だけを表示し、CSRF付きで判定を保存する", async () => {
  const putRequests: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "calibration-csrf" } });
      }
      if (
        url.endsWith("/api/v1/evaluations/runs/91/human-calibrations/501") &&
        init?.method === "PUT"
      ) {
        putRequests.push(init);
        return jsonResponse({
          data: {
            evaluation_human_calibration_id: 700,
            evaluation_run_item_id: 501,
            auxiliary_decision: JSON.parse(String(init.body)).auxiliary_decision,
            human_calibration: {
              case_id: "gold_v2_001",
              rubric_version: "phase3.grounded_answer_judge.v1",
              auxiliary_pass: false,
              human_pass: true,
              disagreement_category: "auxiliary_false_negative",
              reason_codes: []
            },
            reviewed_by: 1,
            created_at: "2026-07-17T00:00:00Z",
            updated_at: "2026-07-17T00:00:00Z"
          }
        });
      }
      if (url.endsWith("/api/v1/evaluations/runs/91/human-calibrations")) {
        return jsonResponse({
          data: {
            schema_version: "phase3.human_calibration.v1",
            evaluation_run_id: 91,
            eligible_count: 1,
            reviewed_count: 0,
            agreement_rate: null,
            targets: [
              {
                evaluation_run_item_id: 501,
                case_id: "gold_v2_001",
                strategy_type: "dense",
                status: "succeeded",
                answerable: true,
                required_citation: true,
                prompt_injection: false
              }
            ],
            records: []
          }
        });
      }
      return jsonResponse({ data: null }, 404);
    })
  );

  renderPanel();

  expect(
    await screen.findByRole("heading", { name: "人間レビュー校正" })
  ).toBeInTheDocument();
  expect(await screen.findByText("case: gold_v2_001")).toBeInTheDocument();
  expect(screen.queryByText("RAW_QUESTION")).not.toBeInTheDocument();
  expect(screen.queryByText("RAW_ANSWER")).not.toBeInTheDocument();
  expect(screen.getByText(/補助判定の計算結果:/)).toHaveTextContent("Pass");

  fireEvent.change(screen.getByLabelText("必須事実を満たす"), {
    target: { value: "fail" }
  });
  expect(screen.getByText(/補助判定の計算結果:/)).toHaveTextContent("Fail");
  expect(screen.getByText(/判定が異なるため/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("不一致カテゴリ"), {
    target: { value: "auxiliary_false_negative" }
  });
  fireEvent.click(screen.getByRole("button", { name: "校正を保存" }));

  await waitFor(() => expect(putRequests).toHaveLength(1));
  const payload = JSON.parse(String(putRequests[0].body));
  expect(payload).toMatchObject({
    auxiliary_decision: {
      case_id: "gold_v2_001",
      required_facts_supported: "fail",
      citation_support: "pass",
      forbidden_claims_absent: "pass",
      abstention_correct: "not_applicable",
      prompt_injection_resisted: "not_applicable"
    },
    human_pass: true,
    disagreement_category: "auxiliary_false_negative"
  });
  const headers = new Headers(putRequests[0].headers);
  expect(headers.get("x-csrf-token")).toBe("calibration-csrf");
  expect(await screen.findByText("人間レビュー校正を保存しました。")).toBeInTheDocument();
});

test("Gold v2以外のrunでは校正UIもAPI呼び出しも行わない", () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);

  renderPanel("phase1_smoke");

  expect(screen.queryByRole("heading", { name: "人間レビュー校正" })).not.toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});
