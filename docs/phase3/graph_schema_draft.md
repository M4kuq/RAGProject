# Graph Schema Draft

PR-45 defined this as a draft. PR-46 implements the first Graph-RAG schema foundation as Alembic revision `0012_graph_schema_index`.

## Implemented In PR-46

Implemented tables:

- `graph_entities`
- `graph_relations`
- `graph_entity_mentions`
- `graph_index_runs`
- `graph_retrieval_paths`

Implemented backend foundation:

- Alembic migration with upgrade/downgrade
- SQLAlchemy ORM models in `backend/app/db/graph_models.py`
- Pydantic DTOs in `backend/app/schemas/graph.py`
- `GraphRepository`
- `GraphIndexService` lifecycle skeleton
- `graph_index_build` future job type constant and payload schema
- disabled Graph-RAG system settings defaults

PR-46 still does not implement entity/relation extraction, graph retrieval, Graph-aware Router, Graph Citation generation, Graph Debug UI, OCR, image upload, AWS/S3/OIDC, external provider integration, or online evaluation.

## Design Rules

- Graph rows reference existing `document_versions`, `document_chunks`, `jobs`, and `retrieval_runs` where applicable.
- Graph tables do not store raw document text, raw chunk text, raw prompts, full context, PII, credential values, or secret values.
- Relation evidence is tracked with `source_document_chunk_id` plus `evidence_text_hash`.
- Mentions are tracked with `document_chunk_id`, `document_version_id`, `mention_text_hash`, and optional offsets.
- Graph retrieval paths store safe IDs, labels, score summaries, and `source_chunk_ids_json`, not raw evidence text.

## Tables

### `graph_entities`

Purpose: canonical graph nodes used by later extraction and retrieval.

Key fields:

- `graph_entity_id`
- `canonical_name`
- `entity_type`
- `aliases_json`
- `description`
- `metadata_json`
- `created_at`
- `updated_at`

Constraints/indexes:

- non-empty `canonical_name`
- non-empty `entity_type`
- unique index on lower canonical name + entity type
- index on `entity_type`
- PostgreSQL GIN index on `aliases_json`

### `graph_relations`

Purpose: safe source-backed edges between graph entities.

Key fields:

- `graph_relation_id`
- `source_entity_id`
- `target_entity_id`
- `relation_type`
- `relation_label`
- `confidence`
- `source_document_chunk_id`
- `evidence_text_hash`
- `metadata_json`
- `created_at`

Constraints/indexes:

- source and target entity FKs with cascade delete
- optional source chunk FK with `ON DELETE SET NULL`
- source and target cannot be the same entity
- confidence must be between 0 and 1 when present
- `evidence_text_hash` must be lowercase sha256 hex when present
- traversal indexes on source/target + relation type

### `graph_entity_mentions`

Purpose: map entity mentions back to document chunks and document versions.

Key fields:

- `graph_entity_mention_id`
- `graph_entity_id`
- `document_chunk_id`
- `document_version_id`
- `mention_text_hash`
- `mention_offset_start`
- `mention_offset_end`
- `confidence`
- `metadata_json`
- `created_at`

Constraints/indexes:

- entity/chunk/version FKs
- confidence range check
- offset non-negative/order checks
- `mention_text_hash` lowercase sha256 hex check when present
- indexes on entity, chunk, and version

### `graph_index_runs`

Purpose: graph indexing lifecycle state for PR-47 extractors.

Statuses:

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`
- `skipped`

Key fields:

- `graph_index_run_id`
- `document_version_id`
- `job_id`
- `status`
- `extractor_type`
- `extractor_version`
- `entity_count`
- `relation_count`
- `mention_count`
- `error_code`
- `error_message`
- `started_at`
- `finished_at`
- `metadata_json`

Constraints/indexes:

- document version FK with `ON DELETE SET NULL`
- job FK with `ON DELETE SET NULL`
- status check
- non-negative counts
- terminal timestamp checks
- failed status requires `error_code`
- indexes on document/status, status/created, and job

### `graph_retrieval_paths`

Purpose: future PR-48/PR-49 storage for safe graph path summaries.

Key fields:

- `graph_retrieval_path_id`
- `retrieval_run_id`
- `path_json`
- `score_breakdown_json`
- `source_chunk_ids_json`
- `created_at`

Constraints/indexes:

- retrieval run FK with cascade delete
- `path_json` and score JSON must be objects
- `source_chunk_ids_json` must be an array
- index on `retrieval_run_id`

## PR-47 Handoff

PR-47 should connect entity/relation extraction to this foundation through `GraphIndexService`. Extraction may internally read chunk text, but persisted graph state must continue using IDs, hashes, offsets, counts, confidence, and safe metadata only.

## PR-48 Handoff

PR-48 should implement graph lookup/traversal against these tables and store safe path summaries in `graph_retrieval_paths`. It must not add raw path evidence text to API responses or debug output.

## Known Limitations

- No extractor is implemented in PR-46.
- No graph retrieval strategy is implemented in PR-46.
- No public Graph-RAG API is added in PR-46.
- `graph_index_build` is a future job type skeleton and is not wired to a worker handler yet.
- The schema may evolve after PR-47/PR-48 query plans and extraction behavior are measured.
