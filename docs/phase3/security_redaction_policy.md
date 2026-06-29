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
- raw LLM response text
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

Repository and DTO paths validate or sanitize graph metadata. Metadata must not
include raw text dumps, prompt material, raw LLM responses, full context, PII
details, token values, credential values, password values, or secret values.
Safe aggregate token counts such as `graph_extraction_input_token_count` are
allowed when stored as non-negative integers.

## Error Handling

`GraphIndexService` and `GraphRepository` store safe error code/message only. Error messages pass through existing redaction before persistence.

## Viewer/Admin Boundary

Admin graph debug surfaces may show counts, IDs, refs, scores, validation
summaries, provider/model labels, latency, cost estimates, token counts, cache
status, and reason codes. Viewer-facing
responses must not expose graph internals or raw evidence.

## PR-54 Demo And Docs Checklist

Before publishing demo docs, screenshots, PR comments, smoke logs, or manual
test evidence, confirm they do not contain:

- raw prompt text
- raw LLM response text
- raw query text beyond safe synthetic examples
- raw chunk text or raw document text
- full generated context
- raw graph evidence payloads
- raw OCR text or image-derived private text
- PII
- credentials, tokens, cookies, password values, API keys, or `.env` values
- Neo4j credentials or database dumps
- PostgreSQL, Qdrant, Neo4j, upload volume, or cache dumps

Safe evidence examples:

- IDs, counts, hashes, version refs, source chunk IDs, retrieval run IDs, and
  graph path IDs
- provider labels such as `postgres` or `neo4j`
- cache status such as `hit`, `miss`, `bypass`, `stale`, or
  `strategy_not_cacheable`
- metric names and aggregate scores
- safe synthetic entity labels and relation types
- fallback or not-applicable reason codes for Neo4j evaluation

## deploy/aws Boundary

External provider and AWS export decisions remain future work. Before any graph,
OCR, or image evidence leaves local runtime, the export policy must preserve
Context Budget, Evidence Pack, Tool Result Compression, and this redaction
policy.
