# PR-52 Retrieval Result Cache Foundation

PR-52 adds a strategy-agnostic retrieval result cache for dense, sparse, hybrid,
graph, and Auto (`agentic_router`) retrieval runs. It caches retrieval result
references only. It does not cache answers, prompts, full context, raw chunk text,
or generated evidence.

## Scope

The cache wraps retrieval execution after the query has been hashed and the
strategy has been selected. On a cache hit, the service creates fresh
`retrieval_run_items` for the current run from cached chunk references, then
rebuilds response snippets and citation sources from the current database rows.
This preserves the existing `source_chunk_ids -> retrieval_run_items -> citations`
flow without storing display snippets in the cache payload.

The default setting is disabled:

- `RETRIEVAL_CACHE_ENABLED=false`
- `RETRIEVAL_CACHE_NAMESPACE=rag.retrieval`
- `RETRIEVAL_CACHE_TTL_SECONDS=300`

Requests may set `cache_bypass=true` to force the non-cached retrieval path.

## Cache Key

The versioned cache key is a SHA-256 hash over safe metadata:

- `cache_namespace`
- `strategy_type`
- `query_hash`
- `retrieval_settings_hash`
- `rerank_settings_hash`
- `embedding_model`
- `rerank_model`
- `active_document_fingerprint`
- `graph_index_fingerprint`
- `graph_store_provider`
- `top_k`
- `rerank_top_n`
- `user_visible_scope`
- `schema_version`

`query_hash` is derived from the raw query, but the raw query itself is not stored
in the key material, payload, trace, or admin debug output. `graph_store_provider`
is part of the key, so PostgreSQL graph retrieval and Neo4j graph retrieval do not
share entries.

## Invalidation

PR-52 intentionally avoids broad cache deletion. Invalidation relies on versioned
keys, TTL, and fingerprint changes.

The active document fingerprint changes when the active ready document set, active
document version, content hash, logical-document status, version status, chunk
count, chunk hash boundary, or chunk creation boundary changes. This covers
approved version switches, archive changes, and DB-visible reindex changes.

The graph index fingerprint changes when graph index runs for active versions
change status, run id, extractor metadata, counts, or updated timestamp. This
covers succeeded, failed, and retried graph index runs. Graph provider changes are
separate through `graph_store_provider`.

The retrieval and rerank settings hashes include strategy, request kind, filters,
hybrid/sparse/router/graph settings, model settings, and a hash of `rag.%`
system settings. Embedding and rerank model identifiers are also top-level key
fields so model changes naturally miss older entries.

TTL handles residual drift and operational cleanup. The first stale lookup runs
normal retrieval and replaces the entry.

## Payload Safety

The cache payload stores only:

- query hash and strategy metadata
- retrieval score summary
- result chunk refs (`document_chunk_id`)
- retrieval/rerank scores
- rank and selected flags
- safe score breakdown metadata
- safe graph path refs (`path_json`, `score_breakdown_json`, `source_chunk_ids_json`)
- cache created time and TTL

The payload must not store raw query, raw prompt, raw chunk text, snippets, full
context, raw graph evidence, answers, PII, secrets, tokens, credentials, or `.env`
values.

## Admin Debug

`retrieval_runs.cache_summary_json` records safe cache status for retrieval debug:

- `hit`
- `miss`
- `bypass`
- `stale`

The summary includes hashes, fingerprints, namespace, provider, and schema version.
It does not include raw query text, snippets, chunk text, prompts, or full context.

## Known Limitations

- Redis, semantic cache, answer cache, full-context cache, OCR cache, and
  user-personalized cache are intentionally out of scope.
- The default is disabled until operators explicitly enable the feature.
- LLM tool orchestrator, LangChain agentic, and LangGraph agentic ask strategies
  are left uncached in this PR to avoid widening retrieval behavior beyond the
  dense/hybrid/graph/Auto foundation.
