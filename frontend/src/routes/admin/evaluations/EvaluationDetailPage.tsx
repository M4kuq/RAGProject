import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  compareMetricNames,
  HelpTooltip,
  MetricHelp,
  orderedMetricEntries
} from "../../../components/admin/MetricHelp";
import { groupMetricsByCategory } from "../../../components/admin/MetricTaxonomy";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import {
  useActiveEvaluationDatasets,
  useCreateEvaluationDataset,
  useEvaluationMetricCatalog,
  useEvaluationRunDetail,
  usePromoteEvaluationFailures
} from "../../../features/evaluations/evaluationHooks";
import type {
  EvaluationFailureCandidate,
  EvaluationFailureSeverity,
  EvaluationMetricCatalog,
  EvaluationMetricResult,
  EvaluationDataset,
  StrategyComparisonMetric
} from "../../../features/evaluations/evaluationTypes";
import { formatDate, formatSafeText, truncateText } from "../../../lib/format";

export function EvaluationDetailPage() {
  const evaluationRunId = Number(useParams().evaluationRunId);
  const run = useEvaluationRunDetail(evaluationRunId);
  const metricCatalog = useEvaluationMetricCatalog();
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
        <LoadingState label="評価詳細を読み込んでいます..." />
      </main>
    );
  }

  if (run.error || !run.data) {
    return (
      <main className="admin-main">
        <ErrorState error={run.error ?? new Error("評価 run が見つかりません。")} />
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
          <h1>評価 #{run.data.evaluation_run_id}</h1>
          <p className="muted">{truncateText(run.data.dataset_name, 80)}</p>
        </div>
        <button type="button" onClick={() => void run.refetch()}>
          更新
        </button>
      </header>

      <section className="admin-section">
        <h2>状態</h2>
        <dl className="detail-grid">
          <div>
            <dt>状態</dt>
            <dd>
              <StatusBadge status={run.data.status} />
            </dd>
          </div>
          <div>
            <dt>ケース</dt>
            <dd>
              成功 {run.data.succeeded_count}/{run.data.case_count}
              {run.data.failed_count ? ` / 失敗 ${run.data.failed_count}` : ""}
            </dd>
          </div>
          <div>
            <dt>strategy</dt>
            <dd>{run.data.strategies.length ? run.data.strategies.join(", ") : run.data.strategy_type}</dd>
          </div>
          <div>
            <dt>
              <span className="metric-heading">
                推定コスト
                <HelpTooltip
                  description="評価 run の成功ケースで記録された LLM 生成コストの概算合計です。"
                  direction="料金表や provider の実請求とは一致しない場合があります。"
                  title="推定コスト（概算）"
                />
              </span>
            </dt>
            <dd>{formatCost(run.data.total_estimated_cost_usd)}</dd>
          </div>
          <div>
            <dt>
              <span className="metric-heading">
                トークン
                <HelpTooltip
                  description="評価 run の成功ケースで記録された入力、出力、合計 token 数です。"
                  direction="provider から usage が返らない場合は - になります。"
                  title="トークン数"
                />
              </span>
            </dt>
            <dd>
              {formatTokenBreakdown(
                run.data.total_input_tokens,
                run.data.total_output_tokens,
                run.data.total_tokens
              )}
            </dd>
          </div>
          <div>
            <dt>生成 latency</dt>
            <dd>{formatLatency(run.data.avg_generation_latency_ms)}</dd>
          </div>
          <div>
            <dt>provider</dt>
            <dd>{formatList(run.data.generation_providers)}</dd>
          </div>
          <div>
            <dt>model</dt>
            <dd>{formatList(run.data.generation_models)}</dd>
          </div>
          <div>
            <dt>起動元</dt>
            <dd>{run.data.trigger_type}</dd>
          </div>
          <div>
            <dt>ジョブ</dt>
            <dd>
              {run.data.job_id ? (
                <Link to={`/admin/jobs/${run.data.job_id}`}>#{run.data.job_id}</Link>
              ) : (
                "-"
              )}
            </dd>
          </div>
          <div>
            <dt>開始日時</dt>
            <dd>{formatDate(run.data.started_at)}</dd>
          </div>
          <div>
            <dt>終了日時</dt>
            <dd>{formatDate(run.data.finished_at)}</dd>
          </div>
          <div>
            <dt>エラー</dt>
            <dd>{run.data.error_code ?? formatSafeText(run.data.error_message, 120)}</dd>
          </div>
        </dl>
      </section>

      <section className="admin-section">
        <h2>指標サマリー</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>metric</th>
              <th>平均</th>
            </tr>
          </thead>
          <tbody>
            {groupMetricsByCategory(
              Object.entries(run.data.metric_summary),
              metricCatalog.data,
              ([name]) => name
            )
              .flatMap((group) => group.items.map((item) => ({ group, item })))
              .map(({ group, item: [name, value] }) => (
              <tr key={name}>
                <td>
                  <span className="metric-category-badge">{group.label}</span>
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
                <td colSpan={2}>まだ metric はありません。</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <section className="admin-section">
        <h2>strategy 比較</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>strategy</th>
              <th>provider</th>
              <th>cache</th>
              <th>
                <span className="metric-heading">
                  Metric
                  <MetricHelp metricName="metric_summary" />
                </span>
              </th>
              <th>平均</th>
              <th>p50</th>
              <th>p95</th>
              <th>件数</th>
              <th>失敗</th>
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
                <td colSpan={9}>まだ strategy comparison はありません。</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <section className="admin-section">
        <h2>provider サマリー</h2>
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
              <dd>まだ provider summary はありません。</dd>
            </div>
          ) : null}
        </dl>
      </section>

      <section className="admin-section">
        <h2>cache サマリー</h2>
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
              <dd>まだ cache summary はありません。</dd>
            </div>
          ) : null}
        </dl>
      </section>

      <section className="admin-section">
        <h2>Agentic サマリー</h2>
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
              <dd>まだ agentic summary はありません。</dd>
            </div>
          ) : null}
        </dl>
      </section>

      <section className="admin-section">
        <div className="section-header-row">
          <div>
            <h2>失敗候補の dataset 化</h2>
            <span className="muted">
              {selectedPromotionKeys.length} 件選択 / {run.data.failure_candidates.length} 件
            </span>
            <p className="section-help">
              失敗した評価 item を再利用できる dataset に追加します。「主要な失敗を選択」で source item ごとの代表候補を選び、
              選択した target dataset に追加します。同じ候補を再度追加しても重複は skip されます。
            </p>
            <p className="section-help">
              追加されるのは安全な失敗メタデータ、metric、reason code、strategy 期待値だけです。
            </p>
          </div>
          <div className="failure-promotion-controls">
            <label className="failure-promotion-target">
              追加先 dataset
              <select
                value={selectedTargetDatasetId}
                onChange={(event) => setPromotionTargetDatasetId(event.target.value)}
              >
                <option value="">dataset を選択</option>
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
                      description: `Evaluation run #${run.data.evaluation_run_id} の失敗候補を追加する dataset。`,
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
                      setPromotionMessage(`追加先 dataset ${dataset.dataset_name} を作成しました。`);
                    });
                }}
              >
                追加先を作成
              </button>
              <button
                type="button"
                disabled={run.data.failure_candidates.length === 0}
                onClick={() => setSelectedPromotionKeys(primaryPromotionKeys)}
              >
                主要な失敗を選択
              </button>
              <button
                type="button"
                disabled={selectedPromotionKeys.length === 0}
                onClick={() => setSelectedPromotionKeys([])}
              >
                選択を解除
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
                    "選択した失敗候補をこの evaluation dataset に追加しますか？"
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
                        `${result.created_count} 件を追加し、${result.skipped_count} 件を skip しました。`
                      );
                    });
                }}
              >
                選択した失敗を追加
              </button>
            </div>
          </div>
        </div>
        {datasets.error ? (
          <InlineAlert tone="error">追加先 dataset を読み込めません。</InlineAlert>
        ) : null}
        {activeDatasets.length === 0 ? (
          <InlineAlert tone="info">
            有効な追加先 dataset がありません。ここで作成してから失敗候補を選択してください。
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
              <th>選択</th>
              <th>case</th>
              <th>strategy</th>
              <th>失敗種別</th>
              <th>重要度</th>
              <th>理由</th>
              <th>promotion_key</th>
            </tr>
          </thead>
          <tbody>
            {run.data.failure_candidates.map((candidate) => (
              <tr key={candidate.promotion_key}>
                <td>
                  <input
                    aria-label={`失敗候補 ${candidate.failure_type} ${truncateText(candidate.promotion_key, 8)} を選択`}
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
                <td colSpan={7}>失敗候補はありません。</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <section className="admin-section">
        <h2>ケース結果</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>case</th>
              <th>strategy</th>
              <th>状態</th>
              <th>provider</th>
              <th>model</th>
              <th>
                <span className="metric-heading">
                  Tokens
                  <HelpTooltip
                    description="このケースの LLM 生成で記録された入力、出力、合計 token 数です。"
                    direction="usage が取得できない provider では - になります。"
                    title="トークン数"
                  />
                </span>
              </th>
              <th>
                <span className="metric-heading">
                  Cost
                  <HelpTooltip
                    description="このケースの LLM 生成 token 数から計算した概算コストです。"
                    direction="未知モデルや usage 欠落では - になります。"
                    title="推定コスト（概算）"
                  />
                </span>
              </th>
              <th>Faithfulness</th>
              <th>Groundedness</th>
              <th>Citation</th>
              <th>Context</th>
              <th>エラー</th>
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
                <td>{item.generation_provider ?? "-"}</td>
                <td>{item.generation_model ?? "-"}</td>
                <td>
                  {formatTokenBreakdown(item.input_tokens, item.output_tokens, item.total_tokens)}
                </td>
                <td>{formatCost(item.estimated_cost_usd)}</td>
                <td>{formatScore(item.faithfulness_score)}</td>
                <td>{formatScore(item.groundedness_score)}</td>
                <td>{formatScore(item.citation_coverage)}</td>
                <td>{formatScore(item.context_precision)}</td>
                <td>{item.error_code ?? formatSafeText(item.error_message, 80)}</td>
                <td>{formatMetricDetails(item.metrics, metricCatalog.data)}</td>
              </tr>
            ))}
            {run.data.items.length === 0 ? (
              <tr>
                <td colSpan={13}>まだケース結果はありません。</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <p>
        <Link to="/admin/evaluations">評価一覧へ戻る</Link>
      </p>
    </main>
  );
}

function formatScore(value: number | null) {
  return value === null ? "-" : value.toFixed(3);
}

function formatCost(value: number | null | undefined) {
  return value === null || value === undefined ? "-" : `$${value.toFixed(6)}`;
}

function formatInteger(value: number | null | undefined) {
  return value === null || value === undefined ? "-" : value.toLocaleString();
}

function formatTokenBreakdown(
  inputTokens: number | null | undefined,
  outputTokens: number | null | undefined,
  totalTokens: number | null | undefined
) {
  if (inputTokens === null && outputTokens === null && totalTokens === null) {
    return "-";
  }
  if (inputTokens === undefined && outputTokens === undefined && totalTokens === undefined) {
    return "-";
  }
  return `入力 ${formatInteger(inputTokens)} / 出力 ${formatInteger(outputTokens)} / 合計 ${formatInteger(totalTokens)}`;
}

function formatLatency(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${value.toFixed(value % 1 === 0 ? 0 : 1)} ms`;
}

function formatList(values: string[] | undefined) {
  return values?.length ? values.join(", ") : "-";
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

function formatMetricDetails(
  metrics: EvaluationMetricResult[],
  catalog: EvaluationMetricCatalog | undefined
) {
  const safeMetrics = metrics.filter((metric) => metric.metric_name !== "case_metadata");
  if (!safeMetrics.length) {
    return "-";
  }
  const groups = groupMetricsByCategory(safeMetrics, catalog, (metric) => metric.metric_name);
  return (
    <span className="metric-detail-list">
      {groups.map((group) => (
        <span className="metric-detail-group" key={group.category}>
          <span className="metric-category-label">{group.label}</span>
          {group.items.map((metric) => {
            const label = metric.metric_label ? ` ${metric.metric_label}` : "";
            return (
              <span
                className="metric-detail-item"
                key={`${metric.strategy_type}-${metric.metric_name}`}
              >
                <span>
                  {metric.metric_name}={formatScore(metric.metric_score ?? metric.metric_value)}
                  {label}
                </span>
                <MetricHelp metricName={metric.metric_name} />
              </span>
            );
          })}
        </span>
      ))}
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
