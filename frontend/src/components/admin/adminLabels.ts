const STATUS_LABELS: Record<string, string> = {
  active: "有効",
  archived: "アーカイブ",
  canceled: "中止",
  failed: "失敗",
  pending_review: "承認待ち",
  processing: "処理中",
  queued: "待機中",
  ready: "準備完了",
  running: "実行中",
  succeeded: "成功",
  unknown: "不明"
};

export function statusLabel(status: string | null | undefined): string {
  const value = status ?? "unknown";
  return STATUS_LABELS[value] ?? value;
}

export function formatCount(value: number | undefined): string {
  return typeof value === "number" ? value.toLocaleString("ja-JP") : "-";
}
