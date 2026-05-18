import { useState } from "react";
import { Link } from "react-router-dom";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { EmptyState, ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import {
  useApproveDocumentVersion,
  useDocuments
} from "../../../features/documents/documentHooks";
import { formatDate, truncateText } from "../../../lib/format";

export function DocumentReviewPage() {
  const pending = useDocuments({ display_status: "pending_review", page: 1, page_size: 100 });
  const approve = useApproveDocumentVersion();
  const [message, setMessage] = useState<string | null>(null);

  async function approveVersion(logicalDocumentId: number, documentVersionId: number, alreadyActive: boolean) {
    if (!window.confirm(alreadyActive ? "This version is already active. Refresh state?" : "Approve this version?")) {
      return;
    }
    try {
      const result = await approve.mutateAsync({ logicalDocumentId, documentVersionId });
      setMessage(result.result_code === "already_active" ? "Already active." : "Approved. Ingest/index state updated.");
    } catch {
      setMessage(null);
    }
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>Review</h1>
          <p className="muted">Approve ready versions that are not active yet.</p>
        </div>
      </header>
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {approve.error ? <InlineAlert tone="error">{approve.error.message}</InlineAlert> : null}
      {pending.isLoading ? <LoadingState /> : null}
      {pending.error ? <ErrorState error={pending.error} /> : null}
      {pending.data?.items.length === 0 ? <EmptyState title="No pending review">No pending versions.</EmptyState> : null}
      {pending.data && pending.data.items.length > 0 ? (
        <table className="admin-table">
          <thead>
            <tr>
              <th>Document</th>
              <th>Version</th>
              <th>File</th>
              <th>Created</th>
              <th>Chunks</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {pending.data.items.map((document) => {
              const version = document.latest_version;
              const canApprove = version?.status === "ready" && version.display_status === "pending_review";
              return (
                <tr key={document.logical_document_id}>
                  <td>
                    <Link to={`/admin/documents/${document.logical_document_id}`}>
                      {truncateText(document.title || document.document_name, 48)}
                    </Link>
                  </td>
                  <td>{version ? `v${version.version_no}` : "-"}</td>
                  <td>{truncateText(version?.file_name, 40)}</td>
                  <td>{formatDate(version?.created_at)}</td>
                  <td>{version?.chunk_count ?? "-"}</td>
                  <td>
                    <StatusBadge status={version?.display_status ?? document.display_status} />
                  </td>
                  <td>
                    <button
                      type="button"
                      disabled={!version || !canApprove || approve.isPending}
                      onClick={() =>
                        version
                          ? void approveVersion(document.logical_document_id, version.document_version_id, version.is_active)
                          : undefined
                      }
                    >
                      Approve
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : null}
    </main>
  );
}
