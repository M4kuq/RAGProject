# Graph Retrieval Strategy

PR-45 defines candidate graph retrieval behavior. It does not implement retrieval.

## Strategy Names

Candidate future strategy values:

- `graph`
- `graph_hybrid`
- `fallback_hybrid`
- `fallback_dense`

`graph` returns graph-supported chunk evidence. `graph_hybrid` combines graph paths with vector/hybrid retrieval.

## Retrieval Flow

```text
query
 -> QueryAnalyzer / QueryPlanner
 -> graph_entity_lookup
 -> relation traversal / multi-hop path search
 -> graph neighborhood expansion
 -> graph_score_breakdown
 -> source chunk mapping
 -> optional vector support retrieval
 -> merge/dedupe/rerank
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

Multi-hop path search should be bounded by:

- max hops
- max start entities
- max paths per query
- max neighbors per entity
- max source chunks per path
- time budget

Paths should be scored and summarized as IDs, labels, relation types, scores, hashes, and source chunk refs only.

## Graph Neighborhood Expansion

Neighborhood expansion can add related entities and chunks when direct lookup is weak. It should never grow unbounded; it must record why expansion occurred.

## Score Breakdown

Candidate `graph_score_breakdown` fields:

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

Graph retrieval should write standard `retrieval_runs` and `retrieval_run_items`. Graph-specific path summaries go to `graph_retrieval_paths` or safe trace JSON. Final citations still derive from selected retrieval run items.

## Relation To Rerank

Rerank may run after graph/vector merge. Rerank must not see raw graph payload dumps. It receives chunk-backed candidate summaries already eligible for context budgeting.

## Context Budget / Evidence Pack

Graph path candidates consume context budget through their source chunk-backed evidence. Evidence Pack groups may include graph path refs and source chunk refs while preserving citation mapping.
