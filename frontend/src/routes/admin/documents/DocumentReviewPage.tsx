import { useState } from "react";
import { Link } from "react-router-dom";
import { StatusBadge } from "../../../components/admin/StatusBadge";
import { EmptyState, ErrorState, InlineAlert, LoadingState } from "../../../components/common/States";
import { Pagination } from "../../../components/common/Pagination";
import {
  useApproveDocumentVersion,
  useDocuments
} from "../../../features/documents/documentHooks";
import { formatDate, truncateText } from "../../../lib/format";

const PAGE_SIZE = 20;

export function DocumentReviewPage() {
  const [page, setPage] = useState(1);
  const pending = useDocuments({ display_status: "pending_review", page, page_size: PAGE_SIZE });
  const approve = useApproveDocumentVersion();
  const [message, setMessage] = useState<string | null>(null);

  async function approveVersion(logicalDocumentId: number, documentVersionId: number, alreadyActive: boolean) {
    if (!window.confirm(alreadyActive ? "この版はすでに有効です。状態を更新しますか？" : "この版を承認して有効化しますか？")) {
      return;
    }
    try {
      const result = await approve.mutateAsync({ logicalDocumentId, documentVersionId });
      setMessage(result.result_code === "already_active" ? "すでに有効です。" : "承認しました。検索対象の状態も更新されます。");
    } catch {
      setMessage(null);
    }
  }

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>承認</h1>
          <p className="muted">準備完了だがまだ有効化されていない版を確認します。</p>
        </div>
      </header>
      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {approve.error ? <InlineAlert tone="error">{approve.error.message}</InlineAlert> : null}
      {pending.isLoading ? <LoadingState label="承認待ちを読み込んでいます..." /> : null}
      {pending.error ? <ErrorState error={pending.error} /> : null}
      {pending.data?.items.length === 0 ? (
        <EmptyState title="承認待ちはありません">新しい版の取り込みが完了すると、ここから有効化できます。</EmptyState>
      ) : null}
      {pending.data && pending.data.items.length > 0 ? (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th>ドキュメント</th>
                <th>版</th>
                <th>ファイル</th>
                <th>作成日時</th>
                <th>チャンク</th>
                <th>状態</th>
                <th>操作</th>
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
                        承認する
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Pagination meta={pending.data.pagination} onPageChange={setPage} />
        </>
      ) : null}
    </main>
  );
}
