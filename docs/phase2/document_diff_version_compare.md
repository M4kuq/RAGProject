# PR-36 Document Diff / Version Compare

## Purpose

PR-36 adds an admin-only comparison flow for versions under the same logical document.
The goal is to make version drift inspectable without exposing raw file content or
unbounded chunk text.

## API

```http
GET /api/v1/documents/{logical_document_id}/versions/compare?base_version_id=...&target_version_id=...
```

- Admin only.
- `base_version_id` and `target_version_id` must belong to the same logical document.
- Archived logical documents can still be compared by admins.
- The response contains safe version summaries, metadata diff, chunk diff summary,
  and bounded chunk previews.
- The response never includes `storage_key`, absolute storage paths, raw file content,
  or unbounded chunk text.

## Diff Algorithm V1

Chunk matching is deterministic and intentionally lightweight:

1. exact `chunk_hash`
2. metadata structural key such as parent/child chunk key, sheet rows, slide number,
   HTML heading path, XML path, or section title
3. chunk index fallback when normalized text similarity is above the configured threshold

Chunk states:

- `added`
- `removed`
- `changed`
- `unchanged`

Diff items are capped by `DOCUMENT_DIFF_MAX_ITEMS`. Previews are capped by
`DOCUMENT_DIFF_PREVIEW_MAX_CHARS` and redacted for secret-like assignments, URLs,
and email addresses.

## Metadata Diff

The metadata diff is limited to safe display fields:

- file name
- MIME type
- file size
- page count
- extractor name/version
- status and active flag
- chunk count
- URL metadata after redaction

The diff intentionally excludes storage keys, absolute paths, raw extractor metadata,
and any raw content snapshots.

## Known Limitations

- No full document visual diff.
- No PDF page image rendering.
- No DOCX/PPTX visual rendering.
- No OCR region UI.
- No Graph-RAG citation graph.

These remain Phase3 or later work.
