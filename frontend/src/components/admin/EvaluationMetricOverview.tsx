import { useState } from "react";
import type {
  EvaluationMetricCatalog,
  EvaluationMetricCatalogItem,
  EvaluationScope
} from "../../features/evaluations/evaluationTypes";
import { MetricHelp } from "./MetricHelp";
import { groupMetricsByCategory, type MetricCategoryGroup } from "./MetricTaxonomy";

type EvaluationMetricOverviewProps = {
  catalog: EvaluationMetricCatalog | undefined;
  evaluationScope: EvaluationScope;
  metrics: Record<string, number>;
  provisional?: boolean;
};

type MetricSummaryEntry = [string, number];

export function EvaluationMetricOverview({
  catalog,
  evaluationScope,
  metrics,
  provisional = false
}: EvaluationMetricOverviewProps) {
  const definitions = metricDefinitionMap(catalog);
  const primaryMetrics = selectPrimaryMetrics(catalog, evaluationScope);
  const groups = groupMetricsByCategory(
    Object.entries(metrics),
    catalog,
    ([metricName]) => metricName
  );

  return (
    <>
      {primaryMetrics.length ? (
        <section className="admin-section key-metrics-section" aria-labelledby="key-metrics-title">
          <div className="key-metrics-title-row">
            <div>
              <h2 id="key-metrics-title">
                <span aria-hidden="true" className="key-metrics-title-icon">
                  ★
                </span>
                まず見る3項目
              </h2>
              <p>
                評価結果を確認するときは、最初にこの{primaryMetrics.length}
                つを確認してください。
              </p>
            </div>
            <span className="evaluation-scope-pill">
              評価スコープ: {formatEvaluationScope(evaluationScope)}
            </span>
          </div>

          <div className="key-metric-grid">
            {primaryMetrics.map((definition) => {
              const value = metrics[definition.metric_name];
              return (
                <article className="key-metric-card" key={definition.metric_name}>
                  <div className="key-metric-card-header">
                    <span className="key-metric-badge">
                      <span aria-hidden="true">★</span> 重要
                    </span>
                    {definition.method === "proxy" ? (
                      <span className="key-metric-badge">簡易</span>
                    ) : null}
                    {provisional ? (
                      <span className="key-metric-badge">暫定</span>
                    ) : null}
                    <MetricHelp metricName={definition.metric_name} />
                  </div>
                  <div className="key-metric-value-row">
                    <div>
                      <h3>{definition.display_name}</h3>
                      <code>{definition.metric_name}</code>
                    </div>
                    {value === undefined ? (
                      <span className="metric-not-available">
                        {metricUnavailableReason(definition)}
                      </span>
                    ) : (
                      <strong>{formatMetricValue(value, definition)}</strong>
                    )}
                  </div>
                  <p>{definition.plain_language_summary}</p>
                  {value !== undefined && definition.value_unit === "ratio" ? (
                    <MetricProgress value={value} label={definition.display_name} />
                  ) : null}
                </article>
              );
            })}
          </div>

          <p className="key-metrics-note">
            <span aria-hidden="true">ⓘ</span> 重要 = 最初に確認する指標です。合否判定ではありません。
          </p>
        </section>
      ) : null}

      <section className="admin-section metric-overview-section">
        <div className="section-title-row">
          <div>
            <h2>指標サマリー</h2>
            <p className="section-help">
              指標を役割ごとに整理しています。数値の色は合否を表しません。
            </p>
          </div>
        </div>

        {groups.length ? (
          <div className="metric-category-sections">
            {groups.map((group) => (
              <MetricCategorySection
                definitions={definitions}
                evaluationScope={evaluationScope}
                group={group}
                provisional={provisional}
                key={group.category}
              />
            ))}
          </div>
        ) : (
          <p className="muted">まだ指標はありません。</p>
        )}
      </section>
    </>
  );
}

