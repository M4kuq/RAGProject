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
    if (!window.confirm("この版を有効化しますか？")) {
      return;
    }
    try {
      const result = await approve.mutateAsync({ logicalDocumentId, documentVersionId });
      setMessage(result.result_code === "already_active" ? "すでに有効です。" : "承認しました。");
    } catch {
      setMessage(null);
    }
  }

  const canApprove = version.data?.status === "ready" && version.data.display_status === "pending_review";

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>版の詳細</h1>
          <p className="muted">
            <Link to={`/admin/documents/${logicalDocumentId}`}>ドキュメント #{logicalDocumentId}</Link>
          </p>
        </div>
        <button type="button" disabled={!canApprove || approve.isPending} onClick={() => void approveVersion()}>
          {version.data?.is_active ? "有効化済み" : "承認する"}
        </button>
      </header>

      {message ? <InlineAlert tone="success">{message}</InlineAlert> : null}
      {approve.error ? <InlineAlert tone="error">{approve.error.message}</InlineAlert> : null}
      {version.isLoading ? <LoadingState label="版の詳細を読み込んでいます..." /> : null}
      {version.error ? <ErrorState error={version.error} /> : null}
      {version.data ? (
        <section className="admin-section">
          <h2>版情報</h2>
          <dl className="detail-grid">
            <div>
              <dt>版</dt>
              <dd>v{version.data.version_no}</dd>
            </div>
            <div>
              <dt>状態</dt>
              <dd>
                <StatusBadge status={version.data.status} />
              </dd>
            </div>
            <div>
              <dt>表示状態</dt>
              <dd>
                <StatusBadge status={version.data.display_status} />
              </dd>
            </div>
            <div>
              <dt>ファイル</dt>
              <dd>{truncateText(version.data.file_name, 60)}</dd>
            </div>
            <div>
              <dt>MIME</dt>
              <dd>{version.data.mime_type ?? "-"}</dd>
            </div>
            <div>
              <dt>サイズ</dt>
              <dd>{formatBytes(version.data.file_size_bytes)}</dd>
            </div>
            <div>
              <dt>チャンク</dt>
              <dd>{version.data.chunk_count ?? "-"}</dd>
            </div>
            <div>
              <dt>作成日時</dt>
              <dd>{formatDate(version.data.created_at)}</dd>
            </div>
          </dl>
          {!canApprove && !version.data.is_active ? <InlineAlert>承認できるのは ready かつ pending_review の版だけです。</InlineAlert> : null}
        </section>
      ) : null}

      <section className="admin-section">
        <h2>チャンク</h2>
        {chunks.isLoading ? <LoadingState label="チャンクを読み込んでいます..." /> : null}
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
