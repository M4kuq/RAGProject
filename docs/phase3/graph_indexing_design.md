# Graph Indexing Design

PR-46 implemented the graph index state foundation. PR-47 connects that
foundation to a safe entity/relation extraction worker.

## Implemented Foundation

- `graph_index_runs` table
- `GraphIndexService` lifecycle methods
- `GraphRepository` lifecycle methods
- `graph_index_build` job type constant
- `GraphIndexJobPayload` DTO
- disabled graph settings defaults in `system_settings`

## Implemented In PR-47

- rule-based `EntityExtractionService`
- rule-based `RelationExtractionService`
- `GraphEntityNormalizer`
- `GraphIndexBuildHandler`
- default worker dispatcher registration for `graph_index_build`
- idempotent document-version rebuild for mentions and relations
- safe retry behavior for failed graph index runs

## Lifecycle

```text
queued -> running -> succeeded
queued -> running -> failed
queued -> skipped
queued/running -> cancelled
```

The lifecycle is updated through repository/service methods:

- `create_index_run_for_document_version`
- `mark_index_run_running`
- `record_index_summary`
- `mark_index_run_failed`

`record_index_summary` records counts only: entity, relation, and mention counts. It does not store extracted text.

## Job Type

Implemented job type:

```text
graph_index_build
```

The default worker dispatcher registers a graph handler. PR-47 does not
automatically enqueue graph indexing jobs from document ingest; callers must
create a `graph_index_build` job explicitly.

Safe payload fields:

- `document_version_id`
- `graph_index_run_id`
- `extractor_type`
- `extractor_version`
- `job_type`
- `reindex_policy`

Unsafe payload fields remain forbidden:

- raw document text
- raw chunk text
- raw prompt
- full context
- credential or secret values

## Settings

Graph indexing remains opt-in, while graph retrieval is enabled by default:

| Key | Default |
|---|---|
| `rag.graph.enabled` | `false` |
| `rag.graph.indexing.enabled` | `false` |
| `rag.graph.extractor.default` | `none` |
| `rag.graph.max_entities_per_chunk` | `20` |
| `rag.graph.max_relations_per_chunk` | `40` |
| `rag.graph.store_raw_evidence_text` | `false` |
| `rag.graph.retrieval.enabled` | `true` |

## Worker Flow

The handler:

1. Acquire a `graph_index_build` job.
2. Mark the corresponding `graph_index_runs` row `running`.
3. Read approved chunk text internally.
4. Persist entities, relations, mentions, hashes, refs, confidence, and counts.
5. Mark the run `succeeded` or `failed` with a safe error code/message.

## Transaction Boundary

Extraction/model work must not run inside long DB transactions. The worker should follow existing Worker / Job rules: short DB transactions, external I/O outside DB locks, idempotent retry, and safe terminal updates.

## Known Limitations

- No automatic graph job creation.
- No backfill job.
- No graph retrieval.
