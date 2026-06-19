# Neo4j Optional Backend

PR-50 adds Neo4j as an optional GraphStore read model. PostgreSQL remains the
source of truth for `graph_entities`, `graph_relations`, `graph_entity_mentions`,
retrieval runs, retrieval run items, and citations.

## Defaults

- `GRAPH_STORE_PROVIDER=postgres` remains the default.
- The Neo4j Python driver is an optional dependency extra, not a required
  runtime dependency.
- The `neo4j` Docker Compose service is behind the `neo4j` profile and is not
  part of default `docker compose up`.
- Neo4j projection is disabled unless `NEO4J_PROJECTION_ENABLED=true`.
- Neo4j health checking is disabled unless `NEO4J_HEALTH_CHECK_ENABLED=true`.

If Neo4j is not configured, the driver is not installed, or the server is down,
the application still starts and PostgreSQL Graph-RAG remains usable.

## Local Setup

Install the optional backend dependency when running the backend outside Docker:

```bash
cd backend
python -m uv sync --extra dev --extra neo4j
```

Start only the optional Neo4j service:

```powershell
$env:NEO4J_USER = "neo4j"
$env:NEO4J_PASSWORD = Read-Host "Local Neo4j password"
docker compose --profile neo4j up -d neo4j
```

Run the app with Neo4j enabled:

```powershell
$env:GRAPH_STORE_PROVIDER = "neo4j"
$env:GRAPH_RETRIEVAL_ENABLED = "true"
$env:NEO4J_URI = "bolt://neo4j:7687"
$env:NEO4J_PROJECTION_ENABLED = "true"
$env:BACKEND_UV_EXTRA_ARGS = "--extra neo4j"
docker compose --profile neo4j up --build backend worker frontend
```

Equivalent POSIX shell shape:

```sh
export NEO4J_USER=neo4j
printf "Local Neo4j password: "
stty -echo
read -r NEO4J_PASSWORD
stty echo
printf "\n"
export NEO4J_PASSWORD
export GRAPH_STORE_PROVIDER=neo4j
export GRAPH_RETRIEVAL_ENABLED=true
export NEO4J_URI=bolt://neo4j:7687
export NEO4J_PROJECTION_ENABLED=true
export BACKEND_UV_EXTRA_ARGS="--extra neo4j"
docker compose --profile neo4j up --build backend worker frontend
```

`BACKEND_UV_EXTRA_ARGS="--extra neo4j"` is required for Docker-based Neo4j
runs because the default backend image installs runtime dependencies with
`uv sync --no-dev` and intentionally does not include optional extras.

Use a local-only password for development and replace it before any shared
environment. Do not paste real Neo4j credentials into docs, logs, PR comments,
or shell transcripts.

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
6. Query once with `GRAPH_STORE_PROVIDER=postgres`.
7. Query the same request with `GRAPH_STORE_PROVIDER=neo4j`.
8. Compare that both responses are chunk-backed and that graph path summaries
   contain only safe refs and `source_chunk_ids`.

Useful checks:

```bash
docker compose config --services
docker compose --profile neo4j config --services
docker compose ps neo4j
```

The first command must not list `neo4j`; the profile command should list it.
