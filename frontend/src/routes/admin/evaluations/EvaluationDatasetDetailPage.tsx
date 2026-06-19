import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import {
  useEvaluationCases,
  useEvaluationDataset,
  useExportEvaluationDataset
} from "../../../features/evaluations/evaluationHooks";
import { formatDate, formatSafeText, truncateText } from "../../../lib/format";

const PAGE_SIZE = 50;

export function EvaluationDatasetDetailPage() {
  const evaluationDatasetId = Number(useParams().evaluationDatasetId);
  const [exportText, setExportText] = useState<string | null>(null);
  const dataset = useEvaluationDataset(evaluationDatasetId);
  const cases = useEvaluationCases(evaluationDatasetId, { page: 1, page_size: PAGE_SIZE });
  const exported = useExportEvaluationDataset(evaluationDatasetId);

  async function exportManifest() {
    const result = await exported.refetch();
    if (result.data) {
      setExportText(JSON.stringify(result.data, null, 2));
    }
  }

  if (dataset.isLoading) {
    return (
      <main className="admin-main">
        <LoadingState label="dataset を読み込んでいます..." />
      </main>
    );
  }

  if (dataset.error || !dataset.data) {
    return (
      <main className="admin-main">
        <ErrorState error={dataset.error ?? new Error("評価 dataset が見つかりません。")} />
      </main>
    );
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>{truncateText(dataset.data.dataset_name, 80)}</h1>
          <p className="muted">{formatSafeText(dataset.data.description, 140)}</p>
        </div>
        <button type="button" onClick={() => void exportManifest()}>
          エクスポート
        </button>
      </header>

      {exported.error ? <InlineAlert tone="error">{exported.error.message}</InlineAlert> : null}
      {exportText ? (
        <section className="admin-section">
          <h2>エクスポート内容</h2>
          <pre className="payload-view">{exportText}</pre>
        </section>
      ) : null}

      <section className="admin-section">
        <h2>dataset 概要</h2>
        <dl className="detail-grid">
          <div>
            <dt>状態</dt>
            <dd>
              <StatusBadge status={dataset.data.status} />
            </dd>
          </div>
          <div>
            <dt>source</dt>
            <dd>{dataset.data.source_type}</dd>
          </div>
          <div>
            <dt>version</dt>
            <dd>{dataset.data.version}</dd>
          </div>
          <div>
            <dt>ケース</dt>
            <dd>{dataset.data.case_count}</dd>
          </div>
          <div>
            <dt>更新日時</dt>
            <dd>{formatDate(dataset.data.updated_at)}</dd>
          </div>
        </dl>
      </section>

      <section className="admin-section">
        <h2>ケース</h2>
        {cases.isLoading ? <LoadingState label="ケースを読み込んでいます..." /> : null}
        {cases.error ? <ErrorState error={cases.error} /> : null}
        {cases.data ? (
          <table className="admin-table">
            <thead>
              <tr>
                <th>case</th>
                <th>状態</th>
                <th>質問</th>
                <th>引用</th>
                <th>tags</th>
              </tr>
            </thead>
            <tbody>
              {cases.data.items.map((evaluationCase) => (
                <tr key={evaluationCase.evaluation_case_id}>
                  <td>{evaluationCase.case_key}</td>
                  <td>
                    <StatusBadge status={evaluationCase.status} />
                  </td>
                  <td>{truncateText(evaluationCase.question, 80)}</td>
                  <td>{evaluationCase.required_citation ? "必須" : "任意"}</td>
                  <td>{evaluationCase.tags.join(", ") || "-"}</td>
                </tr>
              ))}
              {cases.data.items.length === 0 ? (
                <tr>
                  <td colSpan={5}>ケースはありません。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        ) : null}
      </section>

      <p>
        <Link to="/admin/evaluations">評価一覧へ戻る</Link>
      </p>
    </main>
  );
}
