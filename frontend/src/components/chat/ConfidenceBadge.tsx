import { RagAskConfidence } from "../../features/chat/chatTypes";

const LABELS: Record<string, string> = {
  High: "高",
  Medium: "中",
  Low: "低"
};

export function ConfidenceBadge({ confidence }: { confidence?: RagAskConfidence | null }) {
  if (!confidence) {
    return <span className="confidence-badge confidence-unknown">信頼度 未評価</span>;
  }
  const label = LABELS[confidence.confidence_label] ?? "不明";
  const answer = Math.round(confidence.answer_confidence * 100);
  const groundedness = Math.round(confidence.groundedness_score * 100);
  return (
    <span
      className={`confidence-badge confidence-${confidence.confidence_label.toLowerCase()}`}
      title={`回答信頼度 ${answer}% / 根拠整合 ${groundedness}%`}
    >
      信頼度 {label}
    </span>
  );
}
