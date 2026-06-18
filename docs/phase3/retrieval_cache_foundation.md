# PR-52 Retrieval Result Cache Foundation

PR-52 adds a strategy-agnostic retrieval result cache for dense, sparse, hybrid,
and graph retrieval runs. It caches retrieval result references only. It does not
cache answers, prompts, full context, raw chunk text, or generated evidence.

Auto (`agentic_router`) requests intentionally bypass the cache in this PR. They
run live retrieval every time and record `strategy_not_cacheable` rather than
`hit` or `miss`, because replaying their planner/fallback trace safely requires a
separate trace-metadata design.

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

For unfiltered requests, the active document fingerprint uses the
`rag.retrieval_cache.corpus_marker` invalidation marker rather than joining and
aggregating the full active `document_chunks` corpus on every lookup. Active
version switches, archives, and active-version chunk mutations bump that marker.
For filtered `logical_document_ids` requests, the scoped fingerprint remains
chunk-aware and includes chunk count/hash boundaries for those documents. This
covers approved version switches, archive changes, and DB-visible reindex changes
without making the default cache-hit path scan the full chunk corpus.

The graph index fingerprint is included only for graph-dependent retrieval paths.
Dense, sparse, and hybrid cache keys use a stable placeholder so graph indexing
maintenance does not invalidate unrelated retrieval entries. For graph paths, the
fingerprint changes when graph index runs for active versions change status, run
id, extractor metadata, counts, or updated timestamp. This covers succeeded,
failed, and retried graph index runs. Graph provider changes are separate through
`graph_store_provider`.

The retrieval and rerank settings hashes include strategy, request kind, filters,
hybrid/sparse/router/graph settings, model settings, and a hash of `rag.%`
system settings. Embedding and rerank model identifiers are also top-level key
fields so model changes naturally miss older entries.

TTL handles residual drift. Expired persistent cache rows are pruned
opportunistically on store, so one-off queries do not grow the table without
bound.

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
  dense/hybrid/graph foundation.
- Auto (`agentic_router`) is also left uncached until cache payloads can safely
  preserve and replay planner/fallback trace metadata.
