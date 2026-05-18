import { useState } from "react";
import { useParams } from "react-router-dom";
import { ChunkPreviewTable } from "../../../components/admin/ChunkPreviewTable";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { VersionList } from "../../../components/admin/VersionList";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import {
  useArchiveDocument,
  useDocumentChunks,
  useDocumentDetail,
  useUploadDocumentVersion
} from "../../../features/documents/documentHooks";
import { formatDate, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;

export function DocumentDetailPage() {
  const logicalDocumentId = Number(useParams().logicalDocumentId);
  const [message, setMessage] = useState<string | null>(null);
  const [chunkPage, setChunkPage] = useState(1);
  const [versionFile, setVersionFile] = useState<File | null>(null);
  const document = useDocumentDetail(logicalDocumentId);
  const archive = useArchiveDocument();
  const uploadVersion = useUploadDocumentVersion();
  const previewVersion = document.data?.active_version ?? document.data?.latest_version ?? null;
  const chunks = useDocumentChunks(logicalDocumentId, previewVersion?.document_version_id ?? NaN, chunkPage, PAGE_SIZE);

  async function archiveDocument() {
    if (!window.confirm("Archive this document?")) {
      return;
    }
    try {
      const result = await archive.mutateAsync(logicalDocumentId);
      setMessage(result.result_code === "already_archived" ? "Already archived." : "Archived.");
    } catch {
      setMessage(null);
    }
  }

  async function submitVersion() {
    if (!versionFile) {
      setMessage("Select a new version file.");
      return;
    }
    try {
      const result = await uploadVersion.mutateAsync({ logicalDocumentId, file: versionFile });
      setMessage(
        result.status === "duplicate_content_skipped"
          ? "Duplicate content skipped."
          : `New version uploaded. Job #${result.job_id}`
      );
      setVersionFile(null);
    } catch {
      setMessage(null);
    }
  }

  if (document.isLoading) {
    return (
      <main className="admin-main">
        <LoadingState />
      </main>
    );
  }

  if (document.error || !document.data) {
    return (
      <main className="admin-main">
        <ErrorState error={document.error ?? new Error("Document not found.")} />
      </main>
    );
  }

  const isArchived = document.data.status === "archived";

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>{truncateText(document.data.title || document.data.document_name, 80)}</h1>
          <p className="muted">Logical document #{document.data.logical_document_id}</p>
        </div>
        <button type="button" disabled={isArchived || archive.isPending} onClick={() => void archiveDocument()}>
          {isArchived ? "Already archived" : "Archive"}
        </button>
      </header>

      {message ? <InlineAlert tone={message.includes("Select") ? "error" : "success"}>{message}</InlineAlert> : null}
      {archive.error ? <InlineAlert tone="error">{archive.error.message}</InlineAlert> : null}
      {uploadVersion.error ? <InlineAlert tone="error">{uploadVersion.error.message}</InlineAlert> : null}

      <section className="admin-section">
        <h2>Metadata</h2>
        <dl className="detail-grid">
          <div>
            <dt>Status</dt>
            <dd>
              <StatusBadge status={document.data.status} />
            </dd>
          </div>
          <div>
            <dt>Display</dt>
            <dd>
              <StatusBadge status={document.data.display_status} />
            </dd>
          </div>
          <div>
            <dt>Active version</dt>
            <dd>{document.data.active_version ? `v${document.data.active_version.version_no}` : "-"}</dd>
          </div>
          <div>
            <dt>Latest version</dt>
            <dd>{document.data.latest_version ? `v${document.data.latest_version.version_no}` : "-"}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{formatDate(document.data.created_at)}</dd>
          </div>
          <div>
            <dt>Updated</dt>
            <dd>{formatDate(document.data.updated_at)}</dd>
          </div>
        </dl>
        {isArchived ? <InlineAlert>Archived documents are excluded from retrieval.</InlineAlert> : null}
      </section>

      <section className="admin-section">
        <h2>Upload New Version</h2>
        <div className="inline-form">
          <input aria-label="new version file" type="file" onChange={(event) => setVersionFile(event.target.files?.[0] ?? null)} />
          <button type="button" disabled={isArchived || uploadVersion.isPending} onClick={() => void submitVersion()}>
            Upload Version
          </button>
        </div>
      </section>

      <section className="admin-section">
        <h2>Versions</h2>
        <VersionList logicalDocumentId={logicalDocumentId} versions={document.data.versions} />
      </section>

      <section className="admin-section">
        <h2>Chunk Preview</h2>
        {previewVersion ? <p className="muted">Previewing version v{previewVersion.version_no}.</p> : null}
        {chunks.isLoading ? <LoadingState /> : null}
        {chunks.error ? <ErrorState error={chunks.error} /> : null}
        {chunks.data ? (
          <>
            <ChunkPreviewTable chunks={chunks.data.items} />
            <Pagination meta={chunks.data.pagination} onPageChange={setChunkPage} />
          </>
        ) : null}
      </section>
    </main>
  );
}