function MetricCategorySection({
  definitions,
  evaluationScope,
  group,
  provisional
}: {
  definitions: Map<string, EvaluationMetricCatalogItem>;
  evaluationScope: EvaluationScope;
  group: MetricCategoryGroup<MetricSummaryEntry>;
  provisional: boolean;
}) {
  const [isOpen, setIsOpen] = useState(
    ["retrieval", "answer", "citation", "other"].includes(group.category)
  );

  return (
    <section className="metric-category-section">
      <button
        aria-expanded={isOpen}
        className="metric-category-toggle"
        onClick={() => setIsOpen((current) => !current)}
        type="button"
      >
        <span aria-hidden="true" className="metric-category-chevron">
          {isOpen ? "⌄" : "›"}
        </span>
        <strong>{group.label}</strong>
        <span>{group.items.length} 指標</span>
      </button>
      {isOpen ? (
        <div className="metric-category-table-wrap">
          <table className="metric-category-table">
            <thead>
              <tr>
                <th>指標</th>
                <th>値</th>
                <th>目安</th>
              </tr>
            </thead>
            <tbody>
              {group.items.map(([metricName, value]) => {
                const definition = definitions.get(metricName);
                const isPrimary = definition?.primary_scopes?.includes(evaluationScope) ?? false;
                return (
                  <tr key={metricName}>
                    <td>
                      <span className="metric-display-name">
                        {definition?.display_name ?? metricName}
                        {isPrimary ? (
                          <span
                            aria-label="まず見る指標"
                            className="metric-primary-star"
                            title="まず見る指標"
                          >
                            ★
                          </span>
                        ) : null}
                        {definition?.method === "proxy" ? (
                          <span className="metric-primary-star">簡易</span>
                        ) : null}
                        {isPrimary && provisional ? (
                          <span className="metric-primary-star">暫定</span>
                        ) : null}
                        <MetricHelp metricName={metricName} />
                      </span>
                      {definition ? <code className="metric-raw-name">{metricName}</code> : null}
                    </td>
                    <td>
                      <strong>{formatMetricValue(value, definition)}</strong>
                      {definition?.value_unit === "ratio" ? (
                        <MetricProgress value={value} label={definition.display_name} />
                      ) : null}
                    </td>
                    <td>{formatMetricDirection(definition)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function MetricProgress({ label, value }: { label: string; value: number }) {
  const percentage = Math.max(0, Math.min(100, value * 100));
  return (
    <span
      aria-label={`${label} ${percentage.toFixed(1)}%`}
      aria-valuemax={100}
      aria-valuemin={0}
      aria-valuenow={percentage}
      className="metric-progress"
      role="progressbar"
    >
      <span style={{ width: `${percentage}%` }} />
    </span>
  );
}

export function metricDefinitionMap(
  catalog: EvaluationMetricCatalog | undefined
): Map<string, EvaluationMetricCatalogItem> {
  return new Map((catalog?.metrics ?? []).map((metric) => [metric.metric_name, metric]));
}

export function selectPrimaryMetrics(
  catalog: EvaluationMetricCatalog | undefined,
  evaluationScope: EvaluationScope
): EvaluationMetricCatalogItem[] {
  return [...(catalog?.metrics ?? [])]
    .filter(
      (metric) =>
        metric.importance === "primary" && metric.primary_scopes?.includes(evaluationScope)
    )
    .sort(
      (left, right) =>
        left.display_priority - right.display_priority ||
        left.metric_name.localeCompare(right.metric_name)
    )
    .slice(0, 3);
}

export function formatMetricValue(
  value: number | null,
  definition: EvaluationMetricCatalogItem | undefined
): string {
  if (value === null) {
    return "N/A";
  }
  if (definition?.value_unit === "ratio") {
    return `${(value * 100).toFixed(1)}%`;
  }
  if (definition?.value_unit === "ms") {
    return `${formatNumber(value)} ms`;
  }
  if (definition?.value_unit === "count") {
    return `${formatNumber(value)} 回`;
  }
  return value.toFixed(3);
}

export function formatMetricDelta(
  value: number | null,
  definition: EvaluationMetricCatalogItem | undefined
): string {
  if (value === null) {
    return "-";
  }
  const sign = value > 0 ? "+" : "";
  if (definition?.value_unit === "ratio") {
    return `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}pt`;
  }
  if (definition?.value_unit === "ms") {
    return `${sign}${formatNumber(value)} ms`;
  }
  if (definition?.value_unit === "count") {
    return `${sign}${formatNumber(value)} 回`;
  }
  return `${sign}${value.toFixed(3)}`;
}

function formatMetricDirection(definition: EvaluationMetricCatalogItem | undefined): string {
  if (!definition) {
    return "定義を確認";
  }
  return definition.higher_is_better ? "高いほど良い" : "低いほど良い";
}

function metricUnavailableReason(definition: EvaluationMetricCatalogItem): string {
  if (definition.category === "answer" || definition.category === "citation") {
    return "未評価（回答未生成）";
  }
  return "未評価（結果なし）";
}

function formatEvaluationScope(scope: EvaluationScope): string {
  if (scope === "end_to_end") {
    return "検索＋回答";
  }
  if (scope === "answer") {
    return "回答のみ";
  }
  return "検索のみ";
}

function formatNumber(value: number): string {
  return value.toFixed(Number.isInteger(value) ? 0 : 1);
}
