# GraphRAG Known Limitations

These limitations are intentional at the PR-54 boundary.

## Runtime

- Graph retrieval is disabled by default and must be explicitly enabled.
- Graph-aware router selection is disabled by default and must be explicitly enabled.
- Explicit `strategy=graph` requires a populated graph index. Without graph
  evidence, explicit graph requests keep the no-context behavior.
- Router-selected graph can fall back to dense or hybrid, but the project does
  not expose a separate public `graph_hybrid` fusion strategy yet.
- Graph indexing uses the current rule-based extractor. It is deterministic and
  safe for demo coverage, not a general-purpose relation extraction model.
- The local helper queues graph index build jobs for active ready document
  versions. It is not a production scheduler or admin API.

## UI

- Retrieval Debug can display graph trace for graph runs, but the form may not
  expose explicit `graph` as a selectable strategy. Use API or scripted local
  calls to create explicit graph runs, then inspect the trace in the UI.
- Viewer-facing chat does not show internal graph trace panels.
- Graph debug output is admin-only and limited to safe refs, labels, counts,
  scores, coverage, and reason codes.

## Neo4j

- Neo4j is optional and off by default.
- PostgreSQL remains the source of truth. Neo4j is a rebuildable read model.
- Neo4j driver dependencies are optional. Docker-based Neo4j runs require
  `BACKEND_UV_EXTRA_ARGS="--extra neo4j"` during backend/worker build.
- Neo4j projection is best-effort after PostgreSQL graph index success.
- If Neo4j is unavailable, the application should still start and PostgreSQL
  GraphRAG should remain usable.

## Cache

- Retrieval cache is disabled by default.
- Cache stores retrieval references and safe metadata only, not answers or raw
  evidence.
- Auto/tool-orchestrated ask paths remain uncached when replaying trace safely
  would require broader planner metadata design.
- Redis and semantic/answer/full-context cache are out of scope.

## Evaluation

- `phase3_graph_multi_hop` is a small synthetic fixture, not a broad quality
  benchmark.
- `graph_neo4j` is optional and can be not-applicable when Neo4j is absent.
- Graph metrics are safe summaries and should not be treated as calibrated
  production quality measurements.

## Out Of Scope

The following remain PR-55+ or later candidates:

- full graph/vector `graph_hybrid` fusion strategy
- OCR and scanned PDF support
- image upload and multimodal citations
- external LLM provider expansion work
- S3/object storage
- OIDC/OAuth
- AWS/cloud deployment
- Redis cache implementation
- production monitoring, alerting, and online A/B evaluation
