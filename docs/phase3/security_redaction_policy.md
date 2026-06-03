# Phase3 Security / Redaction Policy

Phase3 expands evidence types. The security policy is to minimize and reference evidence rather than copy it into traces, tables, docs, logs, artifacts, UI, or MCP output.

## Forbidden Outputs

Do not output or persist these in docs, logs, artifacts, debug traces, UI, MCP output, graph tables, or PR comments:

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

## Graph Tables

Graph tables must store IDs, hashes, counts, confidence, labels, and source refs. They must not store raw document text or raw chunk text.

Use:

- `document_chunk_id`
- `document_version_id`
- `source_document_chunk_id`
- `mention_text_hash`
- `evidence_text_hash`
- `source_chunk_ids_json`

Avoid:

- copied source passages
- copied extraction prompt material
- full path payload dumps containing evidence text

## PII Masking

PII-like data should be masked or represented through safe labels only when needed for retrieval. Admin debug should show counts, categories, hashes, and refs rather than private values.

## External LLM Export

External LLM provider use is optional and future work. Before enabling it:

- apply Context Budget
- apply Evidence Pack
- apply Tool Result Compression for tool outputs
- send the smallest source-backed evidence set
- record safe export summaries only
- keep provider credentials out of docs, logs, traces, and artifacts

## OCR Text Handling

OCR may internally process image/scanned text. Raw OCR text must not be written to logs, trace JSON, graph debug panels, MCP output, or evaluation artifacts. Use OCR text hashes, region metadata, confidence, and source locator refs.

## Image Metadata Handling

Image metadata should be bounded and sanitized. Location-like, device-like, author-like, or credential-like metadata should be stripped or masked unless a later accepted design proves it is required.

## Graph Path Logging

Graph path logs may include:

- path ref ID
- entity IDs and safe labels
- relation IDs and safe labels/types
- hop count
- source chunk IDs
- score summaries
- validation status
- drop reason counts

Graph path logs must not include raw source passages or raw OCR text.

## Audit Logging

Audit logs record action, target type, target ID, status, and safe error code. They should not include raw evidence, full context, or credential values.

## Viewer/Admin Debug Boundary

- Viewer: answer, citations, confidence, bounded source previews, safe strategy label.
- Admin: safe summaries for retrieval, graph, context budget, evidence pack, compression, and validation.

Admin access is not a reason to expose raw evidence payloads.

## Secret Management Boundary

Local `.env` values, kubeconfig, provider credentials, generated secret manifests, and cloud secrets are never copied into docs or committed files. AWS or provider secrets belong to a future deploy/aws or provider integration design.

## deploy/aws Relationship

AWS work must preserve this redaction policy before adding S3, Bedrock, RDS, ECS/EKS, OIDC, Secrets Manager, WAF, NAT, or private subnet decisions.
