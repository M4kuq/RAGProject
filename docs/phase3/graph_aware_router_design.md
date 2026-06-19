# Graph-aware Router Design

PR-45 defined how the existing QueryAnalyzer, QueryPlanner, StrategyRouter, and
Auto path should grow to include graph strategies. Later PRs added graph-aware
routing for the implemented `graph` path. PR-54 documents this boundary; it
does not add `graph_hybrid`.

## Existing Foundation

Phase2 already has:

- `QueryAnalyzer` for intent, keyword-heavy, ambiguity, version hints, and metadata hints.
- `QueryPlanner` for safe query plan trace and candidate strategies.
- `StrategyRouter` for explicit `agentic_router` routing with dense/hybrid fallback.
- `llm_tool_orchestrator` for retrieval-only Auto.
- safe trace redaction and admin Retrieval Debug summaries.

Graph-aware routing should extend these contracts instead of creating a parallel router.

## Graph-Oriented Query Detection

Candidate signals:

| Signal | Examples | Router impact |
|---|---|---|
| Multi-hop | asks how A relates to B through another concept | prefer `graph` when graph is enabled and source-backed paths exist |
| Relation query | asks dependency, ownership, cause, compatibility, sequence | prefer `graph` |
| Entity comparison | asks differences between named entities/versions | prefer `graph` when version-aware source support exists, otherwise `hybrid` |
| Entity-centric | asks everything about one named system or concept | prefer `graph` when entity/path confidence is high, otherwise `hybrid` |
| Exact identifier | error code, API name, config key | hybrid first, graph if entity match exists |
| Version-specific | old/new/current comparison | graph only if version support is available |

## Strategy Selection

Implemented strategy values and labels:

- `dense`
- `hybrid`
- `agentic_router`
- `llm_tool_orchestrator`
- `graph`
- `fallback_hybrid`
- `fallback_dense`

Future candidate:

- `graph_hybrid`

Selection rules should be deterministic before any optional LLM planning:

1. If graph is disabled, never select graph.
2. If query has strong relation or multi-hop signal and graph index is ready,
   select `graph`.
3. If query has exact entity signal but graph path confidence is low, select `hybrid`.
4. If graph traversal is over budget, fallback to `fallback_hybrid` or `fallback_dense`.
5. If selected graph path lacks source chunk support, do not use it for answer grounding.

## QueryAnalyzer / QueryPlanner Delta

Future PRs may add safe fields:

- `multi_hop_flag`
- `relation_query_flag`
- `entity_comparison_flag`
- `candidate_entity_count`
- `candidate_relation_types`
- `graph_candidate_strategies`

These fields must not include raw query text beyond already redacted preview rules.

## LLM Tool Orchestrator Future Tool

Future `graph_search` tool can be made available to `llm_tool_orchestrator` only after graph retrieval and citation validation pass tests. Tool output must be compressed and may include:

- entity refs
- relation refs
- path refs
- hop count
- score breakdown
- source chunk IDs
- safe reason codes

It must not include raw document text, raw chunk text, full context, raw prompt material, PII, credential values, or secret values.

## Max Graph Traversal Budget

Candidate settings:

| Setting | Purpose |
|---|---|
| `rag.graph.enabled` | master graph strategy flag |
| `rag.graph.max_hops` | hard traversal depth |
| `rag.graph.max_start_entities` | entity lookup bound |
| `rag.graph.max_neighbors_per_entity` | graph explosion guard |
| `rag.graph.max_paths` | output path bound |
| `rag.graph.max_source_chunks_per_path` | citation and budget guard |
| `rag.graph.max_traversal_ms` | latency guard |
| `rag.graph.min_relation_confidence` | hallucinated relation guard |

## Trace / Debug UI Fields

Safe trace candidates:

- selected graph strategy
- fallback reason
- graph enabled flag
- entity candidate count
- relation candidate count
- path candidate count
- selected path count
- dropped path count
- max hop budget
- traversal budget exhausted flag
- source chunk support count
- graph score breakdown summary
- citation validation status

## Fallbacks

Fallback names should be explicit in trace:

- `fallback_hybrid` when graph is unavailable or low confidence but hybrid is usable
- `fallback_dense` when hybrid is unavailable or disabled
- `no_context` when no chunk-backed evidence remains

## Non-Goals

PR-54 does not add a public `graph_hybrid` strategy, new router signals beyond
the existing GraphRAG routing path, new tool classes, or viewer-facing graph
debug fields.
