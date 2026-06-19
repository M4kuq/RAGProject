import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { JobPayloadView } from "../../../components/admin/JobPayloadView";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { useJobDetail, useRetryJob } from "../../../features/jobs/jobHooks";
import { formatDate, formatSafeText, truncateText } from "../../../lib/format";

export function JobDetailPage() {
  const jobId = Number(useParams().jobId);
  const job = useJobDetail(jobId);
  const retry = useRetryJob();
  const [message, setMessage] = useState<string | null>(null);

  async function retryJob() {
    if (!window.confirm("この失敗ジョブを再実行しますか？")) {
      return;
    }
    try {
      const result = await retry.mutateAsync(jobId);
      setMessage(`再実行ジョブ #${result.job_id} を作成しました。`);
    } catch {
      setMessage(null);
    }
  }

  if (job.isLoading) {
    return (
      <main className="admin-main">
        <LoadingState label="ジョブ詳細を読み込んでいます..." />
      </main>
    );
  }

  if (job.error || !job.data) {
    return (
      <main className="admin-main">
        <ErrorState error={job.error ?? new Error("ジョブが見つかりません。")} />
      </main>
    );
  }

  const canRetry = job.data.status === "failed" && !job.data.active_retry_job_id;
  const hasErrorDetails = Boolean(job.data.error_code || job.data.error_message || job.data.status === "failed");

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>ジョブ #{job.data.job_id}</h1>
          <p className="muted">{truncateText(job.data.job_type, 80)}</p>
        </div>
        <button type="button" disabled={!canRetry || retry.isPending} onClick={() => void retryJob()}>
          {job.data.active_retry_job_id ? "再実行中" : "再実行"}
        </button>
      </header>
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {retry.error ? <InlineAlert tone="error">{retry.error.message}</InlineAlert> : null}
      {!canRetry && job.data.status === "failed" && job.data.active_retry_job_id ? (
        <InlineAlert>再実行ジョブ #{job.data.active_retry_job_id} がすでにあります。</InlineAlert>
      ) : null}
      <section className="admin-section">
        <h2>状態</h2>
        <dl className="detail-grid">
          <div>
            <dt>状態</dt>
            <dd>
              <StatusBadge status={job.data.status} />
            </dd>
          </div>
          <div>
            <dt>対象</dt>
            <dd>
              {job.data.target_type ?? "-"} {job.data.target_id ?? ""}
            </dd>
          </div>
          <div>
            <dt>再実行元</dt>
            <dd>{job.data.retry_of_job_id ?? job.data.source_job_id ?? "-"}</dd>
          </div>
          <div>
            <dt>再実行回数</dt>
            <dd>{job.data.retry_count}</dd>
          </div>
          <div>
            <dt>作成日時</dt>
            <dd>{formatDate(job.data.created_at)}</dd>
          </div>
          <div>
            <dt>開始日時</dt>
            <dd>{formatDate(job.data.started_at)}</dd>
          </div>
          <div>
            <dt>終了日時</dt>
            <dd>{formatDate(job.data.finished_at)}</dd>
          </div>
          <div>
            <dt>lease 期限</dt>
            <dd>{formatDate(job.data.lease_expires_at)}</dd>
          </div>
        </dl>
      </section>

      {hasErrorDetails ? (
        <section className="admin-section">
          <h2>エラー</h2>
          <p>{job.data.error_code ? truncateText(job.data.error_code, 160) : formatSafeText(job.data.error_message, 160)}</p>
        </section>
      ) : null}

      <section className="admin-section">
        <h2>安全な payload</h2>
        <JobPayloadView payload={job.data.payload_view.payload} />
      </section>

      <p>
        <Link to="/admin/jobs">ジョブ一覧へ戻る</Link>
      </p>
    </main>
  );
}
