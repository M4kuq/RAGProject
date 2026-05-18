import { RagAskConfidence } from "../../features/chat/chatTypes";

const LABELS: Record<string, string> = {
  High: "高",
  Medium: "中",
  Low: "低"
};

function percent(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 100)}%` : "未評価";
}

export function ConfidenceBadge({ confidence }: { confidence?: RagAskConfidence | null }) {
  if (!confidence) {
    return <span className="confidence-badge confidence-unknown">信頼度 未評価</span>;
  }
  const rawLabel =
    typeof confidence.confidence_label === "string" && confidence.confidence_label in LABELS
      ? confidence.confidence_label
      : "Unknown";
  const label = LABELS[rawLabel] ?? "不明";
  const answer = percent(confidence.answer_confidence);
  const groundedness = percent(confidence.groundedness_score);
  return (
    <span
      className={`confidence-badge confidence-${rawLabel.toLowerCase()}`}
      title={`回答信頼度 ${answer} / 根拠整合 ${groundedness}`}
    >
      信頼度 {label}
    </span>
  );
}
