import type { ReactNode } from "react";

export function LoadingState({ label = "読み込み中..." }: { label?: string }) {
  return <p className="muted">{label}</p>;
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state-box">
      <strong>{title}</strong>
      {children ? <p>{children}</p> : null}
    </div>
  );
}

export function ErrorState({ title = "エラー", error }: { title?: string; error: unknown }) {
  const message = error instanceof Error ? error.message : "処理に失敗しました。";
  return (
    <div className="state-box error-box" role="alert">
      <strong>{title}</strong>
      <p>{message}</p>
    </div>
  );
}

export function InlineAlert({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "error" | "success" }) {
  return (
    <p className={`inline-alert inline-alert-${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </p>
  );
}
