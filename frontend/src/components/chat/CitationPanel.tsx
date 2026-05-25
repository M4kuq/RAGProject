import { RagAskCitation } from "../../features/chat/chatTypes";
import { OldSourceBadge } from "./OldSourceBadge";

function truncate(value: string | null | undefined, maxLength: number, fallback = ""): string {
  const normalized = (value ?? "").replace(/\s+/g, " ").trim() || fallback;
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}...` : normalized;
}

function pageLabel(citation: RagAskCitation): string | null {
  if (citation.page_from === null && citation.page_to === null) {
    return null;
  }
  if (citation.page_from !== null && citation.page_to !== null && citation.page_from !== citation.page_to) {
    return `p.${citation.page_from}-${citation.page_to}`;
  }
  return `p.${citation.page_from ?? citation.page_to}`;
}

export function CitationPanel({ citations }: { citations?: RagAskCitation[] }) {
  if (!citations || citations.length === 0) {
    return null;
  }
  return (
    <aside className="citation-panel" aria-label="citations">
      <h3>Citations</h3>
      <ol>
        {citations.map((citation) => (
          <li key={citation.local_citation_id} className="citation-item">
            <div className="citation-title">
              <span>
                [{citation.local_citation_id}] {truncate(citation.source_label, 80, "source")}
              </span>
              {citation.old_version_flag ? <OldSourceBadge /> : null}
            </div>
            <div className="citation-meta">
              {pageLabel(citation) ? <span>{pageLabel(citation)}</span> : null}
              {citation.section_title ? <span>{truncate(citation.section_title, 80)}</span> : null}
            </div>
            <p>{truncate(citation.snippet, 240)}</p>
          </li>
        ))}
      </ol>
    </aside>
  );
}
