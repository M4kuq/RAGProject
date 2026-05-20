import { FormEvent, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { EmptyState, ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import { useCreateEvaluationRun, useEvaluationRuns } from "../../../features/evaluations/evaluationHooks";
import { formatDate, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;

export function EvaluationListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [datasetName, setDatasetName] = useState("phase1_smoke");
  const [caseLimit, setCaseLimit] = useState(10);
  const [message, setMessage] = useState<string | null>(null);
  const params = useMemo(
    () => ({
      page: Number(searchParams.get("page") ?? 1),
      page_size: PAGE_SIZE
    }),
    [searchParams]
  );
  const runs = useEvaluationRuns(params);
  const createRun = useCreateEvaluationRun();

  function updatePage(page: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(page));
    setSearchParams(next);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const safeCaseLimit = Number.isFinite(caseLimit) ? Math.min(50, Math.max(1, caseLimit)) : 10;
    const result = await createRun.mutateAsync({
      dataset_name: datasetName.trim() || "phase1_smoke",
      case_limit: safeCaseLimit
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
          dataset
          <input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} />
        </label>
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
                <th>Status</th>
                <th>Cases</th>
                <th>Metrics</th>
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
    </main>
  );
}

function formatMetricSummary(summary: Record<string, number>) {
  const entries = Object.entries(summary);
  if (!entries.length) {
    return "-";
  }
  return entries.map(([name, value]) => `${name}: ${value.toFixed(2)}`).join(", ");
}
