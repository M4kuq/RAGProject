# PR-51 Graph Citation / Path Validation / Debug UI

PR-51 connects provider-neutral Graph-RAG paths to the existing chunk-backed
citation model and exposes only admin-safe graph trace fields.

## Citation Bridge

Graph paths are not citation sources by themselves. A path is citable only when
its `source_chunk_ids_json` resolves through the same retrieval run:

```text
graph_retrieval_paths.source_chunk_ids_json
 -> retrieval_run_items.document_chunk_id
 -> CitationSource-compatible source refs
 -> citations
```

The implementation keeps the existing invariant:

```text
answer citation -> retrieval_run_item -> document_chunk
```

`GraphCitationBuilder` builds `CitationSource`-compatible source refs from
resolved, selected retrieval run items. It does not create citations directly
from graph nodes or graph relations.

## Path Validation

`GraphPathValidator` validates each saved graph path against active source
chunks and retrieval run items. A path is excluded from citable coverage when:

- it has no source chunks
- a source chunk does not exist
- a source chunk belongs to an inactive, non-ready, or archived document
- a source chunk is not present in `retrieval_run_items` for the same run

Paths with only unselected retrieval run items remain valid trace entries but
are not counted as citable paths.

## Coverage

The safe coverage summary reports counts and ratios only:

- path count
- valid / citable / excluded path count
- source chunk count
- resolved source chunk count
- citable source chunk count
- citation source count
- source chunk coverage ratio
- citation coverage ratio
- reason codes

No raw graph evidence, chunk text, prompt, or full context is included in these
metrics.

## Admin Debug API

Admins can inspect safe graph trace data with:

```text
GET /api/v1/rag/retrieval-runs/{retrieval_run_id}/graph-trace
```

The response includes safe path IDs, provider, validation status, reason codes,
safe entity labels, relation types, source chunk IDs, retrieval run item IDs,
existing citation IDs, depth, path score, and citation coverage.

The endpoint is admin-only. Viewer-facing `/rag/ask`, citation source lookup,
and chat responses do not expose graph trace panels.

## Provider Boundary

PostgresGraphStore and Neo4jGraphStore both already map retrieval results into
the common `GraphPath` DTO. PR-51 consumes saved `graph_retrieval_paths` and
`source_chunk_ids_json`, so citation/debug behavior is provider-independent.
Neo4j remains optional; when it is not configured, the default `postgres`
provider path continues to work.

## Redaction Boundary

Allowed in graph debug:

- provider name
- graph path id
- safe entity labels
- relation types
- graph node / relation refs
- source chunk ids
- retrieval run item ids
- citation ids
- path score and depth
- coverage numbers and reason codes

Never expose:

- raw graph evidence text
- raw chunk text or document text
- full context
- raw prompt material
- PII
- secrets, tokens, credentials, cookies, or `.env` values
