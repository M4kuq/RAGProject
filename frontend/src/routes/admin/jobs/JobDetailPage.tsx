import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { JobPayloadView } from "../../../components/admin/JobPayloadView";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { useJobDetail, useRetryJob } from "../../../features/jobs/jobHooks";
import { formatDate, truncateText } from "../../../lib/format";

export function JobDetailPage() {
  const jobId = Number(useParams().jobId);
  const job = useJobDetail(jobId);
  const retry = useRetryJob();
  const [message, setMessage] = useState<string | null>(null);

  async function retryJob() {
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

  if (job.isLoading) {
    return (
      <main className="admin-main">
        <LoadingState />
      </main>
    );
  }

  if (job.error || !job.data) {
    return (
      <main className="admin-main">
        <ErrorState error={job.error ?? new Error("Job not found.")} />
      </main>
    );
  }

  const canRetry = job.data.status === "failed" && !job.data.active_retry_job_id;

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>Job #{job.data.job_id}</h1>
          <p className="muted">{truncateText(job.data.job_type, 80)}</p>
        </div>
        <button type="button" disabled={!canRetry || retry.isPending} onClick={() => void retryJob()}>
          {job.data.active_retry_job_id ? "Retry active" : "Retry"}
        </button>
      </header>
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {retry.error ? <InlineAlert tone="error">{retry.error.message}</InlineAlert> : null}
      {!canRetry && job.data.status === "failed" && job.data.active_retry_job_id ? (
        <InlineAlert>Active retry exists: job #{job.data.active_retry_job_id}</InlineAlert>
      ) : null}
      <section className="admin-section">
        <h2>Status</h2>
        <dl className="detail-grid">
          <div>
            <dt>Status</dt>
            <dd>
              <StatusBadge status={job.data.status} />
            </dd>
          </div>
          <div>
            <dt>Target</dt>
            <dd>
              {job.data.target_type ?? "-"} {job.data.target_id ?? ""}
            </dd>
          </div>
          <div>
            <dt>Retry of</dt>
            <dd>{job.data.retry_of_job_id ?? job.data.source_job_id ?? "-"}</dd>
          </div>
          <div>
            <dt>Retry count</dt>
            <dd>{job.data.retry_count}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{formatDate(job.data.created_at)}</dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd>{formatDate(job.data.started_at)}</dd>
          </div>
          <div>
            <dt>Finished</dt>
            <dd>{formatDate(job.data.finished_at)}</dd>
          </div>
          <div>
            <dt>Lease expires</dt>
            <dd>{formatDate(job.data.lease_expires_at)}</dd>
          </div>
        </dl>
      </section>

      <section className="admin-section">
        <h2>Error</h2>
        <p>{truncateText(job.data.error_code ?? job.data.error_message, 160)}</p>
      </section>

      <section className="admin-section">
        <h2>Safe Payload</h2>
        <JobPayloadView payload={job.data.payload_view.payload} />
      </section>

      <p>
        <Link to="/admin/jobs">Back to Jobs</Link>
      </p>
    </main>
  );
}
