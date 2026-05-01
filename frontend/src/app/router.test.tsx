import { render, screen } from "@testing-library/react";
import { AppProviders } from "./providers";
import { AppRouter } from "./router";

test("renders navigation", () => {
  render(
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );
  expect(screen.getByRole("link", { name: "Chat" })).toBeInTheDocument();
});
