import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ChunkPreviewTable } from "../../../components/admin/ChunkPreviewTable";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import {
  useApproveDocumentVersion,
  useDocumentChunks,
  useDocumentVersionDetail
} from "../../../features/documents/documentHooks";
import { formatBytes, formatDate, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;

export function VersionDetailPage() {
  const logicalDocumentId = Number(useParams().logicalDocumentId);
  const documentVersionId = Number(useParams().documentVersionId);
  const [message, setMessage] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const version = useDocumentVersionDetail(logicalDocumentId, documentVersionId);
  const chunks = useDocumentChunks(logicalDocumentId, documentVersionId, page, PAGE_SIZE);
  const approve = useApproveDocumentVersion();

  async function approveVersion() {
    if (!window.confirm("Make this version active?")) {
      return;
    }
    try {
      const result = await approve.mutateAsync({ logicalDocumentId, documentVersionId });
      setMessage(result.result_code === "already_active" ? "Already active." : "Approved.");
    } catch {
      setMessage(null);
    }
  }

  const canApprove = version.data?.status === "ready" && version.data.display_status === "pending_review";

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>Version Detail</h1>
          <p className="muted">
            <Link to={`/admin/documents/${logicalDocumentId}`}>Document #{logicalDocumentId}</Link>
          </p>
        </div>
        <button type="button" disabled={!canApprove || approve.isPending} onClick={() => void approveVersion()}>
          {version.data?.is_active ? "Already active" : "Approve"}
        </button>
      </header>

      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {approve.error ? <InlineAlert tone="error">{approve.error.message}</InlineAlert> : null}
      {version.isLoading ? <LoadingState /> : null}
      {version.error ? <ErrorState error={version.error} /> : null}
      {version.data ? (
        <section className="admin-section">
          <h2>Version</h2>
          <dl className="detail-grid">
            <div>
              <dt>Version</dt>
              <dd>v{version.data.version_no}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>
                <StatusBadge status={version.data.status} />
              </dd>
            </div>
            <div>
              <dt>Display</dt>
              <dd>
                <StatusBadge status={version.data.display_status} />
              </dd>
            </div>
            <div>
              <dt>File</dt>
              <dd>{truncateText(version.data.file_name, 60)}</dd>
            </div>
            <div>
              <dt>MIME</dt>
              <dd>{version.data.mime_type ?? "-"}</dd>
            </div>
            <div>
              <dt>Size</dt>
              <dd>{formatBytes(version.data.file_size_bytes)}</dd>
            </div>
            <div>
              <dt>Chunks</dt>
              <dd>{version.data.chunk_count ?? "-"}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{formatDate(version.data.created_at)}</dd>
            </div>
          </dl>
          {!canApprove && !version.data.is_active ? <InlineAlert>Only ready pending_review versions can be approved.</InlineAlert> : null}
        </section>
      ) : null}

      <section className="admin-section">
        <h2>Chunks</h2>
        {chunks.isLoading ? <LoadingState /> : null}
        {chunks.error ? <ErrorState error={chunks.error} /> : null}
        {chunks.data ? (
          <>
            <ChunkPreviewTable chunks={chunks.data.items} />
            <Pagination meta={chunks.data.pagination} onPageChange={setPage} />
          </>
        ) : null}
      </section>
    </main>
  );
}
