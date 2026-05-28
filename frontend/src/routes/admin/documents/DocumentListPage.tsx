import { FormEvent, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { DocumentUploadForm, DocumentUrlIngestForm } from "../../../components/admin/DocumentUploadForm";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { ErrorState, LoadingState, EmptyState, InlineAlert } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import { useArchiveDocument, useDocuments } from "../../../features/documents/documentHooks";
import { formatDate, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;

export function DocumentListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [qDraft, setQDraft] = useState(searchParams.get("q") ?? "");
  const [message, setMessage] = useState<string | null>(null);
  const params = useMemo(
    () => ({
      status: searchParams.get("status") ?? "",
      display_status: searchParams.get("display_status") ?? "",
      q: searchParams.get("q") ?? "",
      page: Number(searchParams.get("page") ?? 1),
      page_size: PAGE_SIZE
    }),
    [searchParams]
  );
  const documents = useDocuments(params);
  const archive = useArchiveDocument();

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

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    updateFilter("q", qDraft.trim());
  }

  async function archiveDocument(logicalDocumentId: number, alreadyArchived: boolean) {
    if (!window.confirm(alreadyArchived ? "This document is already archived. Refresh state?" : "Archive this document?")) {
      return;
    }
    try {
      const result = await archive.mutateAsync(logicalDocumentId);
      setMessage(result.result_code === "already_archived" ? "Already archived." : "Archived.");
    } catch {
      setMessage(null);
    }
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>Documents</h1>
          <p className="muted">Manage uploaded documents, review state, and ingest status.</p>
        </div>
        <Link className="button-link" to="/admin/documents/review">
          Review
        </Link>
      </header>

      <DocumentUploadForm onUploaded={(result) => setMessage(`Upload accepted. Job #${result.job_id}`)} />
      <DocumentUrlIngestForm onIngested={(result) => setMessage(`URL accepted. Job #${result.job_id}`)} />
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {archive.error ? <InlineAlert tone="error">{archive.error.message}</InlineAlert> : null}

      <form className="filter-bar" onSubmit={submitSearch}>
        <label>
          status
          <select value={params.status} onChange={(event) => updateFilter("status", event.target.value)}>
            <option value="">All</option>
            <option value="active">active</option>
            <option value="archived">archived</option>
          </select>
        </label>
        <label>
          display_status
          <select
            value={params.display_status}
            onChange={(event) => updateFilter("display_status", event.target.value)}
          >
            <option value="">All</option>
            <option value="active">active</option>
            <option value="pending_review">pending_review</option>
            <option value="processing">processing</option>
            <option value="failed">failed</option>
            <option value="archived">archived</option>
          </select>
        </label>
        <label>
          q
          <input value={qDraft} onChange={(event) => setQDraft(event.target.value)} />
        </label>
        <button type="submit">Filter</button>
      </form>

      {documents.isLoading ? <LoadingState /> : null}
      {documents.error ? <ErrorState error={documents.error} /> : null}
      {documents.data?.items.length === 0 ? <EmptyState title="No documents">No uploaded documents.</EmptyState> : null}
      {documents.data && documents.data.items.length > 0 ? (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Status</th>
                <th>Display</th>
                <th>Active</th>
                <th>Latest</th>
                <th>Updated</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {documents.data.items.map((document) => {
                const alreadyArchived = document.status === "archived";
                return (
                  <tr key={document.logical_document_id}>
                    <td>
                      <Link to={`/admin/documents/${document.logical_document_id}`}>
                        {truncateText(document.title || document.document_name, 48)}
                      </Link>
                    </td>
                    <td>
                      <StatusBadge status={document.status} />
                    </td>
                    <td>
                      <StatusBadge status={document.display_status} />
                    </td>
                    <td>{document.active_version ? `v${document.active_version.version_no}` : "-"}</td>
                    <td>{document.latest_version ? `v${document.latest_version.version_no}` : "-"}</td>
                    <td>{formatDate(document.updated_at)}</td>
                    <td>{formatDate(document.created_at)}</td>
                    <td className="actions">
                      <Link to={`/admin/documents/${document.logical_document_id}`}>Detail</Link>
                      <button
                        type="button"
                        disabled={archive.isPending}
                        onClick={() => void archiveDocument(document.logical_document_id, alreadyArchived)}
                      >
                        {alreadyArchived ? "Archived" : "Archive"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Pagination
            meta={documents.data.pagination}
            onPageChange={(page) => updateFilter("page", String(page))}
          />
        </>
      ) : null}
    </main>
  );
}
