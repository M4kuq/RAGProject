import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { resetApiClientStateForTests } from "../../lib/apiClient";
import {
  EvaluationDatasetImportForm,
  MAX_EVALUATION_DATASET_IMPORT_BYTES
} from "./EvaluationDatasetImportForm";

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

function renderForm() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  render(
    <QueryClientProvider client={client}>
      <EvaluationDatasetImportForm />
    </QueryClientProvider>
  );
}

function jsonFile(name: string, content: string): File {
  const file = new File([content], name, { type: "application/json" });
  Object.defineProperty(file, "text", {
    configurable: true,
    value: () => Promise.resolve(content)
  });
  return file;
}

beforeEach(() => {
  resetApiClientStateForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("JSONをread-only検証してからCSRF付きでインポートする", async () => {
  const importRequests: Array<Record<string, unknown>> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/evaluations/datasets/validate") && init?.method === "POST") {
        return jsonResponse({
          data: {
            schema_version: "phase3.evaluation_dataset_validation.v1",
            valid: true,
            manifest_schema_version: "phase2.evaluation_dataset.v1",
            dataset_name: "uploaded_manual_v1",
            version: "v1",
            content_fingerprint: "a".repeat(64),
            corpus_fingerprint: null,
            serialized_size_bytes: 1024,
            composition: {
              case_count: 1,
              source_count: 0,
              fact_count: 0,
              answerable_count: 1,
              unanswerable_count: 0,
              language_ja_count: 0,
              language_en_count: 1,
              single_hop_count: 0,
              multi_hop_count: 0,
              prompt_injection_count: 0
            },
            warnings: ["v1 datasets use the shared legacy corpus"]
          }
        });
      }
      if (url.endsWith("/api/v1/auth/csrf")) {
        return jsonResponse({ data: { csrf_token: "dataset-import-csrf" } });
      }
      if (url.endsWith("/api/v1/evaluations/datasets/import") && init?.method === "POST") {
        importRequests.push(JSON.parse(String(init.body)));
        return jsonResponse({
          data: {
            evaluation_dataset_id: 81,
            dataset_name: "uploaded_manual_v1",
            version: "v1",
            content_fingerprint: "a".repeat(64),
            corpus_fingerprint: null,
            case_count: 1,
            imported_case_count: 1,
            result_code: "created"
          }
        });
      }
      return jsonResponse({ data: null }, 404);
    })
  );

  const manifest = {
    schema_version: "phase2.evaluation_dataset.v1",
    dataset: {
      dataset_name: "uploaded_manual_v1",
      description: null,
      version: "v1",
      source_type: "imported",
      status: "active",
      metadata_json: null
    },
    cases: [
      {
        case_key: "uploaded_001",
        question: "Safe evaluation question",
        expected_answer: "Safe expected answer",
        expected_keywords: [],
        expected_document_ids: [],
        expected_chunk_ids: [],
        required_citation: true,
        tags: ["manual"],
        metadata_json: { answerable: true },
        status: "active"
      }
    ],
    metric_specs: []
  };

  renderForm();
  fireEvent.change(screen.getByLabelText("評価データセットJSON"), {
    target: { files: [jsonFile("dataset.json", JSON.stringify(manifest))] }
  });

  expect(await screen.findByRole("heading", { name: "JSON検証プレビュー" })).toBeInTheDocument();
  expect(importRequests).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "JSONをインポート" }));

  await waitFor(() => expect(importRequests).toHaveLength(1));
  expect(importRequests[0]).toMatchObject({
    schema_version: "phase2.evaluation_dataset.v1",
    dataset: { dataset_name: "uploaded_manual_v1" }
  });
  expect(
    await screen.findByText("dataset uploaded_manual_v1 v1 をインポートしました（1 cases）。")
  ).toBeInTheDocument();
});

test("不正JSONと2MB超過ファイルはAPIへ送信しない", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  renderForm();

  fireEvent.change(screen.getByLabelText("評価データセットJSON"), {
    target: { files: [jsonFile("broken.json", "{not-json")] }
  });
  expect(await screen.findByText("有効なJSONファイルではありません。")).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();

  const oversized = jsonFile("large.json", "{}");
  Object.defineProperty(oversized, "size", {
    configurable: true,
    value: MAX_EVALUATION_DATASET_IMPORT_BYTES + 1
  });
  fireEvent.change(screen.getByLabelText("評価データセットJSON"), {
    target: { files: [oversized] }
  });
  expect(await screen.findByText("JSONファイルは2MB以下にしてください。")).toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});
