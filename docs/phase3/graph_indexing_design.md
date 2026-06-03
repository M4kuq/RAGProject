# Graph Indexing Design

PR-46 implements the graph index state foundation. It does not run entity/relation extraction.

## Implemented Foundation

- `graph_index_runs` table
- `GraphIndexService` skeleton
- `GraphRepository` lifecycle methods
- `graph_index_build` future job type constant
- `GraphIndexJobPayload` DTO
- disabled graph settings defaults in `system_settings`

## Lifecycle

```text
queued -> running -> succeeded
queued -> running -> failed
queued -> skipped
queued/running -> cancelled
```

PR-46 supports lifecycle state changes through repository/service methods:

- `create_index_run_for_document_version`
- `mark_index_run_running`
- `record_index_summary`
- `mark_index_run_failed`

`record_index_summary` records counts only: entity, relation, and mention counts. It does not store extracted text.

## Job Type Skeleton

Future job type:

```text
graph_index_build
```

PR-46 only adds the constant and payload schema. The default worker dispatcher does not register a graph handler yet, and PR-46 does not automatically enqueue graph indexing jobs.

Safe payload fields:

- `document_version_id`
- `graph_index_run_id`
- `job_type`

Unsafe payload fields remain forbidden:

- raw document text
- raw chunk text
- raw prompt
- full context
- credential or secret values

## Settings

PR-46 seeds these disabled defaults:

| Key | Default |
|---|---|
| `rag.graph.enabled` | `false` |
| `rag.graph.indexing.enabled` | `false` |
| `rag.graph.extractor.default` | `none` |
| `rag.graph.max_entities_per_chunk` | `20` |
| `rag.graph.max_relations_per_chunk` | `40` |
| `rag.graph.store_raw_evidence_text` | `false` |
| `rag.graph.retrieval.enabled` | `false` |

## PR-47 Handoff

PR-47 should add extractor implementations and a worker handler. The handler should:

1. Acquire a `graph_index_build` job.
2. Mark the corresponding `graph_index_runs` row `running`.
3. Read approved chunk text internally.
4. Persist entities, relations, mentions, hashes, refs, confidence, and counts.
5. Mark the run `succeeded` or `failed` with a safe error code/message.

## Transaction Boundary

Extraction/model work must not run inside long DB transactions. The worker should follow existing Worker / Job rules: short DB transactions, external I/O outside DB locks, idempotent retry, and safe terminal updates.

## Known Limitations

- No extractor implementation.
- No automatic graph job creation.
- No graph handler registration.
- No backfill job.
- No graph retrieval.
