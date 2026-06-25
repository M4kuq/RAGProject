# GraphRAG Final README

This is the operator-facing GraphRAG entry point for PR-54. It explains the
implemented local GraphRAG path, the default Neo4j graph read model,
PostgreSQL source-of-truth graph store, retrieval cache behavior, evaluation,
demo checks, security
constraints, and the PR-55+ handoff.

## Current Status

PR-54 does not add a new retrieval strategy or metric. It finalizes the
portfolio/demo handoff for the GraphRAG work delivered through PR-46 to PR-53.

Implemented GraphRAG scope:

- graph schema and graph index run lifecycle
- rule-based entity/relation extraction through `graph_index_build`
- explicit `/api/v1/rag/search` and `/api/v1/rag/ask` `strategy=graph`
- graph-aware `agentic_router` shortcut when graph retrieval and router flags are enabled
- Neo4j-backed `GraphStore` as the default read-model provider
- PostgreSQL-backed `GraphStore` as the source-of-truth fallback provider
- chunk-backed graph citation bridge and admin-safe graph trace endpoint
- retrieval result cache for dense, sparse, hybrid, and graph paths
- evaluation comparison targets for `dense`, `hybrid`, `agentic_router`,
  `graph_postgres`, and `graph_neo4j`

Not implemented in PR-54:

- a new `graph_hybrid` public strategy with full graph/vector fusion
- OCR, image, or multimodal retrieval
- new external provider wiring
- Redis cache implementation
- S3, OIDC, AWS deployment, or production alerting

## Architecture

PostgreSQL is the source of truth. Neo4j is only a read model.

```text
document_versions / document_chunks
  -> graph_index_build job
  -> PostgreSQL graph_entities / graph_entity_mentions / graph_relations
  -> graph_retrieval_paths
  -> retrieval_run_items
  -> citations
```

Default Neo4j projection:

```text
PostgreSQL graph tables
  -> Neo4jProjectionService
  -> Neo4j read model
  -> Neo4jGraphStore
  -> provider-neutral GraphPath DTO
  -> source_chunk_ids
  -> retrieval_run_items / citations
```

The source-of-truth rule is deliberate:

- PostgreSQL already owns document versions, chunks, active/archived state,
  retrieval runs, citations, evaluation runs, jobs, and migrations.
- Graph rows must stay transactionally tied to the document version and chunk
  references used for citation validation.
- Neo4j can accelerate or explain traversal, but it must be rebuildable from
  PostgreSQL projection data.
- If Neo4j is absent or unhealthy, application startup and PostgreSQL GraphRAG
  continue to work.

## End-To-End Flow

```text
ingest
  -> extraction / chunking
  -> vector indexing
  -> graph_index_build
  -> entity / relation extraction
  -> PostgreSQL graph index
  -> Neo4j projection
  -> graph retrieval
  -> source_chunk_ids final check
  -> retrieval_run_items
  -> graph path trace
  -> Context Budget
  -> Evidence Pack
  -> citations / answer
  -> retrieval cache summary
  -> strategy comparison evaluation
```

## Retrieval Mode Selection

Use `dense` when the question is primarily semantic and the answer likely lives
inside one chunk.

Use `hybrid` when keyword matching matters, exact product/config terms matter,
or dense retrieval alone is likely to miss terms.

Use `agentic_router` when the user wants automatic strategy selection and the
bounded router/tool flow. If graph retrieval and graph router flags are enabled,
strong relation or multi-hop signals can route to graph. If graph produces no
chunk-backed evidence, router-selected graph may fall back to configured dense
or hybrid retrieval.

Use explicit `graph_neo4j` for relation, dependency, ownership, "how A relates
to B", and multi-hop checks where graph index data is expected to exist.
`graph` remains available and follows the configured graph provider. If graph
cannot produce chunk-backed evidence, the response records the provider and
reason codes before falling back to the configured safe base strategy. That
safe fallback is reported as the actual base retriever (`dense` or `hybrid`) in
the run's effective `strategy_type` and `execution_strategy`, so provider
comparison does not count a base-retrieved answer as a GraphRAG success.

`graph_hybrid` remains a PR-55+ candidate. Current GraphRAG can fall back to
hybrid but does not expose full graph/vector fusion as a separate public
strategy.

## Local Settings

Default Compose settings:

```text
GRAPH_RETRIEVAL_ENABLED=true
GRAPH_STORE_PROVIDER=neo4j
GRAPH_ROUTER_ENABLED=true
RETRIEVAL_CACHE_ENABLED=false
NEO4J_PROJECTION_ENABLED=true
NEO4J_HEALTH_CHECK_ENABLED=true
```

Set `GRAPH_RETRIEVAL_ENABLED=false` only as an operator kill-switch. New
explicit `graph`, `graph_postgres`, and `graph_neo4j` requests then return
`strategy_not_enabled` (409). Saved duplicate-message replays still return the
original completed response without re-running graph retrieval.

PostgreSQL GraphRAG provider override:

```powershell
$env:GRAPH_RETRIEVAL_ENABLED = "true"
$env:GRAPH_ROUTER_ENABLED = "true"
$env:GRAPH_STORE_PROVIDER = "postgres"
$env:RETRIEVAL_CACHE_ENABLED = "true"
docker compose up --build
```

