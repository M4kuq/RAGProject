import { RagAskConfidence } from "../../features/chat/chatTypes";

const LABELS: Record<string, string> = {
  High: "High",
  Medium: "Medium",
  Low: "Low"
};

function percent(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 100)}%` : "not scored";
}

const CONFIDENCE_BASIS_NOTE =
  "検索シグナル(retrieval/rerank/引用カバレッジ)に基づく参考値であり、回答の正確さを保証するものではありません。";

export function ConfidenceBadge({ confidence }: { confidence?: RagAskConfidence | null }) {
  if (!confidence) {
    return (
      <span className="confidence-badge confidence-unknown" title={CONFIDENCE_BASIS_NOTE}>
        Confidence not scored
      </span>
    );
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
      title={`Answer confidence ${answer} / groundedness ${groundedness}\n${CONFIDENCE_BASIS_NOTE}`}
    >
      Confidence {label}
    </span>
  );
}
