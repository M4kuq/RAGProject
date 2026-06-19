import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

export const THEME_STORAGE_KEY = "ragproject.themePreference";
export const THEME_OPTIONS = ["system", "light", "dark"] as const;
const DARK_SCHEME_QUERY = "(prefers-color-scheme: dark)";

export type ThemePreference = (typeof THEME_OPTIONS)[number];
export type ResolvedTheme = "light" | "dark";

type ThemeContextValue = {
  resolvedTheme: ResolvedTheme;
  setThemePreference: (preference: ThemePreference) => void;
  systemTheme: ResolvedTheme;
  themePreference: ThemePreference;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function isThemePreference(value: unknown): value is ThemePreference {
  return typeof value === "string" && THEME_OPTIONS.includes(value as ThemePreference);
}

function readStoredThemePreference(): ThemePreference {
  if (typeof window === "undefined") {
    return "system";
  }

  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(stored) ? stored : "system";
  } catch (_error) {
    return "system";
  }
}

function readSystemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "light";
  }

  return window.matchMedia(DARK_SCHEME_QUERY).matches ? "dark" : "light";
}

function resolveTheme(preference: ThemePreference, systemTheme: ResolvedTheme): ResolvedTheme {
  return preference === "system" ? systemTheme : preference;
}

function applyTheme(resolvedTheme: ResolvedTheme) {
  if (typeof document === "undefined") {
    return;
  }

  document.documentElement.dataset.theme = resolvedTheme;
  document.documentElement.style.colorScheme = resolvedTheme;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themePreference, setThemePreferenceState] = useState<ThemePreference>(readStoredThemePreference);
  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(readSystemTheme);
  const resolvedTheme = resolveTheme(themePreference, systemTheme);

  useEffect(() => {
    applyTheme(resolvedTheme);
  }, [resolvedTheme]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }

    const media = window.matchMedia(DARK_SCHEME_QUERY);
    const handleChange = (event: MediaQueryListEvent) => {
      setSystemTheme(event.matches ? "dark" : "light");
    };

    setSystemTheme(media.matches ? "dark" : "light");
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", handleChange);
      return () => media.removeEventListener("change", handleChange);
    }

    media.addListener(handleChange);
    return () => media.removeListener(handleChange);
  }, []);

  const setThemePreference = useCallback((preference: ThemePreference) => {
    setThemePreferenceState(preference);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, preference);
    } catch (_error) {
      // Theme changes should remain usable even when storage is blocked.
    }
  }, []);

  const value = useMemo(
    () => ({ resolvedTheme, setThemePreference, systemTheme, themePreference }),
    [resolvedTheme, setThemePreference, systemTheme, themePreference]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useThemePreference() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useThemePreference must be used within ThemeProvider.");
  }
  return context;
}
