import { Link } from "react-router-dom";
import { formatBytes, formatDate, truncateText } from "../../lib/format";
import type { DocumentVersionSummary } from "../../features/documents/documentTypes";
import { StatusBadge } from "./StatusBadge";

export function VersionList({
  logicalDocumentId,
  versions
}: {
  logicalDocumentId: number;
  versions: DocumentVersionSummary[];
}) {
  if (versions.length === 0) {
    return <p className="muted">まだ版がありません。新しいファイルをアップロードするとここに表示されます。</p>;
  }

  return (
    <table className="admin-table">
      <thead>
        <tr>
          <th>Version</th>
          <th>状態</th>
          <th>表示状態</th>
          <th>ファイル</th>
          <th>サイズ</th>
          <th>チャンク</th>
          <th>作成日時</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        {versions.map((version) => (
          <tr key={version.document_version_id}>
            <td>
              v{version.version_no} {version.is_active ? <strong>(有効)</strong> : null}
            </td>
            <td>
              <StatusBadge status={version.status} />
            </td>
            <td>
              <StatusBadge status={version.display_status} />
            </td>
            <td>{truncateText(version.file_name, 40)}</td>
            <td>{formatBytes(version.file_size_bytes)}</td>
            <td>{version.chunk_count ?? "-"}</td>
            <td>{formatDate(version.created_at)}</td>
            <td>
              <Link to={`/admin/documents/${logicalDocumentId}/versions/${version.document_version_id}`}>詳細</Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
