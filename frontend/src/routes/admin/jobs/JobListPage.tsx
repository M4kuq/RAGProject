import { FormEvent, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { JobPayloadView } from "../../../components/admin/JobPayloadView";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { EmptyState, ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import { useJobs, useRetryJob } from "../../../features/jobs/jobHooks";
import { formatDate, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;

export function JobListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [jobTypeDraft, setJobTypeDraft] = useState(searchParams.get("job_type") ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const params = useMemo(
    () => ({
      status: searchParams.get("status") ?? "",
      job_type: searchParams.get("job_type") ?? "",
      target_type: searchParams.get("target_type") ?? "",
      target_id: searchParams.get("target_id") ? Number(searchParams.get("target_id")) : undefined,
      page: Number(searchParams.get("page") ?? 1),
      page_size: PAGE_SIZE
    }),
    [searchParams]
  );
  const jobs = useJobs(params);
  const retry = useRetryJob();

  function updateFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    next.set("page", "1");
    setSearchParams(next);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    updateFilter("job_type", jobTypeDraft.trim());
  }

  async function retryJob(jobId: number) {
    if (!window.confirm("Retry this failed job?")) {
      return;
    }
    try {
      const result = await retry.mutateAsync(jobId);
      setMessage(`Retry created. New job #${result.job_id}`);
    } catch {
      setMessage(null);
    }
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>Jobs</h1>
          <p className="muted">Inspect asynchronous job status and retry failed jobs.</p>
        </div>
      </header>
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {retry.error ? <InlineAlert tone="error">{retry.error.message}</InlineAlert> : null}
      <form className="filter-bar" onSubmit={submit}>
        <label>
          status
          <select value={params.status} onChange={(event) => updateFilter("status", event.target.value)}>
            <option value="">All</option>
            <option value="queued">queued</option>
            <option value="running">running</option>
            <option value="succeeded">succeeded</option>
            <option value="failed">failed</option>
            <option value="canceled">canceled</option>
          </select>
        </label>
        <label>
          job_type
          <input value={jobTypeDraft} onChange={(event) => setJobTypeDraft(event.target.value)} />
        </label>
        <button type="submit">Filter</button>
      </form>
      {jobs.isLoading ? <LoadingState /> : null}
      {jobs.error ? <ErrorState error={jobs.error} /> : null}
      {jobs.data?.items.length === 0 ? <EmptyState title="No jobs">No jobs.</EmptyState> : null}
      {jobs.data && jobs.data.items.length > 0 ? (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Type</th>
                <th>Status</th>
                <th>Target</th>
                <th>Created</th>
                <th>Started</th>
                <th>Finished</th>
                <th>Error</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.items.map((job) => {
                const canRetry = job.status === "failed";
                return (
                  <tr key={job.job_id}>
                    <td>
                      <Link to={`/admin/jobs/${job.job_id}`}>#{job.job_id}</Link>
                    </td>
                    <td>{truncateText(job.job_type, 32)}</td>
                    <td>
                      <StatusBadge status={job.status} />
                    </td>
                    <td>
                      {job.target_type ?? "-"} {job.target_id ?? ""}
                    </td>
                    <td>{formatDate(job.created_at)}</td>
                    <td>{formatDate(job.started_at)}</td>
                    <td>{formatDate(job.finished_at)}</td>
                    <td>{truncateText(job.error_code ?? job.error_message, 40)}</td>
                    <td>
                      <button type="button" disabled={!canRetry || retry.isPending} onClick={() => void retryJob(job.job_id)}>
                        Retry
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <section className="admin-section">
            <h2>Payload Preview</h2>
            <p className="muted">Selected rows expose only backend redacted payload fields.</p>
            {jobs.data.items.slice(0, 1).map((job) => (
              <JobPayloadView key={job.job_id} payload={job.payload_view.payload} />
            ))}
          </section>
          <Pagination meta={jobs.data.pagination} onPageChange={(page) => updateFilter("page", String(page))} />
        </>
      ) : null}
    </main>
  );
}
