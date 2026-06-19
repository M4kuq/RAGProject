import { THEME_OPTIONS, ThemePreference, useThemePreference } from "../app/theme";

const THEME_LABELS: Record<ThemePreference, { description: string; label: string }> = {
  system: {
    label: "システム設定",
    description: "OS のライト/ダーク設定に追従します。"
  },
  light: {
    label: "ライト",
    description: "明るい背景で表示します。"
  },
  dark: {
    label: "ダーク",
    description: "暗い背景で表示します。"
  }
};

export function SettingsPage() {
  const { resolvedTheme, setThemePreference, themePreference } = useThemePreference();

  return (
    <main className="panel settings-page">
      <header className="settings-header">
        <p>ユーザー設定</p>
        <h1>設定</h1>
      </header>

      <section className="settings-section" aria-labelledby="display-settings-heading">
        <div className="settings-section-header">
          <div>
            <h2 id="display-settings-heading">表示</h2>
            <p>アプリ全体のテーマを変更します。</p>
          </div>
          <span className="settings-current-theme">現在: {resolvedTheme === "dark" ? "ダーク" : "ライト"}</span>
        </div>

        <fieldset className="theme-choice-group">
          <legend>テーマ</legend>
          <div className="theme-choice-grid">
            {THEME_OPTIONS.map((option) => {
              const label = THEME_LABELS[option];
              const selected = themePreference === option;
              return (
                <label className={`theme-choice ${selected ? "selected" : ""}`} key={option}>
                  <input
                    aria-label={label.label}
                    checked={selected}
                    name="theme-preference"
                    onChange={() => setThemePreference(option)}
                    type="radio"
                    value={option}
                  />
                  <span>
                    <strong>{label.label}</strong>
                    <small>{label.description}</small>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>
      </section>
    </main>
  );
}
