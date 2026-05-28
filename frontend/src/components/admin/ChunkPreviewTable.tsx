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
          <th>Source</th>
          <th>Modality</th>
          <th>Preview</th>
          <th>Chars</th>
        </tr>
      </thead>
      <tbody>
        {chunks.map((chunk) => (
          <tr key={chunk.document_chunk_id}>
            <td>{chunk.chunk_index}</td>
            <td>
              {chunk.page_from ?? "-"}
              {chunk.page_to && chunk.page_to !== chunk.page_from ? `-${chunk.page_to}` : ""}
            </td>
            <td>{truncateText(chunkSourceLabel(chunk), 120)}</td>
            <td>{chunk.modality}</td>
            <td>
              {truncateText(chunk.preview, 160)}
              {chunk.preview_truncated ? " [truncated]" : ""}
            </td>
            <td>{chunk.char_count ?? "-"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function chunkSourceLabel(chunk: DocumentChunkItem): string {
  const metadata = chunk.metadata_json;
  if (!metadata) {
    return chunk.section_title ?? "-";
  }
  if (metadata.structure_type === "excel_sheet") {
    const sheet = typeof metadata.sheet_name === "string" ? metadata.sheet_name : null;
    const rowFrom = typeof metadata.row_from === "number" ? metadata.row_from : null;
    const rowTo = typeof metadata.row_to === "number" ? metadata.row_to : null;
    const rowLabel =
      rowFrom && rowTo
        ? rowFrom === rowTo
          ? `Row ${rowFrom}`
          : `Rows ${rowFrom}-${rowTo}`
        : null;
    return [sheet ? `Sheet: ${sheet}` : null, rowLabel].filter(Boolean).join(" / ") || "-";
  }
  if (metadata.structure_type === "powerpoint_slide") {
    const slide = typeof metadata.slide_number === "number" ? `Slide ${metadata.slide_number}` : null;
    const title = typeof metadata.slide_title === "string" ? metadata.slide_title : null;
    return [slide, title].filter(Boolean).join(" / ") || "-";
  }
  if (metadata.structure_type === "html_section") {
    const heading = typeof metadata.heading_path === "string" ? metadata.heading_path : null;
    const elementType = typeof metadata.element_type === "string" ? metadata.element_type : null;
    return [heading, elementType].filter(Boolean).join(" / ") || "-";
  }
  if (metadata.structure_type === "xml_element") {
    const path = typeof metadata.xml_path === "string" ? metadata.xml_path : null;
    const name = typeof metadata.element_name === "string" ? metadata.element_name : null;
    return [path, name].filter(Boolean).join(" / ") || "-";
  }
  return chunk.section_title ?? "-";
}
