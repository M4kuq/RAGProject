import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SettingsPage } from "../routes/SettingsPage";
import { THEME_STORAGE_KEY, ThemeProvider, useThemePreference } from "./theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function installMatchMedia(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const mediaQueryList = {
    get matches() {
      return matches;
    },
    media: DARK_QUERY,
    onchange: null,
    addEventListener: vi.fn((eventName: string, listener: (event: MediaQueryListEvent) => void) => {
      if (eventName === "change") {
        listeners.add(listener);
      }
    }),
    removeEventListener: vi.fn((eventName: string, listener: (event: MediaQueryListEvent) => void) => {
      if (eventName === "change") {
        listeners.delete(listener);
      }
    }),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn()
  } as unknown as MediaQueryList;

  vi.stubGlobal("matchMedia", vi.fn(() => mediaQueryList));

  return {
    setMatches(nextMatches: boolean) {
      matches = nextMatches;
      const event = { matches: nextMatches, media: DARK_QUERY } as MediaQueryListEvent;
      listeners.forEach((listener) => listener(event));
    }
  };
}

function ThemeProbe() {
  const { resolvedTheme, setThemePreference, systemTheme, themePreference } = useThemePreference();
  return (
    <div>
      <span data-testid="preference">{themePreference}</span>
      <span data-testid="resolved">{resolvedTheme}</span>
      <span data-testid="system">{systemTheme}</span>
      <button onClick={() => setThemePreference("system")} type="button">
        system
      </button>
      <button onClick={() => setThemePreference("light")} type="button">
        light
      </button>
      <button onClick={() => setThemePreference("dark")} type="button">
        dark
      </button>
    </div>
  );
}

afterEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.style.colorScheme = "";
  vi.unstubAllGlobals();
});

test("uses system theme by default and applies it to html", () => {
  installMatchMedia(true);

  render(
    <ThemeProvider>
      <ThemeProbe />
    </ThemeProvider>
  );

  expect(screen.getByTestId("preference")).toHaveTextContent("system");
  expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
  expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
});

test("persists an explicit light preference over system changes", () => {
  const system = installMatchMedia(true);

  render(
    <ThemeProvider>
      <ThemeProbe />
    </ThemeProvider>
  );

  fireEvent.click(screen.getByRole("button", { name: "light" }));
  expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  expect(screen.getByTestId("resolved")).toHaveTextContent("light");
  expect(document.documentElement).toHaveAttribute("data-theme", "light");

  act(() => system.setMatches(false));

  expect(screen.getByTestId("system")).toHaveTextContent("light");
  expect(screen.getByTestId("resolved")).toHaveTextContent("light");
  expect(document.documentElement).toHaveAttribute("data-theme", "light");
});

test("stores system preference and resumes OS tracking", () => {
  const system = installMatchMedia(false);

  render(
    <ThemeProvider>
      <ThemeProbe />
    </ThemeProvider>
  );

  fireEvent.click(screen.getByRole("button", { name: "dark" }));
  expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  expect(document.documentElement).toHaveAttribute("data-theme", "dark");

  fireEvent.click(screen.getByRole("button", { name: "system" }));
  expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("system");
  expect(screen.getByTestId("resolved")).toHaveTextContent("light");
  expect(document.documentElement).toHaveAttribute("data-theme", "light");

  act(() => system.setMatches(true));

  expect(screen.getByTestId("system")).toHaveTextContent("dark");
  expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
  expect(document.documentElement).toHaveAttribute("data-theme", "dark");
});

test("settings page exposes the three theme choices", () => {
  installMatchMedia(false);

  render(
    <ThemeProvider>
      <SettingsPage />
    </ThemeProvider>
  );

  expect(screen.getByRole("heading", { name: "設定" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: /システム設定/ })).toBeChecked();
  expect(screen.getByRole("radio", { name: /ライト/ })).not.toBeChecked();
  expect(screen.getByRole("radio", { name: /ダーク/ })).not.toBeChecked();

  fireEvent.click(screen.getByRole("radio", { name: /ダーク/ }));

  expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  expect(document.documentElement).toHaveAttribute("data-theme", "dark");
});
