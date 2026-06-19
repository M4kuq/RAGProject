import { statusLabel } from "./adminLabels";

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const value = status ?? "unknown";
  return (
    <span
      className={`status-badge status-${value.replace(/[^a-z0-9_-]/gi, "-")}`}
      title={`status: ${value}`}
    >
      {statusLabel(value)}
    </span>
  );
}
