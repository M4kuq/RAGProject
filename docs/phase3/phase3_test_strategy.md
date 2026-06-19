# Phase3 Test Strategy

PR-46 added the first executable GraphRAG foundation tests. PR-47 added the
first executable extraction pipeline and worker tests. PR-48 through PR-54
extend the same safety pattern into retrieval, routing, citation/debug, cache,
evaluation, optional Neo4j, demo docs, and smoke.

## PR-46 Tests

Added/expected coverage:

- graph ORM tables exist in metadata
- graph tables do not contain raw text/prompt/context/secret columns
- GraphRepository creates entities, relations, mentions, index runs, and retrieval path summaries
- GraphIndexService handles queued/running/succeeded/failed lifecycle
- failed error messages are redacted
- Pydantic DTOs reject invalid sha256 hashes and unsafe metadata keys
- Graph settings defaults are disabled
- `graph_index_build` is supported by worker configuration and the default dispatcher
- PostgreSQL-only checks validate migration head, tables, constraints, indexes, and seeded settings when a PostgreSQL DB is available

## PR-47 Tests

Added/expected coverage:

- rule-based entity extraction creates safe entity and mention candidates
- rule-based relation extraction maps relations to source chunk refs and hashes
- graph index rebuild replaces version-level mentions and relations idempotently
- worker registration processes `graph_index_build` jobs
- failed graph index runs retry with a new run
- extraction failures mark jobs/runs failed without leaking raw text

## Migration / DB

PR-46 migration checks should cover:

- upgrade to `0012_graph_schema_index`
- downgrade back to `0011_tool_result_compression`
- table existence
- FK constraints
- CHECK constraints
- indexes
- invalid status rejection
- invalid confidence rejection
- invalid hash rejection
- negative count rejection

## PR-48 Through PR-53 Tests

Added/expected coverage:

- explicit `graph` retrieval returns source chunk-backed candidates.
- graph traversal is bounded by max depth, path count, relation count, source
  chunk count, and timeout settings.
- graph-aware router selects graph only when enabled and falls back safely.
- GraphStore DTOs keep PostgreSQL and optional Neo4j provider behavior
  compatible.
- Neo4j unavailable paths return safe reason codes without breaking default
  PostgreSQL GraphRAG.
- graph citation validation maps paths through retrieval run items before
  citations.
- admin graph debug returns safe refs, counts, status, and reason codes only.
- retrieval cache stores result references and safe graph path summaries only.
- cache keys include graph fingerprint and graph store provider.
- evaluation compares dense, hybrid, agentic_router, graph_postgres, and
  optional graph_neo4j with safe reports.

## PR-54 Smoke / Docs Checks

PR-54 adds non-destructive smoke scripts and docs acceptance checks rather than
new retrieval behavior:

- `docker compose config --quiet`
- `docker compose --profile neo4j config --quiet`
- final GraphRAG docs and helper scripts exist
- docs include PostgreSQL source-of-truth / Neo4j read-model language
- docs include cache and evaluation summary language
- `.env.example` and Compose expose opt-in graph/cache/Neo4j env names without
  requiring them
- optional deep mode can run the queue helper with `--dry-run` inside an already
  running backend container

The smoke must not delete volumes, reset databases, print `.env` values, call
external providers, require Neo4j by default, or store raw prompt/chunk/document
text.

OCR, multimodal, external provider, and AWS tests remain opt-in until their respective PRs.
