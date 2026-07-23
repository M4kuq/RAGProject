import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { EvaluationMetricCatalogItem } from "../../features/evaluations/evaluationTypes";
import { MetricHelp, orderedMetricEntries } from "./MetricHelp";

describe("MetricHelp", () => {
  test("shows the raw metric name with Japanese explanation and direction", () => {
    render(<MetricHelp metricName="citation_coverage" />);

    expect(screen.getByRole("button", { name: "citation_coverage の説明" })).toHaveTextContent("?");
    expect(screen.getByText("citation_coverage")).toBeInTheDocument();
    expect(
      screen.getByText("citation_presence と同じ値を返す後方互換用の指標です。")
    ).toBeInTheDocument();
    expect(screen.getByText("新しい比較では citation_presence を使用します。")).toHaveClass(
      "metric-help-direction"
    );
  });

  test.each([
    [
      "answer_completeness",
      "生成回答に必須の expected_answer_slots が含まれた割合です。"
    ],
    ["citation_presence", "引用必須の回答に安全な引用が1件以上あるかを示します。"],
    [
      "citation_correctness",
      "引用が設定済みの正解 chunk、document、keyword、answer signal に合う割合です。"
    ]
  ])("shows Metric V2 help for %s", (metricName, description) => {
    render(<MetricHelp metricName={metricName} />);

    expect(screen.getByRole("button", { name: `${metricName} の説明` })).toHaveTextContent("?");
    expect(screen.getByText(metricName)).toBeInTheDocument();
    expect(screen.getByText(description)).toBeInTheDocument();
  });

  test("uses the metric catalog explanation instead of the generic fallback", () => {
    const definition = {
      metric_name: "claim_faithfulness",
      category: "answer",
      display_name: "Claim Faithfulness（ローカルjudge）",
      description: "Technical description",
      plain_language_summary: "検索で得た根拠に裏付けられた主張の割合です。",
      higher_is_better: true,
      value_unit: "ratio",
      alias_of: null,
      importance: "primary",
      applicable_scopes: ["answer", "end_to_end"],
      primary_scopes: ["answer", "end_to_end"],
      display_priority: 4,
      method: "local_judge"
    } satisfies EvaluationMetricCatalogItem;

    render(<MetricHelp definition={definition} metricName="claim_faithfulness" />);

    expect(screen.getByText(definition.plain_language_summary)).toBeInTheDocument();
    expect(screen.getByText("高いほど良好です。")).toHaveClass("metric-help-direction");
    expect(screen.queryByText("この評価 run に記録された metric です。")).not.toBeInTheDocument();
  });

  test("orders metrics by display priority before falling back to name order", () => {
    expect(
      orderedMetricEntries([
        ["z_custom", 1],
        ["citation_coverage", 2],
        ["faithfulness", 3],
        ["a_custom", 4],
        ["no_context_rate", 5],
        ["answer_completeness", 6],
        ["citation_presence", 7],
        ["citation_correctness", 8]
      ]).map(([name]) => name)
    ).toEqual([
      "answer_completeness",
      "faithfulness",
      "citation_presence",
      "citation_correctness",
      "citation_coverage",
      "no_context_rate",
      "a_custom",
      "z_custom"
    ]);
  });

  test("flips the tooltip below the trigger on keyboard focus near the viewport top", async () => {
    render(<MetricHelp metricName="metric_summary" />);

    const button = screen.getByRole("button", { name: "metric_summary の説明" });
    const tooltip = screen.getByRole("tooltip", { hidden: true });
    button.getBoundingClientRect = () =>
      ({
        bottom: 22,
        height: 18,
        left: 100,
        right: 118,
        top: 4,
        width: 18,
        x: 100,
        y: 4,
        toJSON: () => ({})
      }) as DOMRect;
    tooltip.getBoundingClientRect = () =>
      ({
        bottom: 80,
        height: 80,
        left: 0,
        right: 200,
        top: 0,
        width: 200,
        x: 0,
        y: 0,
        toJSON: () => ({})
      }) as DOMRect;

    fireEvent.focus(button);

    await waitFor(() => expect(tooltip).toHaveClass("metric-help-tooltip-open"));
    expect(tooltip).toHaveClass("metric-help-tooltip-bottom");
    expect(tooltip).toHaveStyle({ top: "30px" });
  });
});
