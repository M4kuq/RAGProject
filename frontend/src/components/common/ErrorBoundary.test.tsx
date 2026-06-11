import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom(): never {
  throw new Error("boom");
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("renders the fallback UI when a child throws", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);

  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>
  );

  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
});

test("renders children when no error is thrown", () => {
  render(
    <ErrorBoundary>
      <p>Healthy content</p>
    </ErrorBoundary>
  );

  expect(screen.getByText("Healthy content")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
