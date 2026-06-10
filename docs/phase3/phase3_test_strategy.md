# Phase3 Test Strategy

PR-46 added the first executable Graph-RAG foundation tests. PR-47 adds the
first executable extraction pipeline and worker tests.

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

## Later PRs

PR-48 adds graph retrieval tests.

PR-49 adds graph citation/path validation tests.

PR-50 adds Graph Debug UI and evaluation tests.

OCR, multimodal, external provider, and AWS tests remain opt-in until their respective PRs.
