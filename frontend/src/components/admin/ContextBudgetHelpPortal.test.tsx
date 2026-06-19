import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { ContextBudgetHelpPortal } from "./ContextBudgetHelpPortal";

describe("ContextBudgetHelpPortal", () => {
  afterEach(() => {
    cleanup();
    document.body.innerHTML = "";
  });

  test("adds the shared help tooltip to the Context Budget heading", async () => {
    render(
      <main className="retrieval-debug-page">
        <section className="admin-section">
          <h2>Context Budget（文脈予算）</h2>
          <dl />
        </section>
        <ContextBudgetHelpPortal />
      </main>
    );

    expect(screen.getByRole("heading", { name: "Context Budget（文脈予算）" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Context Budget の説明" })).toHaveTextContent("?");
    expect(
      screen.getByText(
        "回答生成に渡す文脈をトークン上限内に収めるため、検索結果から採用する根拠と除外する根拠を記録する仕組みです。"
      )
    ).toBeInTheDocument();
    expect(
      screen.getByText("大きいほど良い指標ではなく、十分な根拠を残しながら予算を超えていないかを確認します。")
    ).toHaveClass("metric-help-direction");
  });
});
