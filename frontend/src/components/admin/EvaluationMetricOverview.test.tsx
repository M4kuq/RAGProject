import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type {
  EvaluationMetricCatalog,
  EvaluationMetricCatalogItem,
  EvaluationMetricCategory,
  EvaluationScope
} from "../../features/evaluations/evaluationTypes";
import {
  EvaluationMetricOverview,
  formatMetricDelta,
  formatMetricValue,
  selectPrimaryMetrics
} from "./EvaluationMetricOverview";

function metric(
  metricName: string,
  category: EvaluationMetricCategory,
  displayName: string,
  displayPriority: number,
  primaryScopes: EvaluationScope[] = [],
  valueUnit: EvaluationMetricCatalogItem["value_unit"] = "ratio",
  higherIsBetter = true
): EvaluationMetricCatalogItem {
  const answerMetric = category === "answer" || category === "citation";
  return {
    metric_name: metricName,
    category,
    display_name: displayName,
    description: `${displayName}の説明`,
    plain_language_summary: `${displayName}を平易に説明します。`,
    higher_is_better: higherIsBetter,
    value_unit: valueUnit,
    alias_of: null,
    importance: primaryScopes.length ? "primary" : "secondary",
    applicable_scopes: answerMetric
      ? ["answer", "end_to_end"]
      : ["retrieval", "end_to_end"],
    primary_scopes: primaryScopes,
    display_priority: displayPriority
  };
}

const catalog: EvaluationMetricCatalog = {
  schema_version: "phase3.evaluation_metric_taxonomy.v1",
  metrics: [
    metric("recall_at_k", "retrieval", "検索再現率", 0, ["retrieval"]),
    metric("mrr", "retrieval", "平均逆順位", 1, ["retrieval"]),
    metric("context_precision", "retrieval", "文脈適合率", 2, ["retrieval"]),
    metric("faithfulness", "answer", "忠実性", 4, ["answer", "end_to_end"]),
    metric(
      "answer_completeness",
      "answer",
      "回答完全性",
      5,
      ["answer", "end_to_end"]
    ),
    metric(
      "citation_correctness",
      "citation",
      "引用正確性",
      7,
      ["answer", "end_to_end"]
    ),
    metric("citation_presence", "citation", "引用の有無", 8),
    metric("p95_latency", "performance", "遅いケースの応答時間", 19, [], "ms", false)
  ]
};

describe("EvaluationMetricOverview", () => {
  test("selects three primary metrics for each evaluation scope", () => {
    expect(selectPrimaryMetrics(catalog, "retrieval").map((item) => item.metric_name)).toEqual([
      "recall_at_k",
      "mrr",
      "context_precision"
    ]);
    expect(selectPrimaryMetrics(catalog, "end_to_end").map((item) => item.metric_name)).toEqual([
      "faithfulness",
      "answer_completeness",
      "citation_correctness"
    ]);
  });

  test("shows non-technical primary cards and grouped metric details", () => {
    render(
      <EvaluationMetricOverview
        catalog={catalog}
        evaluationScope="end_to_end"
        metrics={{
          faithfulness: 0.71,
          answer_completeness: 0.64,
          citation_correctness: 0.72,
          citation_presence: 0.8,
          p95_latency: 245
        }}
      />
    );

    expect(screen.getByRole("heading", { name: /まず見る3項目/ })).toBeInTheDocument();
    expect(screen.getByText("評価スコープ: 検索＋回答")).toBeInTheDocument();
    expect(screen.getAllByText("重要")).toHaveLength(3);
    expect(screen.getAllByText("忠実性").length).toBeGreaterThan(1);
    expect(screen.getAllByText("71.0%")).toHaveLength(2);
    expect(screen.getByText(/合否判定ではありません/)).toBeInTheDocument();

    const performanceToggle = screen.getByRole("button", { name: /性能 1 指標/ });
    expect(performanceToggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(performanceToggle);
    expect(performanceToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("245 ms")).toBeInTheDocument();
    expect(screen.queryByText(/総合点/)).not.toBeInTheDocument();
  });

  test("explains missing answer metrics instead of showing a bare N/A", () => {
    render(
      <EvaluationMetricOverview
        catalog={catalog}
        evaluationScope="end_to_end"
        metrics={{ faithfulness: 0.71 }}
      />
    );

    expect(screen.getAllByText("未評価（回答未生成）")).toHaveLength(2);
  });

  test("marks the same primary metrics in the detailed category table", () => {
    render(
      <EvaluationMetricOverview
        catalog={catalog}
        evaluationScope="retrieval"
        metrics={{ recall_at_k: 0.82, mrr: 0.74, context_precision: 0.68 }}
      />
    );

    const retrievalSection = screen.getByText("検索品質").closest("section");
    expect(retrievalSection).not.toBeNull();
    expect(
      within(retrievalSection as HTMLElement).getAllByLabelText("まず見る指標")
    ).toHaveLength(3);
    expect(screen.getAllByText("82.0%").length).toBeGreaterThan(0);
  });
});

describe("metric formatting", () => {
  test("formats ratios, latency, counts, and deltas with units", () => {
    const ratio = catalog.metrics.find((item) => item.metric_name === "faithfulness");
    const latency = catalog.metrics.find((item) => item.metric_name === "p95_latency");
    const count = metric("retrieval_call_count_avg", "routing", "平均検索回数", 14, [], "count");

    expect(formatMetricValue(0.71, ratio)).toBe("71.0%");
    expect(formatMetricValue(245, latency)).toBe("245 ms");
    expect(formatMetricValue(2.3, count)).toBe("2.3 回");
    expect(formatMetricDelta(0.05, ratio)).toBe("+5.0pt");
    expect(formatMetricDelta(-35, latency)).toBe("-35 ms");
  });
});
