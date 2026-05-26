# Phase2 DB Migration Plan

## Migration Chain

- `0003_phase2_strategy_trace`: PR-20 retrieval strategy and trace columns.
- `0004_eval_dataset_metrics`: PR-22 evaluation dataset and strategy metric schema.
- `0005_sparse_retrieval_fts`: PR-23 PostgreSQL full-text expression index for sparse retrieval.

Both migrations are additive. They do not delete existing Phase1 columns or change existing API contracts.

## PR-22 New Tables

### evaluation_datasets

| column | type | null | default |
|---|---|---:|---|
| `evaluation_dataset_id` | `BIGSERIAL` | no | |
| `dataset_name` | `VARCHAR(120)` | no | |
| `description` | `TEXT` | yes | |
| `version` | `VARCHAR(50)` | no | `v1` |
| `source_type` | `VARCHAR(50)` | no | `manual` |
| `status` | `VARCHAR(30)` | no | `active` |
| `metadata_json` | `JSONB` | yes | |
| `created_by` | `BIGINT` | yes | |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | no | `now()` |

### evaluation_cases

| column | type | null | default |
|---|---|---:|---|
| `evaluation_case_id` | `BIGSERIAL` | no | |
| `evaluation_dataset_id` | `BIGINT` | no | |
| `case_key` | `VARCHAR(120)` | no | |
| `question` | `TEXT` | no | |
| `expected_answer` | `TEXT` | yes | |
| `expected_keywords` | `JSONB` | yes | |
| `expected_document_ids` | `JSONB` | yes | |
| `expected_chunk_ids` | `JSONB` | yes | |
| `required_citation` | `BOOLEAN` | no | `true` |
| `tags` | `JSONB` | yes | |
| `metadata_json` | `JSONB` | yes | |
| `status` | `VARCHAR(30)` | no | `active` |

`UNIQUE(evaluation_dataset_id, case_key)` prevents duplicate fixture import.

## PR-22 Existing Table Extensions

### evaluation_runs

- `evaluation_dataset_id`
- `strategy_type`
- `trigger_type`
- `retrieval_settings_json`
- `strategy_metrics_summary_json`

### evaluation_run_items

- `evaluation_case_id`
- `strategy_type`
- `case_key`
- `latency_breakdown_json`
- `metric_summary_json`

### evaluation_results

- `metric_value`
- `metric_detail_json`
- `strategy_type`

`metric_score` and `details_json` remain for Phase1 compatibility.

## Constraints

`strategy_type` values match PR-20 `RetrievalStrategy`. `trigger_type` accepts `manual`, `ci`, `scheduled`, `post_deploy`, and `online_sampled_trace`. PR-22 stores these values but does not implement CI/scheduled/online execution.

## Downgrade

`0005_sparse_retrieval_fts` downgrade drops only the sparse FTS indexes and leaves `document_chunks` data unchanged. `0004_eval_dataset_metrics` downgrade drops PR-22 constraints, indexes, added columns, then `evaluation_cases` and `evaluation_datasets`. This removes PR-22 metadata but returns to the PR-21 schema.

## PR-23 Sparse Index

PR-23 adds these PostgreSQL indexes concurrently:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_document_chunks_content_fts
ON document_chunks
USING GIN (to_tsvector('simple', content_text));

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_document_chunks_content_fts_english
ON document_chunks
USING GIN (to_tsvector('english', content_text));
```

No generated column is added. The index uses existing `document_chunks.content_text` for search only. Raw chunk text is not copied into trace JSON, score breakdown JSON, logs, or API payload snapshots.

## Security

JSONB columns must not contain raw prompt, full context, raw chunk text, PII, secret, token, credential, API key, or password.