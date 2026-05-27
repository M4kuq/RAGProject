import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import {
  useEvaluationRunDetail,
  usePromoteEvaluationFailures
} from "../../../features/evaluations/evaluationHooks";
import type {
  EvaluationFailureCandidate,
  EvaluationMetricResult
} from "../../../features/evaluations/evaluationTypes";
import { formatDate, formatSafeText, truncateText } from "../../../lib/format";

export function EvaluationDetailPage() {
  const evaluationRunId = Number(useParams().evaluationRunId);
  const run = useEvaluationRunDetail(evaluationRunId);
  const promoteFailures = usePromoteEvaluationFailures(evaluationRunId);
  const [promotionMessage, setPromotionMessage] = useState<string | null>(null);

  if (run.isLoading) {
    return (
      <main className="admin-main">
        <LoadingState />
      </main>
    );
  }

  if (run.error || !run.data) {
    return (
      <main className="admin-main">
        <ErrorState error={run.error ?? new Error("Evaluation run not found.")} />
      </main>
    );
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>Evaluation #{run.data.evaluation_run_id}</h1>
          <p className="muted">{truncateText(run.data.dataset_name, 80)}</p>
        </div>
        <button type="button" onClick={() => void run.refetch()}>
          Refresh
        </button>
      </header>

      <section className="admin-section">
        <h2>Status</h2>
        <dl className="detail-grid">
          <div>
            <dt>Status</dt>
            <dd>
              <StatusBadge status={run.data.status} />
            </dd>
          </div>
          <div>
            <dt>Cases</dt>
            <dd>
              {run.data.succeeded_count}/{run.data.case_count} succeeded
              {run.data.failed_count ? `, ${run.data.failed_count} failed` : ""}
            </dd>
          </div>
          <div>
            <dt>Strategies</dt>
            <dd>{run.data.strategies.length ? run.data.strategies.join(", ") : run.data.strategy_type}</dd>
          </div>
          <div>
            <dt>Trigger</dt>
            <dd>{run.data.trigger_type}</dd>
          </div>
          <div>
            <dt>Job</dt>
            <dd>
              {run.data.job_id ? (
                <Link to={`/admin/jobs/${run.data.job_id}`}>#{run.data.job_id}</Link>
              ) : (
                "-"
              )}
            </dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd>{formatDate(run.data.started_at)}</dd>
          </div>
          <div>
            <dt>Finished</dt>
            <dd>{formatDate(run.data.finished_at)}</dd>
          </div>
          <div>
            <dt>Error</dt>
            <dd>{run.data.error_code ?? formatSafeText(run.data.error_message, 120)}</dd>
          </div>
        </dl>
      </section>

      <section className="admin-section">
        <h2>Metric Summary</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Metric</th>
              <th>Average</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(run.data.metric_summary).map(([name, value]) => (
              <tr key={name}>
                <td>{name}</td>
                <td>{value.toFixed(3)}</td>
              </tr>
            ))}
            {Object.keys(run.data.metric_summary).length === 0 ? (
              <tr>
                <td colSpan={2}>No metrics yet.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <section className="admin-section">
        <h2>Strategy Comparison</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Metric</th>
              <th>Average</th>
              <th>p50</th>
              <th>p95</th>
              <th>Count</th>
              <th>Failed</th>
            </tr>
          </thead>
          <tbody>
            {run.data.strategy_comparison.map((metric) => (
              <tr key={`${metric.strategy_type}-${metric.metric_name}`}>
                <td>{metric.strategy_type}</td>
                <td>{metric.metric_name}</td>
                <td>{formatScore(metric.average)}</td>
                <td>{formatScore(metric.p50)}</td>
                <td>{formatScore(metric.p95)}</td>
                <td>{metric.count}</td>
                <td>{metric.failed_count}</td>
              </tr>
            ))}
            {run.data.strategy_comparison.length === 0 ? (
              <tr>
                <td colSpan={7}>No strategy comparison yet.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <section className="admin-section">
        <h2>Agentic Summary</h2>
        <dl className="detail-grid">
          {agenticSummaryEntries(run.data.strategy_metrics_summary_json).map(([name, value]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
          {agenticSummaryEntries(run.data.strategy_metrics_summary_json).length === 0 ? (
            <div>
              <dt>agentic_router</dt>
              <dd>No agentic summary yet.</dd>
            </div>
          ) : null}
        </dl>
      </section>

      <section className="admin-section">
        <div className="section-header-row">
          <h2>Failure Candidates</h2>
          <button
            type="button"
            disabled={
              promoteFailures.isPending ||
              !run.data.evaluation_dataset_id ||
              run.data.failure_candidates.length === 0
            }
            onClick={() => {
              if (!run.data.evaluation_dataset_id) {
                return;
              }
              const confirmed = window.confirm(
                "Promote one primary failure per source item to this evaluation dataset?"
              );
              if (!confirmed) {
                return;
              }
              void promoteFailures
                .mutateAsync({
                  target_dataset_id: run.data.evaluation_dataset_id,
                  failure_types: primaryFailureTypes(run.data.failure_candidates),
                  min_severity: "medium",
                  limit: 50
                })
                .then((result) => {
                  setPromotionMessage(
                    `Promoted ${result.created_count} case(s), skipped ${result.skipped_count}.`
                  );
                });
            }}
          >
            Promote primary failures
          </button>
        </div>
        {promotionMessage ? <InlineAlert tone="success">{promotionMessage}</InlineAlert> : null}
        {promoteFailures.error ? (
          <InlineAlert tone="error">{promoteFailures.error.message}</InlineAlert>
        ) : null}
        <table className="admin-table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Strategy</th>
              <th>Failure</th>
              <th>Severity</th>
              <th>Reason</th>
              <th>Promotion key</th>
            </tr>
          </thead>
          <tbody>
            {run.data.failure_candidates.map((candidate) => (
              <tr key={candidate.promotion_key}>
                <td>{candidate.case_key ?? `item-${candidate.evaluation_run_item_id}`}</td>
                <td>{candidate.strategy_type}</td>
                <td>{candidate.failure_type}</td>
                <td>{candidate.severity}</td>
                <td>{candidate.failure_reason_codes.map((code) => formatSafeText(code, 40)).join(", ")}</td>
                <td>{truncateText(candidate.promotion_key, 18)}</td>
              </tr>
            ))}
            {run.data.failure_candidates.length === 0 ? (
              <tr>
                <td colSpan={6}>No failure candidates.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <section className="admin-section">
        <h2>Case Results</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Case</th>
              <th>Strategy</th>
              <th>Status</th>
              <th>Faithfulness</th>
              <th>Groundedness</th>
              <th>Citation</th>
              <th>Context</th>
              <th>Error</th>
              <th>Metrics</th>
            </tr>
          </thead>
          <tbody>
            {run.data.items.map((item) => (
              <tr key={item.evaluation_run_item_id}>
                <td>{item.case_key ?? item.case_id ?? `item-${item.evaluation_run_item_id}`}</td>
                <td>{item.strategy_type}</td>
                <td>
                  <StatusBadge status={item.status} />
                </td>
                <td>{formatScore(item.faithfulness_score)}</td>
                <td>{formatScore(item.groundedness_score)}</td>
                <td>{formatScore(item.citation_coverage)}</td>
                <td>{formatScore(item.context_precision)}</td>
                <td>{item.error_code ?? formatSafeText(item.error_message, 80)}</td>
                <td>{formatMetricDetails(item.metrics)}</td>
              </tr>
            ))}
            {run.data.items.length === 0 ? (
              <tr>
                <td colSpan={9}>No case results yet.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <p>
        <Link to="/admin/evaluations">Back to Evaluations</Link>
      </p>
    </main>
  );
}

function formatScore(value: number | null) {
  return value === null ? "-" : value.toFixed(3);
}

function primaryFailureTypes(candidates: EvaluationFailureCandidate[]): string[] {
  const byItem = new Map<number, EvaluationFailureCandidate>();
  for (const candidate of candidates) {
    const existing = byItem.get(candidate.evaluation_run_item_id);
    if (!existing || failurePriority(candidate) < failurePriority(existing)) {
      byItem.set(candidate.evaluation_run_item_id, candidate);
    }
  }
  return Array.from(
    new Set(Array.from(byItem.values()).map((candidate) => candidate.failure_type))
  );
}

function failurePriority(candidate: EvaluationFailureCandidate): string {
  const severityRank = { high: 0, medium: 1, low: 2 }[candidate.severity];
  return `${severityRank}:${candidate.failure_type}:${candidate.promotion_key}`;
}

function formatMetricDetails(metrics: EvaluationMetricResult[]) {
  const safeMetrics = metrics.filter((metric) => metric.metric_name !== "case_metadata");
  if (!safeMetrics.length) {
    return "-";
  }
  return safeMetrics
    .map((metric) => {
      const label = metric.metric_label ? ` ${metric.metric_label}` : "";
      return `${metric.metric_name}=${formatScore(metric.metric_score)}${label}`;
    })
    .join(", ");
}

function agenticSummaryEntries(summary: Record<string, unknown> | null): Array<[string, string]> {
  const agentic = recordValue(summary, "agentic_summary");
  if (!agentic) {
    return [];
  }
  const names = [
    "strategy_selection_accuracy",
    "fallback_rate",
    "budget_exhausted_rate",
    "sufficiency_score_avg",
    "retrieval_call_count_avg",
    "no_context_rate",
    "p95_latency"
  ];
  return names
    .map((name) => [name, formatUnknownMetric(agentic[name])] as [string, string])
    .filter(([, value]) => value !== "-");
}

function recordValue(value: Record<string, unknown> | null, key: string): Record<string, unknown> | null {
  const nested = value?.[key];
  return nested && typeof nested === "object" && !Array.isArray(nested)
    ? (nested as Record<string, unknown>)
    : null;
}

function formatUnknownMetric(value: unknown) {
  return typeof value === "number" ? value.toFixed(3) : "-";
}
