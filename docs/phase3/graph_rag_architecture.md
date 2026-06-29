# Graph-RAG Architecture

PR-46 started the implementation path by adding the graph schema and index run
foundation. PR-47 through PR-53 connect safe extraction, graph retrieval,
graph-aware routing, provider-neutral GraphStore DTOs, Neo4j read-model
projection, graph citation/debug, retrieval cache, and evaluation. C2a makes
Neo4j the default read-model provider without changing extraction strategy.

## Phase2 Relationship

Phase2 and Phase2.5 remain the base retrieval path:

- dense retrieval
- sparse retrieval
- hybrid retrieval
- agentic router
- LLM tool-calling Auto
- Context Budget
- Evidence Pack
- Tool Result Compression

GraphRAG plugs into this stack instead of bypassing it.

## PR-54 Current Flow

```text
document ingest / approved version
  -> chunk extraction and indexing
  -> graph_index_build job
  -> entity / relation extraction
  -> PostgreSQL graph tables
  -> Neo4j projection
  -> explicit graph retrieval or graph-aware router selection
  -> retrieval_run_items backed by source chunks
  -> graph_retrieval_paths safe path refs
  -> Context Budget / Evidence Pack
  -> citation builder
  -> admin-safe graph trace / evaluation / cache summaries
```

## Source Of Truth And Read Model

```text
PostgreSQL
  graph_entities
  graph_relations
  graph_entity_mentions
  graph_index_runs
  retrieval_runs / retrieval_run_items / citations
      |
      | projection, rebuildable from PostgreSQL
      v
Neo4j
  entity and chunk nodes
  mention and relation relationships
  safe IDs, labels, relation types, hashes, counts, source chunk refs
```

PostgreSQL is the source of truth because it already owns document lifecycle,
version state, jobs, retrieval runs, citations, migrations, tests, and the local
demo durability boundary. Neo4j is a traversal-oriented read model that can be
rebuilt from PostgreSQL. It is part of the default stack, but it must not block
application startup or PostgreSQL graph retrieval when temporarily unavailable.

## PR-46 Boundary

Implemented:

- graph tables
- ORM models
- DTOs
- repository methods
- index run lifecycle skeleton
- graph job type constant reserved for PR-47 worker wiring
- disabled settings defaults
- tests and docs

Not implemented:

- entity/relation extraction
- graph retrieval
- graph-aware router
- graph citation generation
- graph debug UI
- OCR/multimodal
- production/AWS expansion

## PR-47 Integration

PR-47 connects rule-based extraction to `GraphIndexService` through the
`graph_index_build` worker job. It stores graph labels, refs, hashes, offsets,
counts, and confidence only.

## PR-48 Through PR-53 Integration

PR-48 adds graph lookup/traversal and routing decisions.

PR-49 adds the GraphStore abstraction so PostgreSQL graph traversal is not
hard-wired into `GraphRetrievalStrategy`. It also prepares a provider-neutral
`graph_retrieval_paths.path_json` shape.

PR-50 adds Neo4j as an optional read-model backend behind the same `GraphStore`
interface. Neo4j stores safe projection data only and maps results back through
`source_chunk_ids`.

PR-51 validates graph paths against retrieval run items and exposes admin-safe
graph trace fields.

PR-52 adds a safe retrieval result cache. Graph cache keys include graph index
fingerprint and graph store provider so PostgreSQL and Neo4j results do not
share cache entries.

PR-53 adds cache-aware strategy evaluation for dense, hybrid, agentic_router,
graph_postgres, and optional graph_neo4j targets.

All graph results must pass through Context Budget and Evidence Pack before
answer generation. Tool outputs for future graph tools must be compressed and
bounded before planner visibility.

## Evidence Policy

Graph evidence is represented by IDs, hashes, source refs, labels, confidence, and score summaries. Raw document text, raw chunk text, full context, raw prompt material, PII, token values, and secret values are not persisted to graph tables or exposed through API/debug output.
