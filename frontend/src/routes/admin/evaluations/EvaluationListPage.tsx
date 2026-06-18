import { FormEvent, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { MetricHelp, orderedMetricEntries } from "../../../components/admin/MetricHelp";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { EmptyState, ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import {
  useCreateEvaluationRun,
  useEvaluationDatasets,
  useEvaluationRuns
} from "../../../features/evaluations/evaluationHooks";
import type {
  EvaluationCacheMode,
  EvaluationRunnableStrategy
} from "../../../features/evaluations/evaluationTypes";
import { formatDate, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;

export function EvaluationListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [datasetName, setDatasetName] = useState("phase1_smoke");
  const [evaluationDatasetId, setEvaluationDatasetId] = useState<number | null>(null);
  const [caseLimit, setCaseLimit] = useState(10);
  const [strategies, setStrategies] = useState<EvaluationRunnableStrategy[]>(["dense"]);
  const [cacheModes, setCacheModes] = useState<EvaluationCacheMode[]>(["default"]);
  const [message, setMessage] = useState<string | null>(null);
  const params = useMemo(
    () => ({
      page: Number(searchParams.get("page") ?? 1),
      page_size: PAGE_SIZE
    }),
    [searchParams]
  );
  const runs = useEvaluationRuns(params);
  const datasets = useEvaluationDatasets({ page: 1, page_size: 50 });
  const createRun = useCreateEvaluationRun();

  function updatePage(page: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(page));
    setSearchParams(next);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const safeCaseLimit = Number.isFinite(caseLimit) ? Math.min(50, Math.max(1, caseLimit)) : 10;
    const selectedDataset = datasets.data?.items.find(
      (dataset) => dataset.evaluation_dataset_id === evaluationDatasetId
    );
    const result = await createRun.mutateAsync({
      dataset_name: selectedDataset?.dataset_name ?? (datasetName.trim() || "phase1_smoke"),
      evaluation_dataset_id: evaluationDatasetId,
      case_limit: safeCaseLimit,
      strategy_type: strategies[0] ?? "dense",
      strategies,
      cache_modes: cacheModes,
      trigger_type: "manual"
    });
    setMessage(`Evaluation run #${result.evaluation_run_id} queued as job #${result.job_id}.`);
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>Evaluations</h1>
          <p className="muted">
            Run deterministic Phase1 evaluation fixtures and inspect safe metric summaries.
          </p>
        </div>
      </header>
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {createRun.error ? <InlineAlert tone="error">{createRun.error.message}</InlineAlert> : null}
      <form className="filter-bar" onSubmit={submit}>
        <label>
          fixture
          <input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} />
        </label>
        <label>
          dataset
          <select
            value={evaluationDatasetId ?? ""}
            onChange={(event) =>
              setEvaluationDatasetId(event.target.value ? Number(event.target.value) : null)
            }
          >
            <option value="">fixture only</option>
            {datasets.data?.items.map((dataset) => (
              <option key={dataset.evaluation_dataset_id} value={dataset.evaluation_dataset_id}>
                {dataset.dataset_name}
              </option>
            ))}
          </select>
        </label>
        <div className="field-group">
          strategies
          <span className="inline-options">
            {(
              [
                "dense",
                "sparse",
                "hybrid",
                "graph_postgres",
                "graph_neo4j",
                "agentic_router",
                "llm_tool_orchestrator",
                "langchain_agentic",
                "langgraph_agentic"
              ] as EvaluationRunnableStrategy[]
            ).map((strategy) => (
              <label key={strategy}>
                <input
                  type="checkbox"
                  checked={strategies.includes(strategy)}
                  onChange={(event) => {
                    const next = event.target.checked
                      ? [...strategies, strategy]
                      : strategies.filter((item) => item !== strategy);
                    setStrategies(next.length ? next : ["dense"]);
                  }}
                />
                {strategy}
              </label>
            ))}
          </span>
        </div>
        <div className="field-group">
          cache modes
          <span className="inline-options">
            {(["default", "disabled", "cold", "warm"] as EvaluationCacheMode[]).map((mode) => (
              <label key={mode}>
                <input
                  type="checkbox"
                  checked={cacheModes.includes(mode)}
                  onChange={(event) => {
                    const next = event.target.checked
                      ? [...cacheModes, mode]
                      : cacheModes.filter((item) => item !== mode);
                    setCacheModes(next.length ? next : ["default"]);
                  }}
                />
                {mode}
              </label>
            ))}
          </span>
        </div>
        <label>
          case_limit
          <input
            type="number"
            min={1}
            max={50}
            value={caseLimit}
            onChange={(event) => setCaseLimit(Number(event.target.value))}
          />
        </label>
        <button type="submit" disabled={createRun.isPending}>
          Run evaluation
        </button>
        <button type="button" onClick={() => void runs.refetch()}>
          Refresh
        </button>
      </form>
      {runs.isLoading ? <LoadingState /> : null}
      {runs.error ? <ErrorState error={runs.error} /> : null}
      {runs.data?.items.length === 0 ? (
        <EmptyState title="No evaluation runs">No evaluation runs.</EmptyState>
      ) : null}
      {runs.data && runs.data.items.length > 0 ? (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Dataset</th>
                <th>Strategy</th>
                <th>Status</th>
                <th>Cases</th>
                <th>
                  <span className="metric-heading">
                    Metrics
                    <MetricHelp metricName="metric_summary" />
                  </span>
                </th>
                <th>Job</th>
                <th>Started</th>
                <th>Finished</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.items.map((run) => (
                <tr key={run.evaluation_run_id}>
                  <td>
                    <Link to={`/admin/evaluations/${run.evaluation_run_id}`}>#{run.evaluation_run_id}</Link>
                  </td>
                  <td>{truncateText(run.dataset_name, 32)}</td>
                  <td>{run.strategies.length ? run.strategies.join(", ") : run.strategy_type}</td>
                  <td>
                    <StatusBadge status={run.status} />
                  </td>
                  <td>
                    {run.succeeded_count}/{run.case_count}
                    {run.failed_count ? ` failed ${run.failed_count}` : ""}
                  </td>
                  <td>{formatMetricSummary(run.metric_summary)}</td>
                  <td>{run.job_id ? <Link to={`/admin/jobs/${run.job_id}`}>#{run.job_id}</Link> : "-"}</td>
                  <td>{formatDate(run.started_at)}</td>
                  <td>{formatDate(run.finished_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination meta={runs.data.pagination} onPageChange={updatePage} />
        </>
      ) : null}

      <section className="admin-section">
        <h2>Datasets</h2>
        {datasets.isLoading ? <LoadingState /> : null}
        {datasets.error ? <ErrorState error={datasets.error} /> : null}
        {datasets.data && datasets.data.items.length > 0 ? (
          <table className="admin-table">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Status</th>
                <th>Source</th>
                <th>Cases</th>
                <th>Version</th>
              </tr>
            </thead>
            <tbody>
              {datasets.data.items.map((dataset) => (
                <tr key={dataset.evaluation_dataset_id}>
                  <td>
                    <Link to={`/admin/evaluations/datasets/${dataset.evaluation_dataset_id}`}>
                      {truncateText(dataset.dataset_name, 40)}
                    </Link>
                  </td>
                  <td>
                    <StatusBadge status={dataset.status} />
                  </td>
                  <td>{dataset.source_type}</td>
                  <td>{dataset.case_count}</td>
                  <td>{dataset.version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </main>
  );
}

function formatMetricSummary(summary: Record<string, number>) {
  const entries = orderedMetricEntries(Object.entries(summary));
  if (!entries.length) {
    return "-";
  }
  return (
    <span className="metric-detail-list">
      {entries.map(([name, value]) => (
        <span className="metric-detail-item" key={name}>
          <span>{name}: {value.toFixed(2)}</span>
          <MetricHelp metricName={name} />
        </span>
      ))}
    </span>
  );
}
