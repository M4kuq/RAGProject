import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { MetricHelp, orderedMetricEntries } from "./MetricHelp";

describe("MetricHelp", () => {
  test("shows the raw metric name with Japanese explanation and direction", () => {
    render(<MetricHelp metricName="citation_coverage" />);

    expect(screen.getByRole("button", { name: "citation_coverage の説明" })).toHaveTextContent("?");
    expect(screen.getByText("citation_coverage")).toBeInTheDocument();
    expect(screen.getByText("回答に必要な引用が付いているかを見る指標です。")).toBeInTheDocument();
    expect(screen.getByText("高いほどよい指標です。")).toHaveClass("metric-help-direction");
  });

  test("orders metrics by display priority before falling back to name order", () => {
    expect(
      orderedMetricEntries([
        ["z_custom", 1],
        ["citation_coverage", 2],
        ["faithfulness", 3],
        ["a_custom", 4],
        ["no_context_rate", 5]
      ]).map(([name]) => name)
    ).toEqual(["faithfulness", "citation_coverage", "no_context_rate", "a_custom", "z_custom"]);
  });
});
