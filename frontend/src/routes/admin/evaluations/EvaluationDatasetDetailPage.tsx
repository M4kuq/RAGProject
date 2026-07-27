import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import {
  useEvaluationCases,
  useEvaluationCorpusReadiness,
  useEvaluationDataset,
  useExportEvaluationDataset,
  usePrepareEvaluationDatasetCorpus
} from "../../../features/evaluations/evaluationHooks";
import { formatDate, formatSafeText, truncateText } from "../../../lib/format";

const PAGE_SIZE = 50;

export function EvaluationDatasetDetailPage() {
  const evaluationDatasetId = Number(useParams().evaluationDatasetId);
  const [exportText, setExportText] = useState<string | null>(null);
  const dataset = useEvaluationDataset(evaluationDatasetId);
  const cases = useEvaluationCases(evaluationDatasetId, { page: 1, page_size: PAGE_SIZE });
  const exported = useExportEvaluationDataset(evaluationDatasetId);
  const readiness = useEvaluationCorpusReadiness(evaluationDatasetId);
  const prepareCorpus = usePrepareEvaluationDatasetCorpus();

  async function exportManifest() {
    const result = await exported.refetch();
    if (result.data) {
      setExportText(JSON.stringify(result.data, null, 2));
    }
  }

  if (dataset.isLoading) {
    return <main className="admin-main"><LoadingState label="dataset を読み込んでいます..." /></main>;
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
        <button type="button" onClick={() => void exportManifest()}>エクスポート</button>
      </header>

      {exported.error ? <InlineAlert tone="error">{exported.error.message}</InlineAlert> : null}
      {prepareCorpus.error ? <InlineAlert tone="error">{prepareCorpus.error.message}</InlineAlert> : null}
      {exportText ? (
        <section className="admin-section">
          <h2>エクスポート内容</h2>
          <pre className="payload-view">{exportText}</pre>
        </section>
      ) : null}

      <section className="admin-section">
        <h2>dataset 概要</h2>
        <dl className="detail-grid">
          <div><dt>状態</dt><dd><StatusBadge status={dataset.data.status} /></dd></div>
          <div><dt>source</dt><dd>{dataset.data.source_type}</dd></div>
          <div><dt>version</dt><dd>{dataset.data.version}</dd></div>
          <div><dt>ケース</dt><dd>{dataset.data.case_count}</dd></div>
          <div><dt>corpus mode</dt><dd>{dataset.data.corpus_mode}</dd></div>
          <div><dt>更新日時</dt><dd>{formatDate(dataset.data.updated_at)}</dd></div>
        </dl>
      </section>

      <section className="admin-section" aria-labelledby="corpus-readiness-title">
        <div className="section-header-row">
          <div>
            <h2 id="corpus-readiness-title">評価コーパス readiness</h2>
            <p className="section-help">
              dataset version専用のsourceだけを使う検証結果です。readyになるまで評価runは開始できません。
            </p>
          </div>
          {readiness.data?.corpus_mode === "isolated" && !readiness.data.ready ? (
            <button
              disabled={prepareCorpus.isPending}
              onClick={() => void prepareCorpus.mutateAsync(evaluationDatasetId)}
              type="button"
            >
              {prepareCorpus.isPending ? "再試行中..." : "不足・失敗分を再試行"}
            </button>
          ) : null}
        </div>
        {readiness.isLoading ? <LoadingState label="readinessを確認しています..." /> : null}
        {readiness.error ? <ErrorState error={readiness.error} /> : null}
        {readiness.data ? (
          <>
            <dl className="detail-grid">
              <div><dt>状態</dt><dd><StatusBadge status={readiness.data.corpus_status} /></dd></div>
              <div><dt>source</dt><dd>{readiness.data.ready_source_count}/{readiness.data.source_count}</dd></div>
              <div><dt>fact本文</dt><dd>{readiness.data.present_fact_count}/{readiness.data.fact_count}</dd></div>
              <div><dt>index</dt><dd>{readiness.data.index_count}</dd></div>
              <div><dt>fact隔離検索</dt><dd>{readiness.data.isolated_fact_retrieval_count}/{readiness.data.fact_count}</dd></div>
              <div><dt>answerable検索</dt><dd>{readiness.data.answerable_retrieval_count}/{readiness.data.answerable_case_count}</dd></div>
              <div><dt>coverage</dt><dd>{(readiness.data.coverage * 100).toFixed(1)}%</dd></div>
              <div><dt>fingerprint</dt><dd><code>{readiness.data.corpus_fingerprint?.slice(0, 20) ?? "shared_legacy"}</code></dd></div>
            </dl>
            {(readiness.data.failure_reasons ?? []).map((reason) => (
              <InlineAlert key={reason} tone="error">{reason}</InlineAlert>
            ))}
            {readiness.data.corpus_mode === "shared_legacy" ? (
              <InlineAlert tone="info">v1 datasetは従来のshared corpusを使用します。</InlineAlert>
            ) : null}
            {(readiness.data.sources ?? []).length ? (
              <table className="admin-table">
                <thead>
                  <tr><th>source</th><th>状態</th><th>fact</th><th>indexed chunk</th><th>失敗理由</th></tr>
                </thead>
                <tbody>
                  {(readiness.data.sources ?? []).map((source) => (
                    <tr key={source.source_key}>
                      <td>{source.source_key}</td>
                      <td><StatusBadge status={source.status} /></td>
                      <td>{source.fact_count}</td>
                      <td>{source.indexed_chunk_count}</td>
                      <td>{source.failure_code ?? "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="admin-section">
        <h2>ケース</h2>
        {cases.isLoading ? <LoadingState label="ケースを読み込んでいます..." /> : null}
        {cases.error ? <ErrorState error={cases.error} /> : null}
        {cases.data ? (
          <table className="admin-table">
            <thead><tr><th>case</th><th>状態</th><th>質問</th><th>引用</th><th>tags</th></tr></thead>
            <tbody>
              {cases.data.items.map((evaluationCase) => (
                <tr key={evaluationCase.evaluation_case_id}>
                  <td>{evaluationCase.case_key}</td>
                  <td><StatusBadge status={evaluationCase.status} /></td>
                  <td>{truncateText(evaluationCase.question, 80)}</td>
                  <td>{evaluationCase.required_citation ? "必須" : "任意"}</td>
                  <td>{evaluationCase.tags.join(", ") || "-"}</td>
                </tr>
              ))}
              {cases.data.items.length === 0 ? <tr><td colSpan={5}>ケースはありません。</td></tr> : null}
            </tbody>
          </table>
        ) : null}
      </section>

      <p><Link to="/admin/evaluations">評価一覧へ戻る</Link></p>
    </main>
  );
}
