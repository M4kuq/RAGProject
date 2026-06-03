# Phase3 Security / Redaction Policy

Phase3 expands evidence types. The policy is to minimize and reference evidence rather than copy it into traces, tables, docs, logs, artifacts, UI, or MCP output.

## PR-46 Database Policy

Graph tables store:

- entity IDs
- canonical labels and bounded aliases
- relation labels/types
- confidence scores
- source chunk refs
- document version refs
- sha256 hashes
- counts
- safe metadata
- graph path summaries

Graph tables do not store:

- raw document text
- raw chunk text
- raw prompt material
- full context
- raw OCR text
- raw graph evidence text
- raw tool payload dumps
- PII
- credential values
- session values
- secret values
- local DB/Qdrant dumps
- kubeconfig or generated secret manifests

## Evidence References

Relations use:

- `source_document_chunk_id`
- `evidence_text_hash`

Mentions use:

- `document_chunk_id`
- `document_version_id`
- `mention_text_hash`
- optional offsets

Retrieval paths use:

- `retrieval_run_id`
- safe `path_json`
- `score_breakdown_json`
- `source_chunk_ids_json`

## Metadata Guard

Repository and DTO paths validate or sanitize graph metadata. Metadata must not include raw text dumps, prompt material, full context, PII, token values, credential values, password values, or secret values.

## Error Handling

`GraphIndexService` and `GraphRepository` store safe error code/message only. Error messages pass through existing redaction before persistence.

## Viewer/Admin Boundary

PR-46 does not add API or UI. Future admin debug surfaces may show counts, IDs, refs, scores, and validation summaries. Viewer-facing responses must not expose graph internals or raw evidence.

## deploy/aws Boundary

External provider and AWS export decisions remain future work. Before any graph, OCR, or image evidence leaves local runtime, the export policy must preserve Context Budget, Evidence Pack, Tool Result Compression, and this redaction policy.
