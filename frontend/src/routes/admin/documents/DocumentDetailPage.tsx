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
    if (!window.confirm("このドキュメントをアーカイブしますか？")) {
      return;
    }
    try {
      const result = await archive.mutateAsync(logicalDocumentId);
      setMessage(result.result_code === "already_archived" ? "すでにアーカイブ済みです。" : "アーカイブしました。");
    } catch {
      setMessage(null);
    }
  }

  async function submitVersion() {
    if (!versionFile) {
      setMessage("新しい版のファイルを選択してください。");
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
          ? "同じ内容のため新しい版は作成しませんでした。"
          : `新しい版をアップロードしました。ジョブ #${result.job_id}`
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
        <ErrorState error={document.error ?? new Error("ドキュメントが見つかりません。")} />
      </main>
    );
  }

  const isArchived = document.data.status === "archived";

  return (
    <main className="admin-main">
      <header className="page-header">
        <div>
          <h1>{truncateText(document.data.title || document.data.document_name, 80)}</h1>
          <p className="muted">ドキュメント ID #{document.data.logical_document_id}</p>
        </div>
        <button className="button-danger" type="button" disabled={isArchived || archive.isPending} onClick={() => void archiveDocument()}>
          {isArchived ? "アーカイブ済み" : "アーカイブ"}
        </button>
      </header>

      {message ? (
        <InlineAlert tone={message.includes("選択") || message.includes("利用できる") || message.includes("ファイルサイズ") ? "error" : "success"}>
          {message}
        </InlineAlert>
      ) : null}
      {archive.error ? <InlineAlert tone="error">{archive.error.message}</InlineAlert> : null}
      {uploadVersion.error ? <InlineAlert tone="error">{uploadVersion.error.message}</InlineAlert> : null}

      <nav className="document-detail-tabs" aria-label="ドキュメント詳細セクション">
        <a href="#document-metadata">メタデータ</a>
        <a href="#document-upload-version">版を追加</a>
        <a href="#document-versions">版一覧</a>
        <a href="#version-compare">版の比較</a>
        <a href="#chunk-preview">チャンク確認</a>
      </nav>

      <section className="admin-section" id="document-metadata">
        <h2>メタデータ</h2>
        <dl className="detail-grid">
          <div>
            <dt>状態</dt>
            <dd>
              <StatusBadge status={document.data.status} />
            </dd>
          </div>
          <div>
            <dt>表示状態</dt>
            <dd>
              <StatusBadge status={document.data.display_status} />
            </dd>
          </div>
          <div>
            <dt>有効版</dt>
            <dd>{document.data.active_version ? `v${document.data.active_version.version_no}` : "-"}</dd>
          </div>
          <div>
            <dt>最新版</dt>
            <dd>{document.data.latest_version ? `v${document.data.latest_version.version_no}` : "-"}</dd>
          </div>
          <div>
            <dt>作成日時</dt>
            <dd>{formatDate(document.data.created_at)}</dd>
          </div>
          <div>
            <dt>更新日時</dt>
            <dd>{formatDate(document.data.updated_at)}</dd>
          </div>
        </dl>
        {isArchived ? <InlineAlert>アーカイブ済みドキュメントは検索対象から除外されます。</InlineAlert> : null}
      </section>

      <section className="admin-section" id="document-upload-version">
        <h2>新しい版をアップロード</h2>
        <div className="inline-form">
          <input aria-label="新しい版のファイル" type="file" onChange={(event) => setVersionFile(event.target.files?.[0] ?? null)} />
          <button type="button" disabled={isArchived || uploadVersion.isPending} onClick={() => void submitVersion()}>
            版をアップロード
          </button>
        </div>
      </section>

      <section className="admin-section" id="document-versions">
        <h2>版一覧</h2>
        <VersionList logicalDocumentId={logicalDocumentId} versions={document.data.versions} />
      </section>

      <section className="admin-section" id="version-compare">
        <h2>版の比較</h2>
        <p className="muted">
          版ごとのメタデータと短い出典プレビューだけを比較します。本文全体は表示しません。
        </p>
        <div className="inline-form">
          <label>
            比較元
            <select
              aria-label="base version"
              value={baseVersionId ?? ""}
              onChange={(event) => changeBaseVersion(event.target.value)}
            >
              {documentVersions.map((version) => (
                <option key={version.document_version_id} value={version.document_version_id}>
                  v{version.version_no} {version.is_active ? "(有効)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            比較先
            <select
              aria-label="target version"
              value={targetVersionId ?? ""}
              onChange={(event) => changeTargetVersion(event.target.value)}
            >
              {documentVersions.map((version) => (
                <option key={version.document_version_id} value={version.document_version_id}>
                  v{version.version_no} {version.is_active ? "(有効)" : ""}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={baseVersionId === null || targetVersionId === null || compare.isLoading}
            onClick={() => setCompareRequested(true)}
          >
            比較する
          </button>
        </div>
        {!compareRequested ? <p className="muted">比較する版を選び、「比較する」を押すと差分を読み込みます。</p> : null}
        {compareRequested && compare.isLoading ? <LoadingState label="版の差分を読み込んでいます..." /> : null}
        {compareRequested && compare.error ? <ErrorState error={compare.error} /> : null}
        {compareRequested && compare.data ? (
          <>
            <DiffSummary summary={compare.data.summary} />
            <MetadataDiffTable items={compare.data.metadata_diff.filter((item) => item.changed)} />
            <ChunkDiffTable items={compare.data.chunk_diff_items} />
            {compare.data.summary.diff_items_truncated ? (
              <InlineAlert>差分の表示件数を省略しています。必要に応じてチャンク一覧を確認してください。</InlineAlert>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="admin-section" id="chunk-preview">
        <h2>チャンク確認</h2>
        {previewVersion ? <p className="muted">v{previewVersion.version_no} の短いプレビューを表示しています。</p> : null}
        {chunks.isLoading ? <LoadingState label="チャンクを読み込んでいます..." /> : null}
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
        <dt>追加</dt>
        <dd>{summary.added_chunks}</dd>
      </div>
      <div>
        <dt>削除</dt>
        <dd>{summary.removed_chunks}</dd>
      </div>
      <div>
        <dt>変更</dt>
        <dd>{summary.changed_chunks}</dd>
      </div>
      <div>
        <dt>変更なし</dt>
        <dd>{summary.unchanged_chunks}</dd>
      </div>
    </dl>
  );
}

function MetadataDiffTable({ items }: { items: DocumentMetadataDiffItem[] }) {
  if (items.length === 0) {
    return <p className="muted">メタデータの変更はありません。</p>;
  }
  return (
    <table className="admin-table compact-table">
      <thead>
        <tr>
          <th>メタデータ</th>
          <th>比較元</th>
          <th>比較先</th>
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
    return <p className="muted">チャンク差分はありません。</p>;
  }
  return (
    <table className="admin-table compact-table">
      <thead>
        <tr>
          <th>種別</th>
          <th>出典</th>
          <th>比較元プレビュー</th>
          <th>比較先プレビュー</th>
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
