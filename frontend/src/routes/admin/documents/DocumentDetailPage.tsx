import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ChunkPreviewTable } from "../../../components/admin/ChunkPreviewTable";
import { validateDocumentFile } from "../../../components/admin/DocumentUploadForm";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { VersionList } from "../../../components/admin/VersionList";
import { ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import {
  useArchiveDocument,
  useDocumentChunks,
  useDocumentDetail,
  useDocumentVersionCompare,
  useUploadDocumentVersion
} from "../../../features/documents/documentHooks";
import type { DocumentChunkDiffItem, DocumentMetadataDiffItem } from "../../../features/documents/documentTypes";
import { formatDate, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;

export function DocumentDetailPage() {
  const logicalDocumentId = Number(useParams().logicalDocumentId);
  const [message, setMessage] = useState<string | null>(null);
  const [chunkPage, setChunkPage] = useState(1);
  const [versionFile, setVersionFile] = useState<File | null>(null);
  const [baseVersionId, setBaseVersionId] = useState<number | null>(null);
  const [targetVersionId, setTargetVersionId] = useState<number | null>(null);
  const [compareRequested, setCompareRequested] = useState(false);
  const document = useDocumentDetail(logicalDocumentId);
  const archive = useArchiveDocument();
  const uploadVersion = useUploadDocumentVersion();
  const previewVersion = document.data?.active_version ?? document.data?.latest_version ?? null;
  const chunks = useDocumentChunks(logicalDocumentId, previewVersion?.document_version_id ?? NaN, chunkPage, PAGE_SIZE);
  const documentVersions = document.data?.versions ?? [];
  const defaultTargetVersionId = documentVersions[0]?.document_version_id ?? null;
  const defaultBaseVersionId = documentVersions[1]?.document_version_id ?? defaultTargetVersionId;
  const versionSelectionKey = `${logicalDocumentId}:${documentVersions.map((version) => version.document_version_id).join(",")}`;
  const compare = useDocumentVersionCompare(logicalDocumentId, baseVersionId, targetVersionId, compareRequested);

  useEffect(() => {
    setTargetVersionId(defaultTargetVersionId);
    setBaseVersionId(defaultBaseVersionId);
    setCompareRequested(false);
  }, [defaultBaseVersionId, defaultTargetVersionId, versionSelectionKey]);

  function changeBaseVersion(value: string) {
    setBaseVersionId(Number(value));
    setCompareRequested(false);
  }

  function changeTargetVersion(value: string) {
    setTargetVersionId(Number(value));
    setCompareRequested(false);
  }

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
    const fileError = validateDocumentFile(versionFile);
    if (fileError) {
      setMessage(fileError);
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

      {message ? (
        <InlineAlert tone={message.includes("Select") || message.includes("Allowed") || message.includes("File size") ? "error" : "success"}>
          {message}
        </InlineAlert>
      ) : null}
      {archive.error ? <InlineAlert tone="error">{archive.error.message}</InlineAlert> : null}
      {uploadVersion.error ? <InlineAlert tone="error">{uploadVersion.error.message}</InlineAlert> : null}

      <nav className="document-detail-tabs" aria-label="Document detail sections">
        <a href="#document-metadata">Metadata</a>
        <a href="#document-upload-version">Upload Version</a>
        <a href="#document-versions">Versions</a>
        <a href="#version-compare">Version Compare</a>
        <a href="#chunk-preview">Chunk Preview</a>
      </nav>

      <section className="admin-section" id="document-metadata">
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

      <section className="admin-section" id="document-upload-version">
        <h2>Upload New Version</h2>
        <div className="inline-form">
          <input aria-label="new version file" type="file" onChange={(event) => setVersionFile(event.target.files?.[0] ?? null)} />
          <button type="button" disabled={isArchived || uploadVersion.isPending} onClick={() => void submitVersion()}>
            Upload Version
          </button>
        </div>
      </section>

      <section className="admin-section" id="document-versions">
        <h2>Versions</h2>
        <VersionList logicalDocumentId={logicalDocumentId} versions={document.data.versions} />
      </section>

      <section className="admin-section" id="version-compare">
        <h2>Version Compare</h2>
        <p className="muted">
          Compare version metadata and bounded source previews. Full source bodies are not shown.
        </p>
        <div className="inline-form">
          <label>
            Base
            <select
              aria-label="base version"
              value={baseVersionId ?? ""}
              onChange={(event) => changeBaseVersion(event.target.value)}
            >
              {documentVersions.map((version) => (
                <option key={version.document_version_id} value={version.document_version_id}>
                  v{version.version_no} {version.is_active ? "(active)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            Target
            <select
              aria-label="target version"
              value={targetVersionId ?? ""}
              onChange={(event) => changeTargetVersion(event.target.value)}
            >
              {documentVersions.map((version) => (
                <option key={version.document_version_id} value={version.document_version_id}>
                  v{version.version_no} {version.is_active ? "(active)" : ""}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={baseVersionId === null || targetVersionId === null || compare.isLoading}
            onClick={() => setCompareRequested(true)}
          >
            Compare versions
          </button>
        </div>
        {!compareRequested ? <p className="muted">Select versions and run compare to load the diff.</p> : null}
        {compareRequested && compare.isLoading ? <LoadingState label="Loading version diff..." /> : null}
        {compareRequested && compare.error ? <ErrorState error={compare.error} /> : null}
        {compareRequested && compare.data ? (
          <>
            <DiffSummary summary={compare.data.summary} />
            <MetadataDiffTable items={compare.data.metadata_diff.filter((item) => item.changed)} />
            <ChunkDiffTable items={compare.data.chunk_diff_items} />
            {compare.data.summary.diff_items_truncated ? (
              <InlineAlert>Diff items are truncated. Narrow the versions or inspect chunks directly.</InlineAlert>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="admin-section" id="chunk-preview">
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

function DiffSummary({ summary }: { summary: { added_chunks: number; removed_chunks: number; changed_chunks: number; unchanged_chunks: number } }) {
  return (
    <dl className="detail-grid compact-grid">
      <div>
        <dt>Added</dt>
        <dd>{summary.added_chunks}</dd>
      </div>
      <div>
        <dt>Removed</dt>
        <dd>{summary.removed_chunks}</dd>
      </div>
      <div>
        <dt>Changed</dt>
        <dd>{summary.changed_chunks}</dd>
      </div>
      <div>
        <dt>Unchanged</dt>
        <dd>{summary.unchanged_chunks}</dd>
      </div>
    </dl>
  );
}

function MetadataDiffTable({ items }: { items: DocumentMetadataDiffItem[] }) {
  if (items.length === 0) {
    return <p className="muted">No metadata changes.</p>;
  }
  return (
    <table className="admin-table compact-table">
      <thead>
        <tr>
          <th>Metadata</th>
          <th>Base</th>
          <th>Target</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.field}>
            <td>{item.field}</td>
            <td>{formatDiffValue(item.base_value)}</td>
            <td>{formatDiffValue(item.target_value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ChunkDiffTable({ items }: { items: DocumentChunkDiffItem[] }) {
  if (items.length === 0) {
    return <p className="muted">No chunk diff items.</p>;
  }
  return (
    <table className="admin-table compact-table">
      <thead>
        <tr>
          <th>Type</th>
          <th>Source</th>
          <th>Base Preview</th>
          <th>Target Preview</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item, index) => {
          const source = item.target_chunk ?? item.base_chunk;
          return (
            <tr key={`${item.diff_type}-${source?.document_chunk_id ?? index}`}>
              <td>{item.diff_type}</td>
              <td>
                {source ? (
                  <>
                    <strong>{truncateText(source.source_label, 80)}</strong>
                    <div className="muted">{sourceLocator(source)}</div>
                  </>
                ) : (
                  "-"
                )}
              </td>
              <td>{item.base_chunk ? truncateText(item.base_chunk.preview, 240) : "-"}</td>
              <td>{item.target_chunk ? truncateText(item.target_chunk.preview, 240) : "-"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function formatDiffValue(value: string | number | boolean | null): string {
  if (value === null) {
    return "-";
  }
  return String(value);
}

function sourceLocator(source: NonNullable<DocumentChunkDiffItem["base_chunk"]>): string {
  const parts = [
    source.page_from ? `p.${source.page_from}${source.page_to && source.page_to !== source.page_from ? `-${source.page_to}` : ""}` : null,
    source.sheet_name ? `Sheet: ${source.sheet_name}` : null,
    source.row_from !== null && source.row_to !== null ? `Rows ${source.row_from}-${source.row_to}` : null,
    source.slide_number !== null ? `Slide ${source.slide_number}` : null,
    source.html_heading_path,
    source.xml_path
  ].filter(Boolean);
  return parts.join(" / ") || `chunk ${source.chunk_index}`;
}
