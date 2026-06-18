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
    description: "Share of evaluated cases that exhausted the agentic retrieval budget.",
    direction: "Lower is better."
  },
  cache_hit_rate: {
    description: "Share of evaluated retrieval runs served from retrieval cache.",
    direction: "Higher means more cache reuse."
  },
  cache_saved_latency: {
    description: "Estimated latency saved by warm cache compared with the cold baseline.",
    direction: "Higher means more milliseconds saved."
  },
  case_metrics: {
    description: "Per-case metric values saved for the evaluation item.",
    direction: "Interpretation depends on each metric."
  },
  citation_coverage: {
    description: "Whether the answer includes the required citation coverage.",
    direction: "Higher is better."
  },
  context_precision: {
    description: "How much retrieved context is relevant to the expected answer signal.",
    direction: "Higher is better."
  },
  entity_relation_quality_summary: {
    description: "Safe counts for extracted graph entities, relations, paths, and source chunks.",
    direction: "Use with graph quality metrics."
  },
  faithfulness: {
    description: "Whether the answer follows the expected answer or keyword signal.",
    direction: "Higher is better."
  },
  fallback_rate: {
    description: "Share of evaluated cases that used a fallback retrieval path.",
    direction: "Lower is better."
  },
  graph_citation_coverage: {
    description: "Graph paths that resolve back to citable retrieval sources.",
    direction: "Higher is better."
  },
  graph_path_relevance: {
    description: "Graph paths matched to expected safe entity labels and relation types.",
    direction: "Higher is better."
  },
  groundedness: {
    description: "Whether the answer is supported by retrieved evidence.",
    direction: "Higher is better."
  },
  metric_summary: {
    description: "Aggregate metric values for the evaluation run or strategy.",
    direction: "Interpretation depends on each metric."
  },
  mrr: {
    description: "Rank quality for the first relevant retrieved result.",
    direction: "Higher is better."
  },
  multi_hop_answerability: {
    description: "Retrieved graph paths cover the required hop depth for the case.",
    direction: "Higher is better."
  },
  no_context_rate: {
    description: "Share of cases where no answerable context was found.",
    direction: "Lower is better."
  },
  p95_latency: {
    description: "The slower-tail evaluation latency at the 95th percentile.",
    direction: "Lower is better."
  },
  recall_at_k: {
    description: "Whether expected documents, chunks, or keywords were retrieved.",
    direction: "Higher is better."
  },
  retrieval_call_count_avg: {
    description: "Average number of retrieval calls used per evaluated case.",
    direction: "Lower is lighter; higher means more search work."
  },
  strategy_selection_accuracy: {
    description: "Whether the expected retrieval strategy was selected.",
    direction: "Higher is better."
  },
  sufficiency_score_avg: {
    description: "Average score for whether retrieved context is sufficient.",
    direction: "Higher is better."
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
    description: "Metric recorded by this evaluation run.",
    direction: "Interpretation depends on the metric."
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
