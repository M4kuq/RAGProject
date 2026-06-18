import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  compareMetricNames,
  MetricHelp,
  orderedMetricEntries
} from "../../../components/admin/MetricHelp";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import {
  useActiveEvaluationDatasets,
  useCreateEvaluationDataset,
  useEvaluationRunDetail,
  usePromoteEvaluationFailures
} from "../../../features/evaluations/evaluationHooks";
import type {
  EvaluationFailureCandidate,
  EvaluationFailureSeverity,
  EvaluationMetricResult,
  EvaluationDataset,
  StrategyComparisonMetric
} from "../../../features/evaluations/evaluationTypes";
import { formatDate, formatSafeText, truncateText } from "../../../lib/format";

export function EvaluationDetailPage() {
  const evaluationRunId = Number(useParams().evaluationRunId);
  const run = useEvaluationRunDetail(evaluationRunId);
  const datasets = useActiveEvaluationDatasets();
  const createDataset = useCreateEvaluationDataset();
  const promoteFailures = usePromoteEvaluationFailures(evaluationRunId);
  const [promotionMessage, setPromotionMessage] = useState<string | null>(null);
  const [promotionTargetDatasetId, setPromotionTargetDatasetId] = useState("");
  const [selectedPromotionKeys, setSelectedPromotionKeys] = useState<string[]>([]);
  const [createdTargetDataset, setCreatedTargetDataset] = useState<EvaluationDataset | null>(null);
  const activeDatasets = useMemo(
    () => mergeDatasets(datasets.data ?? [], createdTargetDataset),
    [createdTargetDataset, datasets.data]
  );
  const selectedPromotionKeySet = useMemo(
    () => new Set(selectedPromotionKeys),
    [selectedPromotionKeys]
  );

  useEffect(() => {
    setSelectedPromotionKeys([]);
    setPromotionMessage(null);
    setPromotionTargetDatasetId("");
    setCreatedTargetDataset(null);
  }, [evaluationRunId]);

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

  const selectedTargetDatasetId =
    promotionTargetDatasetId || (run.data.evaluation_dataset_id ? String(run.data.evaluation_dataset_id) : "");
  const primaryPromotionKeys = primaryFailureKeys(run.data.failure_candidates);

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
            {orderedMetricEntries(Object.entries(run.data.metric_summary)).map(([name, value]) => (
              <tr key={name}>
                <td>
                  <span className="metric-name-cell">
                    {name}
                    <MetricHelp metricName={name} />
                  </span>
                </td>
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
              <th>Provider</th>
              <th>Cache</th>
              <th>
                <span className="metric-heading">
                  Metric
                  <MetricHelp metricName="metric_summary" />
                </span>
              </th>
              <th>Average</th>
              <th>p50</th>
              <th>p95</th>
              <th>Count</th>
              <th>Failed</th>
            </tr>
          </thead>
          <tbody>
            {[...run.data.strategy_comparison].sort(compareStrategyMetrics).map((metric) => (
              <tr key={`${comparisonMetricLabel(metric)}-${metric.metric_name}`}>
                <td>{comparisonMetricLabel(metric)}</td>
                <td>{metric.graph_store_provider ?? "-"}</td>
                <td>{metric.cache_mode ?? "-"}</td>
                <td>
                  <span className="metric-name-cell">
                    {metric.metric_name}
                    <MetricHelp metricName={metric.metric_name} />
                  </span>
                </td>
                <td>{formatScore(metric.average)}</td>
                <td>{formatScore(metric.p50)}</td>
                <td>{formatScore(metric.p95)}</td>
                <td>{metric.count}</td>
                <td>{metric.failed_count}</td>
              </tr>
            ))}
            {run.data.strategy_comparison.length === 0 ? (
              <tr>
                <td colSpan={9}>No strategy comparison yet.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <section className="admin-section">
        <h2>Provider Summary</h2>
        <dl className="detail-grid">
          {comparisonSummaryEntries(run.data.strategy_metrics_summary_json, "provider_comparison").map(
            ([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{value}</dd>
              </div>
            )
          )}
          {comparisonSummaryEntries(run.data.strategy_metrics_summary_json, "provider_comparison").length ===
          0 ? (
            <div>
              <dt>providers</dt>
              <dd>No provider summary yet.</dd>
            </div>
          ) : null}
        </dl>
      </section>

      <section className="admin-section">
        <h2>Cache Summary</h2>
        <dl className="detail-grid">
          {comparisonSummaryEntries(run.data.strategy_metrics_summary_json, "cache_comparison").map(
            ([name, value]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd>{value}</dd>
              </div>
            )
          )}
          {comparisonSummaryEntries(run.data.strategy_metrics_summary_json, "cache_comparison").length ===
          0 ? (
            <div>
              <dt>cache</dt>
              <dd>No cache summary yet.</dd>
            </div>
          ) : null}
        </dl>
      </section>

      <section className="admin-section">
        <h2>Agentic Summary</h2>
        <dl className="detail-grid">
          {agenticSummaryEntries(run.data.strategy_metrics_summary_json).map(([name, value]) => (
            <div key={name}>
              <dt>
                <span className="metric-name-cell">
                  {name}
                  <MetricHelp metricName={name} />
                </span>
              </dt>
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
          <div>
            <h2>Failure Candidates</h2>
            <span className="muted">
              {selectedPromotionKeys.length} selected / {run.data.failure_candidates.length} candidates
            </span>
            <p className="section-help">
              Promote failed evaluation items into a reusable dataset. Use Select primary failures
              to choose one representative candidate per source item, then promote it into the target
              dataset. Repeating the same promotion should skip duplicates.
            </p>
            <p className="section-help">
              Promotion copies safe failure metadata, metrics, reason codes, and strategy expectations only.
            </p>
          </div>
          <div className="failure-promotion-controls">
            <label className="failure-promotion-target">
              Target dataset
              <select
                aria-label="failure promotion target dataset"
                value={selectedTargetDatasetId}
                onChange={(event) => setPromotionTargetDatasetId(event.target.value)}
              >
                <option value="">Select dataset</option>
                {activeDatasets.map((dataset) => (
                  <option
                    key={dataset.evaluation_dataset_id}
                    value={dataset.evaluation_dataset_id}
                  >
                    {truncateText(dataset.dataset_name, 80)}
                  </option>
                ))}
              </select>
            </label>
            <div className="failure-promotion-actions">
              <button
                type="button"
                disabled={createDataset.isPending}
                onClick={() => {
                  const datasetName = `failure_promoted_run_${run.data.evaluation_run_id}`;
                  void createDataset
                    .mutateAsync({
                      dataset_name: datasetName,
                      description: `Failure promotion target for evaluation run #${run.data.evaluation_run_id}.`,
                      version: "v1",
                      source_type: "feedback_promoted",
                      status: "active",
                      metadata_json: {
                        source: "failure_promotion_target",
                        source_evaluation_run_id: run.data.evaluation_run_id
                      }
                    })
                    .then((dataset) => {
                      setCreatedTargetDataset(dataset);
                      setPromotionTargetDatasetId(String(dataset.evaluation_dataset_id));
                      setPromotionMessage(`Created target dataset ${dataset.dataset_name}.`);
                    });
                }}
              >
                Create target dataset
              </button>
              <button
                type="button"
                disabled={run.data.failure_candidates.length === 0}
                onClick={() => setSelectedPromotionKeys(primaryPromotionKeys)}
              >
                Select primary failures
              </button>
              <button
                type="button"
                disabled={selectedPromotionKeys.length === 0}
                onClick={() => setSelectedPromotionKeys([])}
              >
                Clear selection
              </button>
              <button
                type="button"
                disabled={
                  promoteFailures.isPending ||
                  !selectedTargetDatasetId ||
                  selectedPromotionKeys.length === 0
                }
                onClick={() => {
                  const targetDatasetId = Number(selectedTargetDatasetId);
                  if (!Number.isSafeInteger(targetDatasetId) || targetDatasetId < 1) {
                    return;
                  }
                  const confirmed = window.confirm(
                    "Promote selected failure candidates to this evaluation dataset?"
                  );
                  if (!confirmed) {
                    return;
                  }
                  void promoteFailures
                    .mutateAsync({
                      target_dataset_id: targetDatasetId,
                      promotion_keys: selectedPromotionKeys,
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
                Promote selected failures
              </button>
            </div>
          </div>
        </div>
        {datasets.error ? (
          <InlineAlert tone="error">Unable to load promotion target datasets.</InlineAlert>
        ) : null}
        {activeDatasets.length === 0 ? (
          <InlineAlert tone="info">
            No active target dataset exists. Create one here, then select failure candidates to promote.
          </InlineAlert>
        ) : null}
        {promotionMessage ? <InlineAlert tone="success">{promotionMessage}</InlineAlert> : null}
        {promoteFailures.error ? (
          <InlineAlert tone="error">{promoteFailures.error.message}</InlineAlert>
        ) : null}
        {createDataset.error ? (
          <InlineAlert tone="error">{createDataset.error.message}</InlineAlert>
        ) : null}
        <table className="admin-table">
          <thead>
            <tr>
              <th>Select</th>
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
                <td>
                  <input
                    aria-label={`select failure ${candidate.failure_type} ${truncateText(candidate.promotion_key, 8)}`}
                    type="checkbox"
                    checked={selectedPromotionKeySet.has(candidate.promotion_key)}
                    onChange={(event) => {
                      setSelectedPromotionKeys((current) =>
                        event.target.checked
                          ? Array.from(new Set([...current, candidate.promotion_key]))
                          : current.filter((key) => key !== candidate.promotion_key)
                      );
                    }}
                  />
                </td>
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
                <td colSpan={7}>No failure candidates.</td>
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
              <th>
                <span className="metric-heading">
                  Metrics
                  <MetricHelp metricName="case_metrics" />
                </span>
              </th>
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

function mergeDatasets(
  datasets: EvaluationDataset[],
  createdDataset: EvaluationDataset | null
): EvaluationDataset[] {
  if (!createdDataset || createdDataset.status !== "active") {
    return datasets;
  }
  if (datasets.some((dataset) => dataset.evaluation_dataset_id === createdDataset.evaluation_dataset_id)) {
    return datasets;
  }
  return [...datasets, createdDataset];
}

function primaryFailureKeys(candidates: EvaluationFailureCandidate[]): string[] {
  const byItem = new Map<number, EvaluationFailureCandidate>();
  for (const candidate of candidates) {
    const existing = byItem.get(candidate.evaluation_run_item_id);
    if (!existing || compareFailureCandidates(candidate, existing) < 0) {
      byItem.set(candidate.evaluation_run_item_id, candidate);
    }
  }
  return Array.from(byItem.values()).map((candidate) => candidate.promotion_key);
}

function compareFailureCandidates(
  candidate: EvaluationFailureCandidate,
  existing: EvaluationFailureCandidate
): number {
  return (
    severityPriority(candidate.severity) - severityPriority(existing.severity) ||
    failureTypePriority(candidate.failure_type) - failureTypePriority(existing.failure_type) ||
    candidate.failure_type.localeCompare(existing.failure_type) ||
    candidate.promotion_key.localeCompare(existing.promotion_key)
  );
}

function severityPriority(severity: EvaluationFailureSeverity): number {
  return { high: 0, medium: 1, low: 2 }[severity];
}

function failureTypePriority(failureType: string): number {
  const priority: Record<string, number> = {
    retrieval_exception: 0,
    generation_exception: 1,
    citation_build_failed: 2,
    fallback_failed: 3,
    budget_exhausted: 4,
    strategy_selection_incorrect: 5,
    no_context: 6
  };
  return priority[failureType] ?? 100;
}

function formatMetricDetails(metrics: EvaluationMetricResult[]) {
  const safeMetrics = metrics.filter((metric) => metric.metric_name !== "case_metadata");
  if (!safeMetrics.length) {
    return "-";
  }
  return (
    <span className="metric-detail-list">
      {[...safeMetrics].sort(compareEvaluationMetrics).map((metric) => {
        const label = metric.metric_label ? ` ${metric.metric_label}` : "";
        return (
          <span className="metric-detail-item" key={`${metric.strategy_type}-${metric.metric_name}`}>
            <span>
              {metric.metric_name}={formatScore(metric.metric_score)}
              {label}
            </span>
            <MetricHelp metricName={metric.metric_name} />
          </span>
        );
      })}
    </span>
  );
}

function comparisonMetricLabel(metric: StrategyComparisonMetric) {
  return metric.comparison_label || metric.strategy_type;
}

function compareStrategyMetrics(left: StrategyComparisonMetric, right: StrategyComparisonMetric) {
  return (
    comparisonMetricLabel(left).localeCompare(comparisonMetricLabel(right)) ||
    compareMetricNames(left.metric_name, right.metric_name)
  );
}

function compareEvaluationMetrics(left: EvaluationMetricResult, right: EvaluationMetricResult) {
  return (
    compareMetricNames(left.metric_name, right.metric_name) ||
    left.strategy_type.localeCompare(right.strategy_type)
  );
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

function comparisonSummaryEntries(
  summary: Record<string, unknown> | null,
  key: "provider_comparison" | "cache_comparison"
): Array<[string, string]> {
  const comparison = recordValue(summary, key);
  if (!comparison) {
    return [];
  }
  return Object.entries(comparison)
    .map(([name, value]) => {
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return [name, "-"] as [string, string];
      }
      const metricSummary = recordValue(value as Record<string, unknown>, "metric_summary");
      return [name, formatSummaryMetrics(metricSummary)] as [string, string];
    })
    .sort(([left], [right]) => left.localeCompare(right));
}

function formatSummaryMetrics(summary: Record<string, unknown> | null) {
  if (!summary) {
    return "-";
  }
  const parts = orderedMetricEntries(Object.entries(summary))
    .map(([name, value]) => (typeof value === "number" ? `${name}=${value.toFixed(3)}` : null))
    .filter((value): value is string => Boolean(value));
  return parts.length ? parts.join(", ") : "-";
}

function formatUnknownMetric(value: unknown) {
  return typeof value === "number" ? value.toFixed(3) : "-";
}
