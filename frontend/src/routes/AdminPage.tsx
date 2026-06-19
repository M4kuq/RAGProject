import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { AdminIcon, type AdminIconName } from "../components/admin/AdminIcon";
import { StatusBadge } from "../components/admin/StatusBadge";
import { formatCount } from "../components/admin/adminLabels";
import { useDocuments } from "../features/documents/documentHooks";
import { useEvaluationRuns } from "../features/evaluations/evaluationHooks";
import { useJobs } from "../features/jobs/jobHooks";
import { formatDate, truncateText } from "../lib/format";

export function AdminPage() {
  const documents = useDocuments({ page: 1, page_size: 1 });
  const pendingReviews = useDocuments({ display_status: "pending_review", page: 1, page_size: 1 });
  const jobs = useJobs({ page: 1, page_size: 5 });
  const evaluations = useEvaluationRuns({ page: 1, page_size: 5 });
  const latestJob = jobs.data?.items[0];
  const latestEvaluation = evaluations.data?.items[0];

  return (
    <main className="admin-main admin-dashboard">
      <header className="page-header admin-dashboard-hero">
        <div>
          <h1>ダッシュボード</h1>
          <p className="muted">
            ドキュメントの取り込み、承認、評価、検索デバッグ、ジョブ状況をここから確認できます。
          </p>
        </div>
        <Link className="button-link" to="/admin/documents">
          ドキュメントを管理
        </Link>
      </header>

      <section className="dashboard-metric-grid" aria-label="管理概要">
        <DashboardMetric
          icon="documents"
          label="ドキュメント"
          loading={documents.isLoading}
          to="/admin/documents"
          value={formatCount(documents.data?.pagination?.total)}
        />
        <DashboardMetric
          icon="review"
          label="承認待ち"
          loading={pendingReviews.isLoading}
          to="/admin/documents/review"
          value={formatCount(pendingReviews.data?.pagination?.total)}
        />
        <DashboardMetric
          icon="jobs"
          label="直近ジョブ"
          loading={jobs.isLoading}
          status={latestJob?.status}
          to="/admin/jobs"
          value={latestJob ? `#${latestJob.job_id}` : "-"}
        />
        <DashboardMetric
          icon="evaluations"
          label="直近評価"
          loading={evaluations.isLoading}
          status={latestEvaluation?.status}
          to="/admin/evaluations"
          value={latestEvaluation ? `#${latestEvaluation.evaluation_run_id}` : "-"}
        />
      </section>

      <section className="dashboard-grid">
        <FeatureCard
          description="アップロード、URL 取り込み、版管理、アーカイブを確認します。"
          icon="documents"
          title="ドキュメント"
          to="/admin/documents"
        />
        <FeatureCard
          description="準備ができた版を確認し、検索対象として有効化します。"
          icon="review"
          title="承認"
          to="/admin/documents/review"
        />
        <FeatureCard
          description="評価 run と dataset を確認します。"
          icon="evaluations"
          title="評価"
          to="/admin/evaluations"
        />
        <FeatureCard
          description="検索経路、score、graph trace、圧縮 trace を安全な範囲で確認します。"
          icon="debug"
          title="検索デバッグ"
          to="/admin/retrieval-debug"
        />
        <FeatureCard
          description="取り込みや評価など、非同期処理の状態と retry を確認します。"
          icon="jobs"
          title="ジョブ"
          to="/admin/jobs"
        />
      </section>

      <section className="dashboard-grid dashboard-grid-secondary">
        <RecentPanel title="最近のジョブ" to="/admin/jobs">
          {jobs.isLoading ? <p className="muted">ジョブを読み込んでいます...</p> : null}
          {jobs.error ? <p className="error">ジョブ一覧を取得できませんでした。</p> : null}
          {jobs.data && jobs.data.items.length === 0 ? (
            <p className="muted">まだジョブはありません。ドキュメントの取り込みや評価を実行すると表示されます。</p>
          ) : null}
          {jobs.data && jobs.data.items.length > 0 ? (
            <ul className="dashboard-recent-list">
              {jobs.data.items.slice(0, 4).map((job) => (
                <li key={job.job_id}>
                  <Link to={`/admin/jobs/${job.job_id}`}>#{job.job_id}</Link>
                  <span>{truncateText(job.job_type, 28)}</span>
                  <StatusBadge status={job.status} />
                  <small>{formatDate(job.created_at)}</small>
                </li>
              ))}
            </ul>
          ) : null}
        </RecentPanel>

        <RecentPanel title="最近の評価" to="/admin/evaluations">
          {evaluations.isLoading ? <p className="muted">評価 run を読み込んでいます...</p> : null}
          {evaluations.error ? <p className="error">評価一覧を取得できませんでした。</p> : null}
          {evaluations.data && evaluations.data.items.length === 0 ? (
            <p className="muted">まだ評価 run はありません。評価カードから実行できます。</p>
          ) : null}
          {evaluations.data && evaluations.data.items.length > 0 ? (
            <ul className="dashboard-recent-list">
              {evaluations.data.items.slice(0, 4).map((run) => (
                <li key={run.evaluation_run_id}>
                  <Link to={`/admin/evaluations/${run.evaluation_run_id}`}>#{run.evaluation_run_id}</Link>
                  <span>{truncateText(run.dataset_name, 28)}</span>
                  <StatusBadge status={run.status} />
                  <small>{formatDate(run.created_at)}</small>
                </li>
              ))}
            </ul>
          ) : null}
        </RecentPanel>
      </section>
    </main>
  );
}

function DashboardMetric({
  icon,
  label,
  loading,
  status,
  to,
  value
}: {
  icon: AdminIconName;
  label: string;
  loading: boolean;
  status?: string;
  to: string;
  value: string;
}) {
  return (
    <Link className="dashboard-metric-card" to={to}>
      <span className="dashboard-card-icon">
        <AdminIcon name={icon} />
      </span>
      <span>
        <span className="dashboard-metric-label">{label}</span>
        <strong>{loading ? "..." : value}</strong>
        {status ? <StatusBadge status={status} /> : null}
      </span>
    </Link>
  );
}

function FeatureCard({
  children,
  description,
  icon,
  title,
  to
}: {
  children?: ReactNode;
  description: string;
  icon: AdminIconName;
  title: string;
  to: string;
}) {
  return (
    <article className="dashboard-feature-card">
      <span className="dashboard-card-icon">
        <AdminIcon name={icon} />
      </span>
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      <div className="dashboard-card-actions">
        <Link to={to}>開く</Link>
        {children}
      </div>
    </article>
  );
}

function RecentPanel({ children, title, to }: { children: ReactNode; title: string; to: string }) {
  return (
    <section className="dashboard-recent-panel">
      <div className="section-title-row">
        <h2>{title}</h2>
        <Link to={to}>すべて見る</Link>
      </div>
      {children}
    </section>
  );
}
