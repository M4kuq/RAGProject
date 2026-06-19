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
    if (!window.confirm(alreadyArchived ? "このドキュメントはすでにアーカイブ済みです。状態を更新しますか？" : "このドキュメントをアーカイブしますか？")) {
      return;
    }
    try {
      const result = await archive.mutateAsync(logicalDocumentId);
      setMessage(result.result_code === "already_archived" ? "すでにアーカイブ済みです。" : "アーカイブしました。");
    } catch {
      setMessage(null);
    }
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>ドキュメント</h1>
          <p className="muted">アップロード済み文書、承認状況、取り込み状態を確認します。</p>
        </div>
        <Link className="button-link" to="/admin/documents/review">
          承認待ちを見る
        </Link>
      </header>

      <DocumentUploadForm onUploaded={(result) => setMessage(`アップロードを受け付けました。ジョブ #${result.job_id}`)} />
      <DocumentUrlIngestForm onIngested={(result) => setMessage(`URL 取り込みを受け付けました。ジョブ #${result.job_id}`)} />
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {archive.error ? <InlineAlert tone="error">{archive.error.message}</InlineAlert> : null}

      <form className="filter-bar" onSubmit={submitSearch}>
        <label>
          状態
          <select value={params.status} onChange={(event) => updateFilter("status", event.target.value)}>
            <option value="">すべて</option>
            <option value="active">有効</option>
            <option value="archived">アーカイブ</option>
          </select>
        </label>
        <label>
          表示状態
          <select
            value={params.display_status}
            onChange={(event) => updateFilter("display_status", event.target.value)}
          >
            <option value="">すべて</option>
            <option value="active">有効</option>
            <option value="pending_review">承認待ち</option>
            <option value="processing">処理中</option>
            <option value="failed">失敗</option>
            <option value="archived">アーカイブ</option>
          </select>
        </label>
        <label>
          キーワード
          <input value={qDraft} onChange={(event) => setQDraft(event.target.value)} />
        </label>
        <button type="submit">絞り込む</button>
      </form>

      {documents.isLoading ? <LoadingState label="ドキュメントを読み込んでいます..." /> : null}
      {documents.error ? <ErrorState error={documents.error} /> : null}
      {documents.data?.items.length === 0 ? (
        <EmptyState title="ドキュメントがありません">まずファイルまたは URL を取り込むと、ここに一覧が表示されます。</EmptyState>
      ) : null}
      {documents.data && documents.data.items.length > 0 ? (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th>タイトル</th>
                <th>状態</th>
                <th>表示状態</th>
                <th>有効版</th>
                <th>最新版</th>
                <th>更新日時</th>
                <th>作成日時</th>
                <th>操作</th>
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
                      <Link to={`/admin/documents/${document.logical_document_id}`}>詳細</Link>
                      <button
                        className="button-danger"
                        type="button"
                        disabled={archive.isPending}
                        onClick={() => void archiveDocument(document.logical_document_id, alreadyArchived)}
                      >
                        {alreadyArchived ? "アーカイブ済み" : "アーカイブ"}
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
