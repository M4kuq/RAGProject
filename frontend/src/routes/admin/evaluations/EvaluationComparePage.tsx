import { Link, useSearchParams } from "react-router-dom";
import {
  compareMetricNames,
  MetricHelp,
  orderedMetricEntries
} from "../../../components/admin/MetricHelp";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { useEvaluationRunComparison } from "../../../features/evaluations/evaluationHooks";
import type {
  EvaluationCaseComparison,
  EvaluationCaseTransition,
  EvaluationComparisonDirection,
  EvaluationMetricComparison,
  EvaluationRunSummary
} from "../../../features/evaluations/evaluationTypes";
import { formatDate, truncateText } from "../../../lib/format";

export function EvaluationComparePage() {
  const [searchParams] = useSearchParams();
  const baseRunId = parseRunId(searchParams.get("base"));
  const candidateRunId = parseRunId(searchParams.get("candidate"));
  const comparison = useEvaluationRunComparison(baseRunId, candidateRunId);
  const hasValidParams = baseRunId !== null && candidateRunId !== null;

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>評価 run 比較</h1>
          <p className="muted">
            既存の評価結果から base と candidate の差分を read-only で計算します。
          </p>
        </div>
        <Link className="button-link" to="/admin/evaluations">
          評価一覧へ戻る
        </Link>
      </header>

      {!hasValidParams ? (
        <InlineAlert tone="error">比較する base と candidate の run id を指定してください。</InlineAlert>
      ) : null}
      {comparison.isLoading ? <LoadingState label="比較結果を読み込んでいます..." /> : null}
      {comparison.error ? <ErrorState error={comparison.error} /> : null}
      {comparison.data ? (
        <>
          {comparison.data.base_run.status !== "succeeded" ||
          comparison.data.candidate_run.status !== "succeeded" ? (
            <InlineAlert tone="info">
              succeeded 以外の run も比較できます。status と case 数を確認してください。
            </InlineAlert>
          ) : null}
          <section className="admin-section">
            <div className="comparison-run-pair">
              <RunSummary title="base" run={comparison.data.base_run} />
              <RunSummary title="candidate" run={comparison.data.candidate_run} />
            </div>
          </section>

          <section className="admin-section">
            <h2>サマリ</h2>
            <dl className="comparison-summary-grid">
              <SummaryItem label="改善 metric" value={comparison.data.summary.improved_metric_count} />
              <SummaryItem label="悪化 metric" value={comparison.data.summary.regressed_metric_count} />
              <SummaryItem label="変化なし metric" value={comparison.data.summary.unchanged_metric_count} />
              <SummaryItem label="回帰 case" value={comparison.data.summary.regressed_case_count} />
              <SummaryItem label="改善 case" value={comparison.data.summary.improved_case_count} />
              <SummaryItem label="共通 case" value={comparison.data.summary.common_case_count} />
              <SummaryItem label="base のみ" value={comparison.data.summary.base_only_case_count} />
              <SummaryItem label="candidate のみ" value={comparison.data.summary.candidate_only_case_count} />
            </dl>
          </section>

          <section className="admin-section">
            <h2>metric 差分</h2>
            <table className="admin-table" aria-label="metric 差分">
              <thead>
                <tr>
                  <th>metric</th>
                  <th>base</th>
                  <th>candidate</th>
                  <th>Δ</th>
                  <th>判定</th>
                  <th>向き</th>
                </tr>
              </thead>
              <tbody>
                {[...comparison.data.metrics].sort(compareMetricComparisons).map((metric) => (
                  <tr
                    className={`comparison-direction-${metric.direction}`}
                    key={metric.metric_name}
                  >
                    <td>
                      <span className="metric-name-cell">
                        {metric.metric_name}
                        <MetricHelp metricName={metric.metric_name} />
                      </span>
                    </td>
                    <td>{formatScore(metric.base_score)}</td>
                    <td>{formatScore(metric.candidate_score)}</td>
                    <td>{formatDelta(metric.delta)}</td>
                    <td>
                      <span className="comparison-direction-badge">
                        {directionLabel(metric.direction)}
                      </span>
                    </td>
                    <td>{metric.lower_is_better ? "低いほど良い" : "高いほど良い"}</td>
                  </tr>
                ))}
                {comparison.data.metrics.length === 0 ? (
                  <tr>
                    <td colSpan={6}>比較できる metric がありません。</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </section>

          <section className="admin-section">
            <h2>case 差分</h2>
            <table className="admin-table" aria-label="case 差分">
              <thead>
                <tr>
                  <th>case</th>
                  <th>target</th>
                  <th>base</th>
                  <th>candidate</th>
                  <th>判定</th>
                  <th>主な metric Δ</th>
                </tr>
              </thead>
              <tbody>
                {[...comparison.data.cases].sort(compareCases).map((caseComparison) => (
                  <tr
                    className={`comparison-direction-${caseComparison.transition}`}
                    key={`${caseComparison.case_id}-${caseComparison.comparison_label ?? "default"}`}
                  >
                    <td>
                      <span className="case-identifier">
                        {truncateText(caseComparison.case_id, 48)}
                      </span>
                    </td>
                    <td>{caseComparison.comparison_label ?? "-"}</td>
                    <td>{caseComparison.base_status ? <StatusBadge status={caseComparison.base_status} /> : "-"}</td>
                    <td>
                      {caseComparison.candidate_status ? (
                        <StatusBadge status={caseComparison.candidate_status} />
                      ) : (
                        "-"
                      )}
                    </td>
                    <td>
                      <span className="comparison-direction-badge">
                        {transitionLabel(caseComparison.transition)}
                      </span>
                    </td>
                    <td>{formatMetricDeltas(caseComparison.metric_deltas)}</td>
                  </tr>
                ))}
                {comparison.data.cases.length === 0 ? (
                  <tr>
                    <td colSpan={6}>比較できる case がありません。</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </section>
        </>
      ) : null}
    </main>
  );
}

function RunSummary({ run, title }: { run: EvaluationRunSummary; title: string }) {
  return (
    <section className="comparison-run-panel" aria-label={`${title} run`}>
      <h2>{title}</h2>
      <dl className="detail-grid">
        <div>
          <dt>run</dt>
          <dd>
            <Link to={`/admin/evaluations/${run.evaluation_run_id}`}>#{run.evaluation_run_id}</Link>
          </dd>
        </div>
        <div>
          <dt>dataset</dt>
          <dd>{truncateText(run.dataset_name, 48)}</dd>
        </div>
        <div>
          <dt>status</dt>
          <dd>
            <StatusBadge status={run.status} />
          </dd>
        </div>
        <div>
          <dt>case</dt>
          <dd>
            成功 {run.succeeded_count}/{run.case_count}
            {run.failed_count ? ` / 失敗 ${run.failed_count}` : ""}
          </dd>
        </div>
        <div>
          <dt>strategy</dt>
          <dd>{run.strategies.length ? run.strategies.join(", ") : run.strategy_type}</dd>
        </div>
        <div>
          <dt>終了日時</dt>
          <dd>{formatDate(run.finished_at)}</dd>
        </div>
      </dl>
    </section>
  );
}

function SummaryItem({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function parseRunId(value: string | null): number | null {
  if (!value) {
    return null;
  }
  const runId = Number(value);
  return Number.isSafeInteger(runId) && runId > 0 ? runId : null;
}

function compareMetricComparisons(
  left: EvaluationMetricComparison,
  right: EvaluationMetricComparison
) {
  return compareMetricNames(left.metric_name, right.metric_name);
}

function compareCases(left: EvaluationCaseComparison, right: EvaluationCaseComparison) {
  return (
    transitionPriority(left.transition) - transitionPriority(right.transition) ||
    left.case_id.localeCompare(right.case_id) ||
    (left.comparison_label ?? "").localeCompare(right.comparison_label ?? "")
  );
}

function transitionPriority(transition: EvaluationCaseTransition) {
  return { regressed: 0, removed: 1, added: 2, improved: 3, unchanged: 4 }[transition];
}

function formatScore(value: number | null) {
  return value === null ? "-" : value.toFixed(3);
}

function formatDelta(value: number | null) {
  if (value === null) {
    return "-";
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(3)}`;
}

function formatMetricDeltas(deltas: Record<string, number | null>) {
  const entries = orderedMetricEntries(Object.entries(deltas))
    .filter(([, value]) => value !== null)
    .slice(0, 4);
  if (!entries.length) {
    return "-";
  }
  return entries.map(([name, value]) => `${name}=${formatDelta(value)}`).join(", ");
}

function directionLabel(direction: EvaluationComparisonDirection) {
  return {
    improved: "改善",
    not_applicable: "比較不可",
    regressed: "悪化",
    unchanged: "変化なし"
  }[direction];
}

function transitionLabel(transition: EvaluationCaseTransition) {
  return {
    added: "candidate のみ",
    improved: "改善",
    regressed: "回帰",
    removed: "base のみ",
    unchanged: "変化なし"
  }[transition];
}
