# Neo4j Graph Store Backend

C2a promotes Neo4j from an opt-in demo profile to the default GraphStore read
model for the local Compose and CI stacks. PostgreSQL remains the
source of truth for `graph_entities`, `graph_relations`, `graph_entity_mentions`,
retrieval runs, retrieval run items, and citations.

## Defaults

- `GRAPH_STORE_PROVIDER=neo4j` is the default in `docker-compose.yml` and
  `docker-compose.ci.yml`.
- `GRAPH_RETRIEVAL_ENABLED=true`, `NEO4J_PROJECTION_ENABLED=true`, and
  `NEO4J_HEALTH_CHECK_ENABLED=true` are the local Compose defaults.
- The `neo4j` Docker Compose service is part of default `docker compose up`.
- The backend and worker Compose builds install the `neo4j` optional dependency
  extra by default. Running the backend outside Docker still requires
  `uv sync --extra dev --extra neo4j` for Neo4j connectivity.

If Neo4j is not configured, the driver is not installed, the server is down, or
projection has not been populated, the application still starts and PostgreSQL
Graph-RAG remains usable. When graph retrieval asks for the Neo4j provider and
PostgreSQL graph sources can answer the query, the strategy falls back to
PostgreSQL graph, records `neo4j_to_postgres_fallback`, and keeps the Neo4j
setup reason codes in `fallback_reason_codes` and the persisted retrieval score
summary field `graph_fallback_reason_codes`.

## Local Setup

Install the optional backend dependency when running the backend outside Docker:

```bash
cd backend
python -m uv sync --extra dev --extra neo4j
```

Start the default local stack:

```powershell
docker compose up -d --build
```

The local default Neo4j password is the non-secret Compose development value
`change-me-local`. Override it before any shared environment:

```powershell
$env:NEO4J_PASSWORD = "<local override>"
docker compose up -d --build
```

Equivalent POSIX shell shape:

```sh
export NEO4J_PASSWORD="<local override>"
docker compose up -d --build
```

`BACKEND_UV_EXTRA_ARGS="--extra neo4j"` remains available for explicit Docker
build overrides, but the default Compose stack already passes that extra to
backend, worker, migrate, seed, and CI test builds.

Use a local-only password for development and replace it before any shared
environment. Do not paste real Neo4j credentials into docs, logs, PR comments,
or shell transcripts.

For a single local demo command that starts the default Neo4j stack, rebuilds
the self-docs corpus, builds PostgreSQL graph indexes, projects to Neo4j, and runs
`graph_postgres` vs `graph_neo4j` comparison, use:

```powershell
scripts\neo4j_demo.ps1
```

```sh
sh scripts/neo4j_demo.sh
```

The detailed corpus manifest and provider comparison runbook are in
[`docs/demo/corpus_neo4j_demo.md`](../demo/corpus_neo4j_demo.md).

## Projection

The projection reads PostgreSQL graph tables and writes only safe read-model
properties to Neo4j:

- entity IDs
- safe labels
- entity types
- sanitized aliases
- fixed Neo4j node labels
- fixed relationship labels
- relation types
- source chunk IDs
- document version IDs
- logical document IDs
- chunk modality and active-status metadata
- graph index run IDs
- confidence values
- hashes

It does not write raw chunk text, raw document text, raw evidence text, prompts,
full context, PII, tokens, credentials, or `.env` values.

When `NEO4J_PROJECTION_ENABLED=true`, the worker triggers projection after a
successful `graph_index_build` commit. The projection is best-effort; failure
does not fail the PostgreSQL graph index run. If a retry sees that the graph
index run has already succeeded, it still retries projection before returning a
no-op job result so a crash between commit and projection does not permanently
leave the read model stale.

Projection is idempotent for a document version:

1. Existing Neo4j mentions, relation projections, and chunk refs for the
   document version are removed.
2. Entity and chunk nodes are `MERGE`d by stable IDs.
3. Mention and relation relationships are `MERGE`d by stable graph row IDs.

The delete and replacement writes run in one Neo4j write transaction so a failed
replacement does not commit a partially removed read model.

## Search Behavior

`Neo4jGraphStore` returns the same provider-independent DTOs as
`PostgresGraphStore`:

- `GraphNodeRef`
- `GraphRelationRef`
- `GraphEvidenceRef`
- `GraphPath`
- `GraphRetrievalResult`

Neo4j traversal is bounded by the existing graph retrieval settings:

- `GRAPH_RETRIEVAL_MAX_START_ENTITIES`
- `GRAPH_RETRIEVAL_MAX_DEPTH`
- `GRAPH_RETRIEVAL_MAX_PATHS`
- `GRAPH_RETRIEVAL_MAX_RELATIONS_PER_ENTITY`
- `GRAPH_RETRIEVAL_MAX_SOURCE_CHUNKS`
- `GRAPH_RETRIEVAL_TIMEOUT_MS`

Neo4j paths still return through `source_chunk_ids`. Citations continue to be
derived from selected `retrieval_run_items`, not directly from graph nodes or
relationships.

## Optional Smoke

This smoke is not required for normal CI.

1. Start PostgreSQL/Qdrant/backend/worker/frontend normally.
2. Start Neo4j with the `neo4j` profile and enable projection.
3. Ingest or seed documents so ready document versions exist.
4. Queue graph index work without printing document text:

   ```bash
   docker compose exec backend python -m app.scripts.queue_graph_index_builds --dry-run
   docker compose exec backend python -m app.scripts.queue_graph_index_builds
   ```

5. Confirm `graph_index_build` jobs succeed in the worker/admin job view.
6. Query once with `strategy=graph_postgres`.
7. Query the same request with `strategy=graph_neo4j`.
8. Compare that both responses are chunk-backed and that graph path summaries
   contain only safe refs and `source_chunk_ids`.

Useful checks:

```bash
docker compose config --services
docker compose -f docker-compose.yml -f docker-compose.neo4j-demo.yml config --services
docker compose ps neo4j
```

The default config must list `neo4j`; the demo overlay should not define a
second Neo4j service.
