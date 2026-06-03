# Graph Indexing Design

PR-45 defines the graph indexing plan. Implementation starts in PR-46/PR-47.

## Responsibilities

Graph indexing converts safe extraction results into queryable graph state:

- create or update canonical entities
- create entity mentions linked to chunks and versions
- create relations linked to entities and source chunks
- update `graph_index_runs` lifecycle
- maintain stale/version-aware state
- expose only safe summaries to debug/evaluation

## Pipeline

```text
document_version ready
 -> graph_index_run created
 -> load chunk refs internally
 -> run extractor
 -> normalize entities
 -> validate relations
 -> upsert entities / mentions / relations
 -> write safe counts and terminal status
```

## Transaction Boundary

Follow existing Worker principles:

- Do not run external extraction calls inside a DB transaction.
- Use short transactions for state transitions and writes.
- Make graph writes idempotent by document version and normalized keys.
- Treat graph index state and job state as related but separate.

## Idempotency

Candidate natural keys:

- entity: `(entity_type, canonical_name)` plus alias metadata.
- mention: `(graph_entity_id, document_chunk_id, mention_text_hash)`.
- relation: `(source_entity_id, target_entity_id, relation_type, source_document_chunk_id, evidence_text_hash)`.
- index run: `(document_version_id, extractor_type, extractor_version, reindex policy)` with service-level active-run guard.

## Version Handling

Graph rows should retain `document_version_id` or source chunk references that resolve to version. Retrieval should prefer active versions by default and allow version-aware graph search later.

## Stale Graph Handling

A graph path is stale if all supporting source chunks belong to inactive or archived document versions and the request is not version-aware. Stale paths can remain visible to admin debug as safe summaries but should not ground default viewer answers.

## Graph Store Choice

The first implementation should use PostgreSQL tables. A dedicated graph database is out of scope until the schema, retrieval behavior, and evaluation metrics are stable.

## Backfill

Graph backfill must be an explicit worker job, not a migration side effect. Backfill should support dry-run counts and safe summaries.

## Observability

Safe graph index run summaries can include:

- document version ID
- extractor type/version
- status
- entity count
- relation count
- mention count
- skipped count
- safe error code
- duration

Do not log raw document text, raw chunk text, full context, prompt material, PII, credential values, or secret values.
