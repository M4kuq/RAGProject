import { RagAskConfidence } from "../../features/chat/chatTypes";

const LABELS: Record<string, string> = {
  High: "High",
  Medium: "Medium",
  Low: "Low"
};

function percent(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 100)}%` : "not scored";
}

export function ConfidenceBadge({ confidence }: { confidence?: RagAskConfidence | null }) {
  if (!confidence) {
    return <span className="confidence-badge confidence-unknown">Confidence not scored</span>;
  }
  const rawLabel =
    typeof confidence.confidence_label === "string" && confidence.confidence_label in LABELS
      ? confidence.confidence_label
      : "Unknown";
  const label = LABELS[rawLabel] ?? "Unknown";
  const answer = percent(confidence.answer_confidence);
  const groundedness = percent(confidence.groundedness_score);
  return (
    <span
      className={`confidence-badge confidence-${rawLabel.toLowerCase()}`}
      title={`Answer confidence ${answer} / groundedness ${groundedness}`}
    >
      Confidence {label}
    </span>
  );
}
