import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { MetricHelp, orderedMetricEntries } from "./MetricHelp";

describe("MetricHelp", () => {
  test("shows the raw metric name with Japanese explanation and direction", () => {
    render(<MetricHelp metricName="citation_coverage" />);

    expect(screen.getByRole("button", { name: "citation_coverage の説明" })).toHaveTextContent("?");
    expect(screen.getByText("citation_coverage")).toBeInTheDocument();
    expect(screen.getByText("回答が必要な引用を満たしているかを示します。")).toBeInTheDocument();
    expect(screen.getByText("高いほど良好です。")).toHaveClass("metric-help-direction");
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
