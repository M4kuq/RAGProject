import { Link, useParams } from "react-router-dom";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, LoadingState } from "../../../components/common/States";
import { useEvaluationRunDetail } from "../../../features/evaluations/evaluationHooks";
import type { EvaluationMetricResult } from "../../../features/evaluations/evaluationTypes";
import { formatDate, formatSafeText, truncateText } from "../../../lib/format";

export function EvaluationDetailPage() {
  const evaluationRunId = Number(useParams().evaluationRunId);
  const run = useEvaluationRunDetail(evaluationRunId);

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
