import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { resetApiClientStateForTests } from "../../lib/apiClient";
import { HumanCalibrationPanel } from "./HumanCalibrationPanel";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  render(
    <QueryClientProvider client={client}>
      <HumanCalibrationPanel evaluationRunId={91} />
    </QueryClientProvider>
  );
}

const automaticDecision = {
  case_id: "gold_v2_001",
  rubric_version: "phase3.grounded_answer_judge.v1",
  required_facts_supported: "pass",
  citation_support: "pass",
  forbidden_claims_absent: "pass",
  abstention_correct: "not_applicable",
  prompt_injection_resisted: "not_applicable",
  confidence: 0.91,
  reason_codes: []
};

beforeEach(() => {
  resetApiClientStateForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("自動judgeを読取専用表示し、同じdimensionの手動校正だけを送信する", async () => {
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
        const payload = JSON.parse(String(init.body));
        return jsonResponse({
          data: {
            evaluation_human_calibration_id: 700,
            evaluation_run_item_id: 501,
            auxiliary_decision: automaticDecision,
            human_dimensions: payload.human_dimensions,
            human_calibration: {
              case_id: "gold_v2_001",
              rubric_version: "phase3.grounded_answer_judge.v1",
              auxiliary_pass: true,
              human_pass: false,
              disagreement_category: "auxiliary_false_positive",
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
                prompt_injection: false,
                judge_status: "succeeded",
                judge_failure_code: null,
                auxiliary_decision: automaticDecision,
                claim_faithfulness: 0.8,
                generated_answer: "RAW_ANSWER",
                citation_excerpts: [
                  { citation_id: 1, source_label: "source-a", snippet: "SAFE_CITATION" }
                ],
                required_facts: [{ fact_id: "fact-1", statement: "SAFE_REQUIRED_FACT" }],
                review_payload_available: true,
                review_payload_expires_at: "2026-08-16T00:00:00Z"
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

  expect(await screen.findByRole("heading", { name: "手動校正" })).toBeInTheDocument();
  expect(await screen.findByText("case: gold_v2_001")).toBeInTheDocument();
  expect(screen.getByText("RAW_ANSWER")).toBeInTheDocument();
  expect(screen.getByText(/SAFE_REQUIRED_FACT/)).toBeInTheDocument();
  expect(screen.getByText(/SAFE_CITATION/)).toBeInTheDocument();

  const automatic = screen.getByRole("group", { name: "自動judge判定（読み取り専用）" });
  expect(within(automatic).getByText("91.0%")).toBeInTheDocument();
  expect(screen.getByLabelText("回答拒否が正しい")).toBeDisabled();
  expect(screen.getByLabelText("prompt injectionを拒否した")).toBeDisabled();

  fireEvent.change(screen.getByLabelText("必須事実を満たす"), {
    target: { value: "fail" }
  });
  expect(screen.getByText(/自動judgeと判定が異なるため/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("不一致カテゴリ"), {
    target: { value: "auxiliary_false_positive" }
  });
  fireEvent.click(screen.getByRole("button", { name: "手動校正を保存" }));

  await waitFor(() => expect(putRequests).toHaveLength(1));
  const payload = JSON.parse(String(putRequests[0].body));
  expect(payload).toMatchObject({
    human_pass: false,
    human_dimensions: {
      required_facts_supported: "fail",
      citation_support: "pass",
      forbidden_claims_absent: "pass",
      abstention_correct: "not_applicable",
      prompt_injection_resisted: "not_applicable"
    },
    disagreement_category: "auxiliary_false_positive"
  });
  expect(payload).not.toHaveProperty("auxiliary_decision");
  const headers = new Headers(putRequests[0].headers);
  expect(headers.get("x-csrf-token")).toBe("calibration-csrf");
  expect(await screen.findByText("手動校正を保存しました。")).toBeInTheDocument();
});

test("対象が空でも手動校正UIを表示する", async () => {
  const fetchMock = vi.fn((url: string) => {
    if (url.endsWith("/api/v1/evaluations/runs/91/human-calibrations")) {
      return jsonResponse({
        data: {
          schema_version: "phase3.human_calibration.v1",
          evaluation_run_id: 91,
          eligible_count: 0,
          reviewed_count: 0,
          agreement_rate: null,
          targets: [],
          records: []
        }
      });
    }
    return jsonResponse({ data: null }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);

  renderPanel();

  expect(await screen.findByRole("heading", { name: "手動校正" })).toBeInTheDocument();
  expect(await screen.findByText("校正可能な評価itemはありません。")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/evaluations/runs/91/human-calibrations",
    expect.anything()
  );
});
