import { truncateText } from "../../lib/format";
import type { DocumentChunkItem } from "../../features/documents/documentTypes";

export function ChunkPreviewTable({ chunks }: { chunks: DocumentChunkItem[] }) {
  if (chunks.length === 0) {
    return <p className="muted">No chunks.</p>;
  }

  return (
    <table className="admin-table">
      <thead>
        <tr>
          <th>Index</th>
          <th>Page</th>
          <th>Modality</th>
          <th>Preview</th>
          <th>Chars</th>
        </tr>
      </thead>
      <tbody>
        {chunks.map((chunk) => (
          <tr key={chunk.document_chunk_id}>
            <td>{chunk.chunk_index}</td>
            <td>{chunk.page_from ?? "-"}{chunk.page_to && chunk.page_to !== chunk.page_from ? `-${chunk.page_to}` : ""}</td>
            <td>{chunk.modality}</td>
            <td>{truncateText(chunk.preview, 160)}{chunk.preview_truncated ? " [truncated]" : ""}</td>
            <td>{chunk.char_count ?? "-"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

