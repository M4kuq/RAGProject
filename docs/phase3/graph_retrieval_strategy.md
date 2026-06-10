# Graph Retrieval Strategy

PR-48 adds the first Graph-RAG retrieval layer on top of the PR-46 graph schema and PR-47 entity/relation index.

## Scope

Implemented in this PR:

- `GraphEntityLookupService` for safe entity lookup from query terms.
- `GraphPathSearchService` for bounded relation traversal.
- `GraphScoreCalculator` for graph score breakdowns.
- `GraphRetrievalStrategy` for graph source chunk candidates.
- `GraphRetrievalRepository` for graph lookup, bounded relation loading, active chunk filtering, and safe `graph_retrieval_paths` persistence.

Not implemented in this PR:

- Graph Citation Builder.
- Graph Path Validation UI.
- Graph Debug UI.
- Graph Evaluation.
- Graph + Vector Hybrid Fusion.
- OCR, image upload, AWS/S3/OIDC, or external provider integration.

## Bounded Traversal

Graph traversal is bounded by these settings:

- `max_start_entities`
- `max_depth`
- `max_paths`
- `max_relations_per_entity`
- `max_source_chunks`
- `timeout_ms`

The traversal starts from matched graph entities, loads relations in bounded batches, avoids cycles, and stops when path, depth, relation, source chunk, or timeout limits are reached.

## Safe Path Trace

`graph_retrieval_paths.path_json` stores only safe references:

- graph path id
- entity ids
- relation ids
- safe entity labels
- relation types
- source chunk ids
- path score
- depth

It must not store raw document text, raw chunk text, full context, raw prompt, PII, tokens, secrets, or raw evidence text.

## Score Breakdown

Graph candidates use `phase3.graph_score.v1` score breakdowns with:

- `retrieval_source = graph`
- `entity_match_score`
- `relation_score`
- `path_score`
- `source_chunk_score`
- `path_depth`
- `path_rank`
- `source_chunk_ids_count`
- `selected_flag`

## API Handoff

The lower retrieval layer is intentionally independent of `/rag/search` and `/rag/ask` plumbing. The next PR-48 step wires `strategy=graph` into `RagService`, saves `retrieval_run_items`, saves `graph_retrieval_paths`, and lets existing Context Budget / Evidence Pack logic consume graph-selected source chunks.
