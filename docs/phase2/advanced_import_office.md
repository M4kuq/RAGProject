# PR-34 Advanced Import: Excel / PowerPoint

## Purpose

PR-34 adds deterministic ingestion for Office Open XML spreadsheets and presentations:

- `.xlsx` Excel workbooks
- `.pptx` PowerPoint presentations

The implementation is intentionally limited to text extraction and safe structural metadata. It does not add OCR, embedded object extraction, legacy binary Office parsing, HTML/XML/URL ingestion, Graph-RAG, or multimodal retrieval.

## Upload Validation

Allowed new extensions:

- `.xlsx`
- `.pptx`

Rejected formats:

- `.xls`
- `.ppt`
- `.xlsm`
- `.pptm`
- `.docm`
- unknown Office files

Validation checks extension, MIME type, ZIP magic bytes, required OOXML entries, path traversal, encrypted ZIP entries, compression ratio, total uncompressed bytes, and main XML size. Archives containing macro or embedded object parts such as `vbaProject.bin`, `activeX`, or `embeddings` are rejected.

## Excel Extraction

Excel extraction reads visible sheets only. Hidden sheets are skipped.

Extracted text is grouped by sheet and row blocks. Each row is rendered deterministically as column labels and cell values, for example:

```text
Sheet: Sales
Rows: 1-2
R1: A=Region | B=Revenue
R2: A=East | B=10
```

Safe metadata saved with extracted pages and child chunks:

- `structure_type=excel_sheet`
- `sheet_name`
- `row_from`
- `row_to`
- `column_from`
- `column_to`
- `table_index`
- `parent_chunk_key`
- `parent_title`

Formulas are read as the stored formula/value XML text available in the workbook. External links are not followed. Workbook author metadata is not stored.

## PowerPoint Extraction

PowerPoint extraction reads slides in presentation order and extracts text from text shapes and table cells.

Safe metadata saved with extracted pages and child chunks:

- `structure_type=powerpoint_slide`
- `slide_number`
- `slide_title`
- `shape_count`
- `table_count`
- `parent_chunk_key`
- `parent_title`

Speaker notes are not extracted in PR-34. Images, charts without embedded text, and embedded/OLE objects are ignored.

## Search And Citations

Excel and PowerPoint chunks are stored as normal `document_chunks` rows with `modality=text`. Search and ask use the same dense/sparse/hybrid/agentic paths as existing documents.

Source labels are enriched from safe metadata:

- `sales.xlsx / Sheet: Sales / Rows 1-20`
- `proposal.pptx / Slide 3 / Title: Architecture`

Responses, traces, and score breakdowns must not expose raw file content or raw chunk text.

## Security

PR-34 does not read `.env`, secrets, or credentials. It does not log raw file content, raw extracted text, raw chunk text, hidden sheets, embedded files, or OOXML package internals. Metadata persisted in `document_chunks.metadata_json` is limited to allowlisted structural values.

## Known Limitations

- Legacy `.xls` and `.ppt` are not supported.
- Macro-enabled Office files are rejected.
- OCR is not performed.
- Speaker notes are excluded.
- Table semantic understanding is limited to deterministic text rendering.
- Parent-child chunking is metadata-only in PR-34.
