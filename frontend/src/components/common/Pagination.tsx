import type { PaginationMeta } from "../../types/api";

export function Pagination({
  meta,
  onPageChange
}: {
  meta?: PaginationMeta;
  onPageChange: (page: number) => void;
}) {
  if (!meta) {
    return null;
  }
  return (
    <nav className="pagination" aria-label="ページ送り">
      <button type="button" disabled={meta.page <= 1} onClick={() => onPageChange(meta.page - 1)}>
        前へ
      </button>
      <span>
        {meta.page} / {Math.max(1, Math.ceil(meta.total / meta.page_size))} ページ
      </span>
      <button type="button" disabled={!meta.has_next} onClick={() => onPageChange(meta.page + 1)}>
        次へ
      </button>
    </nav>
  );
}
