import { useId } from "react";

type MetricDefinition = {
  description: string;
  direction: string;
};

const METRIC_DEFINITIONS: Record<string, MetricDefinition> = {
  budget_exhausted_rate: {
    description: "探索予算を使い切った評価項目の割合です。",
    direction: "低いほどよい指標です。"
  },
  case_metrics: {
    description: "各評価項目で保存された指標の内訳です。内部情報は表示しません。",
    direction: "指標ごとに見方が異なります。"
  },
  citation_coverage: {
    description: "回答に必要な引用が付いているかを見る指標です。",
    direction: "高いほどよい指標です。"
  },
  context_precision: {
    description: "取得した文脈に、期待回答へつながる根拠がどれだけ含まれるかを見る指標です。",
    direction: "高いほどよい指標です。"
  },
  faithfulness: {
    description: "回答が期待回答・期待キーワード・根拠に沿っているかを見る指標です。",
    direction: "高いほどよい指標です。"
  },
  fallback_rate: {
    description: "主戦略から代替戦略に切り替わった割合です。",
    direction: "低いほどよい指標です。"
  },
  groundedness: {
    description: "回答が検索で取得した根拠に支えられているかを見る指標です。",
    direction: "高いほどよい指標です。"
  },
  metric_summary: {
    description: "評価実行全体の平均値です。主要指標を上から重要度順に表示します。",
    direction: "指標ごとに見方が異なります。"
  },
  mrr: {
    description: "最初に見つかった正解根拠が、検索結果のどの順位にあるかを見る指標です。",
    direction: "高いほどよい指標です。"
  },
  no_context_rate: {
    description: "回答に使える根拠が見つからなかった割合です。",
    direction: "低いほどよい指標です。"
  },
  p95_latency: {
    description: "遅いケースを含めた処理時間の目安です。",
    direction: "低いほどよい指標です。"
  },
  recall_at_k: {
    description: "期待する文書・分割された根拠・キーワードを検索結果に含められたかを見る指標です。",
    direction: "高いほどよい指標です。"
  },
  retrieval_call_count_avg: {
    description: "1つの評価項目あたりの検索呼び出し回数です。探索の広がりと処理負荷を見る指標です。",
    direction: "少ないほど軽く、多いほど追加探索が増えています。"
  },
  strategy_selection_accuracy: {
    description: "期待された検索戦略を選べたかを見る指標です。",
    direction: "高いほどよい指標です。"
  },
  sufficiency_score_avg: {
    description: "取得した根拠だけで回答に足りるかを見る指標です。",
    direction: "高いほどよい指標です。"
  }
};

const METRIC_PRIORITY = [
  "faithfulness",
  "groundedness",
  "citation_coverage",
  "recall_at_k",
  "mrr",
  "context_precision",
  "strategy_selection_accuracy",
  "no_context_rate",
  "fallback_rate",
  "budget_exhausted_rate",
  "sufficiency_score_avg",
  "retrieval_call_count_avg",
  "p95_latency"
];

export function compareMetricNames(left: string, right: string) {
  const leftIndex = metricPriority(left);
  const rightIndex = metricPriority(right);
  if (leftIndex !== rightIndex) {
    return leftIndex - rightIndex;
  }
  return left.localeCompare(right);
}

export function orderedMetricEntries<T>(entries: Array<[string, T]>): Array<[string, T]> {
  return [...entries].sort(([left], [right]) => compareMetricNames(left, right));
}

export function MetricHelp({ metricName }: { metricName: string }) {
  const tooltipId = useId();
  const definition = METRIC_DEFINITIONS[metricName] ?? {
    description: "この評価実行で記録された指標です。",
    direction: "指標ごとに見方が異なります。"
  };
  const title = metricName;
  const plainDescription = `${metricName}: ${definition.description} ${definition.direction}`;

  return (
    <span className="metric-help">
      <button
        aria-describedby={tooltipId}
        aria-label={`${metricName} の説明`}
        className="metric-help-button"
        title={plainDescription}
        type="button"
      >
        ?
      </button>
      <span className="metric-help-tooltip" id={tooltipId} role="tooltip">
        <strong>{title}</strong>
        <span>{definition.description}</span>
        <span className="metric-help-direction">{definition.direction}</span>
      </span>
    </span>
  );
}

function metricPriority(metricName: string) {
  const index = METRIC_PRIORITY.indexOf(metricName);
  return index === -1 ? METRIC_PRIORITY.length : index;
}
