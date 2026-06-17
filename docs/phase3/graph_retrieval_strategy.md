# Graph Retrieval Strategy

PR-45 defines candidate graph retrieval behavior. PR-48 starts implementing the first Graph-RAG retrieval layer on top of the PR-46 graph schema and PR-47 entity/relation index.

## Strategy Names

Candidate and implemented strategy values:

- `graph`
- `graph_hybrid`
- `fallback_hybrid`
- `fallback_dense`

`graph` returns graph-supported chunk evidence. `graph_hybrid` combines graph paths with vector/hybrid retrieval and remains a future extension.

## PR-48 Scope

Implemented in PR-48:

- `GraphEntityLookupService` for safe entity lookup from query terms.
- `GraphPathSearchService` for bounded relation traversal.
- `GraphScoreCalculator` for graph score breakdowns.
- `GraphRetrievalStrategy` for graph source chunk candidates.
- `GraphRetrievalRepository` for graph lookup, bounded relation loading, active chunk filtering, and safe `graph_retrieval_paths` persistence.

Still future work:

- Graph Citation Builder.
- Graph Path Validation UI.
- Graph Debug UI.
- Graph Evaluation.
- Graph + Vector Hybrid Fusion.
- OCR, image upload, AWS/S3/OIDC, or external provider integration.

## Retrieval Flow

```text
query
 -> QueryAnalyzer / QueryPlanner
 -> GraphRetrievalStrategy
 -> GraphStoreResolver
 -> GraphStore provider
 -> provider-independent GraphRetrievalResult
 -> source chunk mapping
 -> RDB final check
 -> retrieval_run_items
 -> graph_retrieval_paths
 -> Context Budget
 -> Evidence Pack
 -> citations
```

## `graph_entity_lookup`

Entity lookup should combine:

- exact canonical name and alias match
- sparse term match
- optional vector support over safe labels/descriptions
- query metadata hints
- document/version filters

Outputs are entity refs and scores, not raw text.

## Relation Traversal

Traversal should support:

- outgoing relations from matched entities
- incoming relations for reverse questions
- relation type filters inferred from query
- hop limit from settings
- source chunk support count
- relation confidence threshold

## Multi-Hop Path Search

Multi-hop path search is bounded by:

- `max_depth`
- `max_start_entities`
- `max_paths`
- `max_relations_per_entity`
- `max_source_chunks`
- `timeout_ms`

Paths are scored and summarized as IDs, safe labels, relation types, scores, and source chunk refs only.

## Graph Neighborhood Expansion

Neighborhood expansion can add related entities and chunks when direct lookup is weak. It should never grow unbounded; it must record why expansion occurred.

## PR-49 GraphStore Boundary

PR-49 keeps the PR-48 PostgreSQL traversal behavior but wraps it behind the
`GraphStore` interface:

- `GraphRetrievalStrategy` only resolves and calls a `GraphStore`.
- `PostgresGraphStore` owns PostgreSQL-backed entity lookup, bounded path search,
  mention-only fallback, graph scoring, and source chunk mapping.
- `Neo4jGraphStore` is an optional PR-50 backend. It lazily imports the Neo4j
  driver only when configured, keeps PostgreSQL as the source of truth, and
  returns a safe unavailable result when Neo4j is not configured or reachable.
- `GRAPH_STORE_PROVIDER` defaults to `postgres`; setting it to `neo4j` must not
  break application startup or non-graph retrieval paths.

The common DTO surface is provider-neutral:

- `GraphNodeRef`
- `GraphRelationRef`
- `GraphEvidenceRef`
- `GraphPath`
- `GraphRetrievalResult`

`GraphPath` carries `source_chunk_ids`, `safe_entity_labels`, `relation_types`,
and a safe `score_breakdown` so PR-50 can map Neo4j paths into the same shape.

## Safe Path Trace

`graph_retrieval_paths.path_json` stores only safe references:

- schema version
- provider
- graph path id
- node refs
- relation refs
- evidence refs
- source chunk ids
- safe entity labels
- relation types
- path score
- depth
- safe score breakdown

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

Future `graph_score_breakdown` fields may include:

- entity_match_score
- relation_confidence_score
- path_coherence_score
- source_support_score
- vector_support_score
- freshness_score
- final_graph_score
- reason_codes

## Graph + Vector Hybrid

`graph_hybrid` should merge graph path evidence with dense/sparse/hybrid chunk candidates:

1. Run graph lookup/traversal.
2. Resolve supporting source chunks.
3. Run vector/hybrid retrieval using the original or planned query.
4. Merge by source chunk ID.
5. Boost chunks that support high-confidence paths.
6. Preserve dense/sparse/graph score breakdown.
7. Pass merged candidates to Context Budget and Evidence Pack.

## Fallback Strategy

Fallbacks should be deterministic:

- if graph is disabled, use `fallback_hybrid` when hybrid is available
- if graph finds no supported path, use `fallback_hybrid` or `fallback_dense`
- if graph traversal exceeds budget, return partial safe graph trace and fallback
- if citation validation fails, do not use graph path as grounding evidence

## No-Context Behavior

For `/rag/search`, zero graph results can return `items=[]` with safe summary. For `/rag/ask`, no chunk-backed graph or fallback evidence should use the existing no-context behavior rather than generating unsupported answers.

## Retrieval Run Integration

Graph retrieval writes standard `retrieval_runs` and `retrieval_run_items`. Graph-specific path summaries go to `graph_retrieval_paths`. Final citations still derive from selected retrieval run items.

## Relation To Rerank

Rerank may run after graph/vector merge. Rerank must not see raw graph payload dumps. It receives chunk-backed candidate summaries already eligible for context budgeting.

## Context Budget / Evidence Pack

Graph path candidates consume context budget through their source chunk-backed evidence. Evidence Pack groups may include graph path refs and source chunk refs while preserving citation mapping.

## API Handoff

Current graph retrieval requests already return chunk-backed candidates through
the existing `/rag/search` and `/rag/ask` pipeline when `strategy=graph` is
enabled. Graph-aware router selection and Auto-compatible fallback behavior use
the same source chunk bridge.

The implemented strategy resolves each path back to `document_chunk_id`, so
future citation, debug, and evaluation work can connect to the same
chunk-backed candidate shape without exposing raw graph evidence.
