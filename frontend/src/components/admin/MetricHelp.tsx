import { useId } from "react";

type MetricDefinition = {
  description: string;
  direction: string;
};

type HelpTooltipProps = {
  ariaLabel?: string;
  description: string;
  direction?: string;
  title: string;
};

const METRIC_DEFINITIONS: Record<string, MetricDefinition> = {
  budget_exhausted_rate: {
    description: "Agentic retrieval の実行予算を使い切ったケースの割合です。",
    direction: "低いほど軽く安定しています。"
  },
  cache_hit_rate: {
    description: "retrieval cache から再利用できた検索の割合です。",
    direction: "高いほどキャッシュが効いています。"
  },
  cache_saved_latency: {
    description: "cold baseline と比べてキャッシュで短縮できた推定時間です。",
    direction: "高いほど待ち時間を削減できています。"
  },
  case_metrics: {
    description: "評価ケースごとに保存された指標値です。",
    direction: "意味は各 metric_name に依存します。"
  },
  citation_coverage: {
    description: "回答が必要な引用を満たしているかを示します。",
    direction: "高いほど良好です。"
  },
  context_precision: {
    description: "取得した context が期待回答にどれだけ関係しているかを示します。",
    direction: "高いほど良好です。"
  },
  entity_relation_quality_summary: {
    description: "抽出した graph entity、relation、path、source chunk の安全な集計です。",
    direction: "graph quality 系指標と合わせて確認します。"
  },
  faithfulness: {
    description: "回答が期待回答や keyword signal に沿っているかを示します。",
    direction: "高いほど良好です。"
  },
  fallback_rate: {
    description: "fallback retrieval path を使ったケースの割合です。",
    direction: "低いほど想定どおりの経路で検索できています。"
  },
  graph_citation_coverage: {
    description: "graph path が引用可能な retrieval source に戻れているかを示します。",
    direction: "高いほど良好です。"
  },
  graph_path_relevance: {
    description: "graph path が期待する entity label や relation type に合っているかを示します。",
    direction: "高いほど良好です。"
  },
  groundedness: {
    description: "回答が取得済み evidence に支えられているかを示します。",
    direction: "高いほど良好です。"
  },
  metric_summary: {
    description: "評価 run または strategy の集計指標です。",
    direction: "意味は各 metric_name に依存します。"
  },
  mrr: {
    description: "最初の関連結果がどれだけ上位に出たかを示すランキング指標です。",
    direction: "高いほど良好です。"
  },
  multi_hop_answerability: {
    description: "取得した graph path が必要な hop depth を満たしているかを示します。",
    direction: "高いほど良好です。"
  },
  no_context_rate: {
    description: "回答に使える context が見つからなかったケースの割合です。",
    direction: "低いほど良好です。"
  },
  p95_latency: {
    description: "遅い側 5% に近い評価 latency です。",
    direction: "低いほど速く安定しています。"
  },
  recall_at_k: {
    description: "期待 document、chunk、keyword が検索結果に含まれたかを示します。",
    direction: "高いほど良好です。"
  },
  retrieval_call_count_avg: {
    description: "評価ケースごとの平均 retrieval call 数です。",
    direction: "低いほど軽く、高いほど検索を多く試しています。"
  },
  strategy_selection_accuracy: {
    description: "期待した retrieval strategy が選ばれたかを示します。",
    direction: "高いほど良好です。"
  },
  sufficiency_score_avg: {
    description: "取得 context が回答に十分かどうかの平均 score です。",
    direction: "高いほど良好です。"
  }
};

const METRIC_PRIORITY = [
  "faithfulness",
  "groundedness",
  "citation_coverage",
  "recall_at_k",
  "mrr",
  "context_precision",
  "graph_path_relevance",
  "graph_citation_coverage",
  "multi_hop_answerability",
  "strategy_selection_accuracy",
  "no_context_rate",
  "fallback_rate",
  "cache_hit_rate",
  "cache_saved_latency",
  "entity_relation_quality_summary",
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
  const definition = METRIC_DEFINITIONS[metricName] ?? {
    description: "この評価 run に記録された metric です。",
    direction: "解釈は metric の定義に依存します。"
  };

  return (
    <HelpTooltip
      ariaLabel={`${metricName} の説明`}
      description={definition.description}
      direction={definition.direction}
      title={metricName}
    />
  );
}

export function HelpTooltip({ ariaLabel, description, direction, title }: HelpTooltipProps) {
  const tooltipId = useId();
  const plainDescription = `${title}: ${description}${direction ? ` ${direction}` : ""}`;
  return (
    <span className="metric-help">
      <button
        aria-describedby={tooltipId}
        aria-label={ariaLabel ?? `${title} の説明`}
        className="metric-help-button"
        title={plainDescription}
        type="button"
      >
        ?
      </button>
      <span className="metric-help-tooltip" id={tooltipId} role="tooltip">
        <strong>{title}</strong>
        <span>{description}</span>
        {direction ? <span className="metric-help-direction">{direction}</span> : null}
      </span>
    </span>
  );
}

function metricPriority(metricName: string) {
  const index = METRIC_PRIORITY.indexOf(metricName);
  return index === -1 ? METRIC_PRIORITY.length : index;
}
