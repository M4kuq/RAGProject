# Neo4j Migration Prep

PR-49 prepares Graph-RAG for a future optional Neo4j backend without adding
Neo4j itself.

## Provider Boundary

Graph retrieval now goes through:

```text
GraphRetrievalStrategy
 -> GraphStoreResolver
 -> GraphStore
 -> GraphRetrievalResult
 -> retrieval_run_items
 -> graph_retrieval_paths
```

`PostgresGraphStore` remains the only working provider in PR-49 and preserves the
PR-48 PostgreSQL graph retrieval behavior. `Neo4jGraphStore` is intentionally a
skeleton that returns a safe unavailable result. It must not import the Neo4j
driver, open network connections, require a Docker service, or block app startup.

`GRAPH_STORE_PROVIDER` defaults to `postgres`. `neo4j` is accepted as a provider
value, but it is not a working backend until PR-50.

## Common DTO Shape

Providers must return the same safe DTOs:

- `GraphNodeRef`
- `GraphRelationRef`
- `GraphEvidenceRef`
- `GraphPath`
- `GraphRetrievalResult`

`GraphPath` stores safe refs only: provider, path ID, node refs, relation refs,
evidence refs, source chunk IDs, safe entity labels, relation types, depth,
path score, and score breakdown. Source chunk IDs remain the bridge back to
`retrieval_run_items` and citations.

## Redaction Rules

GraphStore DTOs, persisted graph path JSON, score breakdowns, logs, and API
responses must not include raw prompt text, raw chunk text, raw document text,
full context, raw graph evidence, PII, credentials, tokens, secrets, or `.env`
content. Hashes and IDs are allowed.

## PR-50 Implementation

PR-50 adds:

- optional Neo4j Python driver dependency
- optional Neo4j docker compose profile
- Neo4j connection config and health check
- PostgreSQL graph table to Neo4j projection
- bounded Neo4j path search
- PostgresGraphStore vs Neo4jGraphStore comparison smoke instructions

PR-50 must keep PostgreSQL as the source of truth and must continue to map Neo4j
results back through `source_chunk_ids`.

See `neo4j_optional_backend.md` for local setup, projection behavior, and the
optional smoke procedure.
