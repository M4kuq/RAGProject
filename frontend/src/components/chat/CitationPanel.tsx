import { useState } from "react";
import { Link } from "react-router-dom";
import { useCurrentUser } from "../../features/auth/authHooks";
import { fetchCitationSource } from "../../features/chat/chatApi";
import { RagAskCitation } from "../../features/chat/chatTypes";
import { OldSourceBadge } from "./OldSourceBadge";

function truncate(value: string | null | undefined, maxLength: number, fallback = ""): string {
  const normalized = (value ?? "").replace(/\s+/g, " ").trim() || fallback;
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}...` : normalized;
}

function pageLabel(citation: { page_from: number | null; page_to: number | null }): string | null {
  if (citation.page_from === null && citation.page_to === null) {
    return null;
  }
  if (citation.page_from !== null && citation.page_to !== null && citation.page_from !== citation.page_to) {
    return `p.${citation.page_from}-${citation.page_to}`;
  }
  return `p.${citation.page_from ?? citation.page_to}`;
}

export function CitationPanel({ citations }: { citations?: RagAskCitation[] }) {
  const currentUser = useCurrentUser();
  const [openCitationId, setOpenCitationId] = useState<number | null>(null);
  const [sourceByCitationId, setSourceByCitationId] = useState<Record<number, Awaited<ReturnType<typeof fetchCitationSource>>>>({});
  const [loadingCitationId, setLoadingCitationId] = useState<number | null>(null);
  const [sourceErrorByCitationId, setSourceErrorByCitationId] = useState<Record<number, string>>({});

  if (!citations || citations.length === 0) {
    return null;
  }

  async function openSource(citation: RagAskCitation) {
    if (openCitationId === citation.citation_id) {
      setOpenCitationId(null);
      return;
    }
    const citationId = citation.citation_id;
    setOpenCitationId(citationId);
    setSourceErrorByCitationId((current) => {
      const next = { ...current };
      delete next[citationId];
      return next;
    });
    if (sourceByCitationId[citationId]) {
      return;
    }
    if (loadingCitationId === citationId) {
      return;
    }
    setLoadingCitationId(citationId);
    try {
      const source = await fetchCitationSource(citationId);
      setSourceByCitationId((current) => ({ ...current, [citationId]: source }));
    } catch {
      setSourceErrorByCitationId((current) => ({ ...current, [citationId]: "出典プレビューを読み込めませんでした。" }));
    } finally {
      setLoadingCitationId((current) => (current === citationId ? null : current));
    }
  }

  return (
    <aside className="citation-panel" aria-label="引用">
      <h3>引用</h3>
      <ol>
        {citations.map((citation) => (
          <li key={citation.local_citation_id} className="citation-item">
            <div className="citation-title">
              <span>
                [{citation.local_citation_id}] {truncate(citation.source_label, 80, "出典")}
              </span>
              {citation.old_version_flag ? <OldSourceBadge /> : null}
            </div>
            <div className="citation-meta">
              {pageLabel(citation) ? <span>{pageLabel(citation)}</span> : null}
              {citation.section_title ? <span>{truncate(citation.section_title, 80)}</span> : null}
            </div>
            <p>{truncate(citation.snippet, 240)}</p>
            <button className="inline-text-button" type="button" onClick={() => void openSource(citation)}>
              {openCitationId === citation.citation_id ? "出典を非表示" : "出典を表示"}
            </button>
            {openCitationId === citation.citation_id ? (
              <SourcePreview
                citationId={citation.citation_id}
                isAdmin={currentUser.data?.role === "admin"}
                isLoading={loadingCitationId === citation.citation_id}
                error={sourceErrorByCitationId[citation.citation_id] ?? null}
                source={sourceByCitationId[citation.citation_id]}
              />
            ) : null}
          </li>
        ))}
      </ol>
    </aside>
  );
}

function SourcePreview({
  citationId,
  error,
  isAdmin,
  isLoading,
  source
}: {
  citationId: number;
  error: string | null;
  isAdmin: boolean;
  isLoading: boolean;
  source?: Awaited<ReturnType<typeof fetchCitationSource>>;
}) {
  if (isLoading) {
    return <div className="source-preview muted">出典を読み込み中...</div>;
  }
  if (error) {
    return <div className="source-preview source-preview-error">{error}</div>;
  }
  if (!source) {
    return null;
  }
  const locatorParts = [
    source.sheet_name ? `シート: ${source.sheet_name}` : null,
    source.row_from !== null && source.row_to !== null ? `行 ${source.row_from}-${source.row_to}` : null,
    source.slide_number !== null ? `スライド ${source.slide_number}` : null,
    source.html_heading_path,
    source.xml_path,
    pageLabel(source)
  ].filter(Boolean);
  return (
    <div className="source-preview" aria-label={`出典プレビュー ${citationId}`}>
      <div className="source-preview-header">
        <strong>{truncate(source.display_label || source.source_label, 100, "出典")}</strong>
        {source.old_version_flag ? <OldSourceBadge /> : null}
      </div>
      <dl className="source-preview-facts">
        <div>
          <dt>版</dt>
          <dd>v{source.version_no}</dd>
        </div>
        <div>
          <dt>チャンク</dt>
          <dd>#{source.document_chunk_id}</dd>
        </div>
        <div>
          <dt>出典</dt>
          <dd>{source.source_type === "external_url" ? "外部URL" : "アップロード"}</dd>
        </div>
      </dl>
      {locatorParts.length ? <p className="citation-meta">{locatorParts.join(" / ")}</p> : null}
      {source.source_url ? (
        <a href={source.source_url} rel="noopener noreferrer" target="_blank">
          {truncate(source.source_url, 120)}
        </a>
      ) : null}
      <p>{truncate(source.preview, 500)}</p>
      {source.preview_truncated ? <p className="muted">プレビューは一部のみ表示しています。</p> : null}
      {isAdmin ? (
        <div className="source-preview-actions">
          <Link to={`/admin/documents/${source.logical_document_id}`}>
            文書 #{source.logical_document_id} を開く
          </Link>
          <Link to={`/admin/documents/${source.logical_document_id}#version-compare`}>版比較を開く</Link>
        </div>
      ) : null}
    </div>
  );
}