```sh
export GRAPH_RETRIEVAL_ENABLED=true
export GRAPH_ROUTER_ENABLED=true
export GRAPH_STORE_PROVIDER=postgres
export RETRIEVAL_CACHE_ENABLED=true
docker compose up --build
```

Queue graph indexing for active ready local demo documents:

```powershell
docker compose exec -T backend python -m app.scripts.queue_graph_index_builds
```

```sh
docker compose exec -T backend python -m app.scripts.queue_graph_index_builds
```

The queue helper emits only document version IDs, job IDs, and counts. It does
not print chunk text, document text, prompts, graph evidence, credentials, or
`.env` values.

## Neo4j Read Model

Neo4j is part of the default Compose stack. The local default password is the
non-secret development value `change-me-local`; override it for shared
environments. Neo4j startup is not a hard application dependency: if it is
temporarily unavailable, `graph_neo4j` records visible reason codes and falls
back to PostgreSQL graph, then to the configured safe base retrieval path if no
graph evidence exists. A base fallback remains visible as a fallback and is not
recorded as a GraphRAG win.

Do not paste real Neo4j credentials into docs, logs, PR comments, or shell
transcripts. Set any shared-environment password in your shell without
committing it.

```powershell
$env:NEO4J_PASSWORD = "<local override>"
docker compose up -d --build
```

```sh
export NEO4J_PASSWORD="<local override>"
docker compose up -d --build
```

The default stack already sets Neo4j projection and provider selection:

```powershell
docker compose config --services
docker compose ps neo4j
```

```sh
docker compose config --services
docker compose ps neo4j
```

If Neo4j is not configured, the driver is absent, the server is down, or the
projection has no matching paths, the fallback reason is surfaced in the
retrieval response and graph trace.

## Cache Behavior

The retrieval cache is disabled by default. When enabled, it caches retrieval
references, scores, safe graph path refs, hashes, fingerprints, provider, and
TTL metadata. It does not cache answers, prompts, raw query text, snippets, raw
chunk text, full context, raw graph evidence, PII, credentials, tokens, or
`.env` values.

Graph cache keys include the graph store provider and graph index fingerprint.
This prevents PostgreSQL and Neo4j graph runs from sharing cache entries and
invalidates graph cache entries when active graph index state changes. Dense,
sparse, and hybrid keys are not invalidated by unrelated graph index
maintenance.

## Evaluation Summary

The `phase3_graph_multi_hop` fixture is a small safe synthetic dataset. PR-53
evaluation can compare:

- `dense`
- `hybrid`
- `agentic_router`
- `graph_postgres`
- `graph_neo4j`

Graph metrics are safe summaries:

- `graph_path_relevance`
- `graph_citation_coverage`
- `multi_hop_answerability`
- `cache_hit_rate`
- `cache_saved_latency`
- `entity_relation_quality_summary`

When Neo4j is not configured, unavailable, or unprojected, `graph_neo4j` uses
PostgreSQL graph as a visible fallback when PostgreSQL graph sources can answer
and records `neo4j_to_postgres_fallback`. If neither graph provider has usable
sources, the run records safe reason codes and uses the configured safe base
retriever. The graph target remains transparent, but the effective execution is
reported as `dense` or `hybrid` so the fallback cannot inflate GraphRAG/provider
success rates.

## Verification

Non-destructive PR-54 smoke:

```powershell
scripts\smoke_phase3_graph_rag.ps1
```

```sh
sh scripts/smoke_phase3_graph_rag.sh
```

The smoke checks Compose config, required docs, the GraphRAG fixture, helper
script presence, and optional running health endpoints. It does not delete
volumes, print environment values, call external providers, or require Neo4j.

## Security Checklist

Safe to show:

- IDs, hashes, counts, scores, status values, strategy labels
- safe entity labels and relation types
- source chunk IDs and retrieval run item IDs
- cache status, fingerprints, provider names, schema versions
- graph citation coverage ratios and reason codes

Never show or commit:

- raw prompt text
- raw chunk text
- raw document text
- full context
- raw graph evidence
- answers copied from private documents
- PII
- secrets, tokens, credentials, cookies, API keys, Neo4j credentials, or `.env` values
- database, Qdrant, Neo4j, or upload volume dumps

## Troubleshooting

If `strategy=graph` returns `strategy_not_enabled`, confirm
`GRAPH_RETRIEVAL_ENABLED=true` reached backend and worker. With Docker Compose,
restart backend and worker after changing env values.

If graph search returns no context, queue graph index jobs, wait for worker
completion, and confirm the active document version has succeeded graph index
runs. Explicit graph does not silently fall back.

If router does not select graph, confirm both `GRAPH_RETRIEVAL_ENABLED=true` and
`GRAPH_ROUTER_ENABLED=true`, then use a relation or multi-hop synthetic query.

If Neo4j comparison falls back or is unavailable, confirm the `neo4j` profile is
running, the backend image was built with `BACKEND_UV_EXTRA_ARGS="--extra
neo4j"`, and projection is enabled. PostgreSQL GraphRAG is still valid when
Neo4j is absent.

If cache does not hit, confirm `RETRIEVAL_CACHE_ENABLED=true`, avoid
`cache_bypass=true`, and use the same strategy/provider/settings against the
same active corpus before TTL expiry.

## Handoff

PR-54 closes the text GraphRAG portfolio/demo scope. PR-55+ candidates are
tracked in [graph_rag_next_phase_handoff.md](graph_rag_next_phase_handoff.md).
