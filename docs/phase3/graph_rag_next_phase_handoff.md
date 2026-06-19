# GraphRAG Next Phase Handoff

PR-54 closes the text GraphRAG portfolio/demo slice. The following items are
handoff candidates for PR-55 and later. They are not part of PR-54.

## Candidate PR-55: Graph + Vector Hybrid Fusion

Goal: expose a real `graph_hybrid` strategy that merges graph path evidence with
dense/sparse/hybrid candidates before Context Budget.

Required guardrails:

- keep citations chunk-backed
- preserve graph path validation
- keep cache keys provider-aware
- add evaluation comparison against `graph_postgres` and `graph_neo4j`
- avoid raw graph evidence in score breakdowns or debug output

## Candidate PR-56: OCR / Scanned PDF Boundary

Goal: add text extraction for scanned documents only after the graph citation
model is stable.

Required guardrails:

- store OCR text according to the document/chunk policy, not graph debug payloads
- keep region/page metadata bounded
- do not expose raw OCR dumps in traces or artifacts
- add OCR-specific confidence and citation region tests

## Candidate PR-57: Image / Multimodal Metadata

Goal: support image lifecycle and safe metadata for future multimodal citation.

Required guardrails:

- strict upload validation
- no embedded secrets or EXIF leakage in debug/admin output
- safe source locator model before viewer exposure

## Candidate PR-58: Storage / Identity / Deploy Options

Possible independent tracks:

- S3/object storage adapter
- OIDC/OAuth while preserving local demo auth
- AWS deployment foundation separate from `k8s/local`

Required guardrails:

- local defaults remain runnable
- no real cloud secrets in repo, docs, logs, or screenshots
- cost and cleanup steps are explicit

## Candidate PR-59: Production Evaluation And Observability

Goal: production-like evaluation, sampling, dashboards, and alerting.

Required guardrails:

- aggregate metrics only by default
- no raw prompt, context, chunk text, graph evidence, PII, or credentials in
  traces/artifacts
- explicit opt-in for external export
- safe blocked states for unavailable local models/providers

## Carry-Forward Acceptance Checks

- PostgreSQL remains source of truth for GraphRAG state unless a future design
  explicitly changes that boundary.
- Neo4j remains a rebuildable read model.
- Graph, OCR, and multimodal evidence must map back to citation-safe sources.
- Context Budget, Evidence Pack, and Tool Result Compression remain in the path
  before generated answers or planner-visible tool outputs.
