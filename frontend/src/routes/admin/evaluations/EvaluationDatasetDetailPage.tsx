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
        <LoadingState />
      </main>
    );
  }

  if (dataset.error || !dataset.data) {
    return (
      <main className="admin-main">
        <ErrorState error={dataset.error ?? new Error("Evaluation dataset not found.")} />
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
          Export
        </button>
      </header>

      {exported.error ? <InlineAlert tone="error">{exported.error.message}</InlineAlert> : null}
      {exportText ? (
        <section className="admin-section">
          <h2>Export manifest</h2>
          <pre className="payload-view">{exportText}</pre>
        </section>
      ) : null}

      <section className="admin-section">
        <h2>Dataset</h2>
        <dl className="detail-grid">
          <div>
            <dt>Status</dt>
            <dd>
              <StatusBadge status={dataset.data.status} />
            </dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>{dataset.data.source_type}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd>{dataset.data.version}</dd>
          </div>
          <div>
            <dt>Cases</dt>
            <dd>{dataset.data.case_count}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{formatDate(dataset.data.updated_at)}</dd>
          </div>
        </dl>
      </section>

      <section className="admin-section">
        <h2>Cases</h2>
        {cases.isLoading ? <LoadingState /> : null}
        {cases.error ? <ErrorState error={cases.error} /> : null}
        {cases.data ? (
          <table className="admin-table">
            <thead>
              <tr>
                <th>Case</th>
                <th>Status</th>
                <th>Question</th>
                <th>Citation</th>
                <th>Tags</th>
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
                  <td>{evaluationCase.required_citation ? "required" : "optional"}</td>
                  <td>{evaluationCase.tags.join(", ") || "-"}</td>
                </tr>
              ))}
              {cases.data.items.length === 0 ? (
                <tr>
                  <td colSpan={5}>No cases.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        ) : null}
      </section>

      <p>
        <Link to="/admin/evaluations">Back to Evaluations</Link>
      </p>
    </main>
  );
}
