import { FormEvent, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { JobPayloadView } from "../../../components/admin/JobPayloadView";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { EmptyState, ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import { useJobs, useRetryJob } from "../../../features/jobs/jobHooks";
import { ApiError } from "../../../lib/apiClient";
import { formatDate, formatSafeText, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;

export function JobListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [jobTypeDraft, setJobTypeDraft] = useState(searchParams.get("job_type") ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const [retryingJobIds, setRetryingJobIds] = useState<Set<number>>(() => new Set());
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
    if (key !== "page") {
      next.set("page", "1");
    }
    setSearchParams(next);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    updateFilter("job_type", jobTypeDraft.trim());
  }

  async function retryJob(jobId: number) {
    if (!window.confirm("この失敗ジョブを再実行しますか？")) {
      return;
    }
    try {
      const result = await retry.mutateAsync(jobId);
      setRetryingJobIds((current) => new Set(current).add(jobId));
      setMessage(`再実行ジョブ #${result.job_id} を作成しました。`);
    } catch (error) {
      if (error instanceof ApiError && error.code === "job_active_retry_exists") {
        setRetryingJobIds((current) => new Set(current).add(jobId));
      }
      setMessage(null);
    }
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>ジョブ</h1>
          <p className="muted">取り込みや評価などの非同期処理を確認し、失敗したジョブを再実行できます。</p>
        </div>
      </header>
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {retry.error ? <InlineAlert tone="error">{retry.error.message}</InlineAlert> : null}
      <form className="filter-bar" onSubmit={submit}>
        <label>
          状態
          <select value={params.status} onChange={(event) => updateFilter("status", event.target.value)}>
            <option value="">すべて</option>
            <option value="queued">待機中</option>
            <option value="running">実行中</option>
            <option value="succeeded">成功</option>
            <option value="failed">失敗</option>
            <option value="canceled">中止</option>
          </select>
        </label>
        <label>
          ジョブ種別
          <input value={jobTypeDraft} onChange={(event) => setJobTypeDraft(event.target.value)} />
        </label>
        <button type="submit">絞り込む</button>
      </form>
      {jobs.isLoading ? <LoadingState label="ジョブを読み込んでいます..." /> : null}
      {jobs.error ? <ErrorState error={jobs.error} /> : null}
      {jobs.data?.items.length === 0 ? <EmptyState title="ジョブがありません">ドキュメント取り込みや評価を実行すると、ここに処理履歴が表示されます。</EmptyState> : null}
      {jobs.data && jobs.data.items.length > 0 ? (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th>ジョブ</th>
                <th>種別</th>
                <th>状態</th>
                <th>対象</th>
                <th>作成日時</th>
                <th>開始日時</th>
                <th>終了日時</th>
                <th>エラー</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.items.map((job) => {
                const retryKnownActive = retryingJobIds.has(job.job_id);
                const canRetry = job.status === "failed" && !retryKnownActive;
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
                    <td>{job.error_code ? truncateText(job.error_code, 40) : formatSafeText(job.error_message, 40)}</td>
                    <td>
                      <button type="button" disabled={!canRetry || retry.isPending} onClick={() => void retryJob(job.job_id)}>
                        {retryKnownActive ? "再実行待ち" : "再実行"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <section className="admin-section">
            <h2>安全な payload プレビュー</h2>
            <p className="muted">backend で redaction 済みの payload フィールドだけを表示します。</p>
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
