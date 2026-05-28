# PR-34 Parent-child Chunking v1

## Purpose

Parent-child chunking v1 tracks a large structural unit as a parent and the searchable text unit as a child. PR-34 uses this for Excel sheets and PowerPoint slides without redesigning the database.

## Storage Model

PR-34 uses metadata-only parent-child tracking.

`document_chunks` receives a nullable `metadata_json` column. The column stores safe structural metadata only. It does not store raw prompt, raw file content, full context, raw chunk text, PII, tokens, or secrets.

Example:

```json
{
  "parent_child_schema_version": "phase2.parent_child.v1",
  "chunk_level": "child",
  "parent_chunk_key": "xlsx:sheet:1",
  "child_chunk_key": "xlsx:sheet:1:chunk:0",
  "parent_title": "Sales",
  "structure_type": "excel_sheet",
  "sheet_name": "Sales",
  "row_from": 1,
  "row_to": 20
}
```

## Chunking Rules

- Excel parents are visible sheets or row groups.
- Excel children are normal text chunks with sheet and row metadata.
- PowerPoint parents are slides.
- PowerPoint children are normal text chunks with slide metadata.
- `modality` remains `text`.
- `page_from` / `page_to` may be used for slide numbers when available.
- `section_title` may contain `Sheet: <name>` or `Slide <n>: <title>`.

The chunk hash and normalized text behavior remain unchanged.

## Retrieval Use

The retrieval pipeline keeps using `document_chunks.content_text` for search and answer context assembly. Parent-child metadata is used only for source labels, safe payload snapshots, and debug/citation display.

Qdrant payloads include only allowlisted structural metadata fields. RDB final check remains authoritative for active document/version filtering.

## Future Work

Option B, a nullable `parent_document_chunk_id`, is deferred. It should only be added if a later PR needs relational parent traversal that cannot be served by safe metadata.
