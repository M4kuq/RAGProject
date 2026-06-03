# Graph Schema Draft

PR-45 defines the draft only. It does not create an Alembic migration or runtime tables.

## Design Rules

- Graph rows must reference existing `document_versions`, `document_chunks`, `retrieval_runs`, and `retrieval_run_items` where applicable.
- Do not store raw document text, raw chunk text, raw prompts, full context, PII, credential values, or secret values.
- Use source chunk IDs, version IDs, hashes, counts, confidence, and metadata summaries.
- Graph rows are invalidated or superseded when document versions change.
- Graph citation must be able to return to chunk-backed citations.

## Candidate Tables

### `graph_entities`

| Column | Draft type | Notes |
|---|---|---|
| `graph_entity_id` | BIGSERIAL PK | Internal graph entity ID. |
| `canonical_name` | TEXT NOT NULL | Normalized display name, bounded and sanitized. |
| `entity_type` | VARCHAR(80) NOT NULL | Example categories: concept, system, person-like label, organization-like label, version, file, API, setting. |
| `aliases_json` | JSONB NOT NULL DEFAULT '[]' | Bounded aliases; no raw source passage. |
| `description` | TEXT NULL | Optional safe summary, not raw evidence. |
| `metadata_json` | JSONB NOT NULL DEFAULT '{}' | Counts, hashes, provenance flags, extractor metadata. |
| `created_at` | TIMESTAMPTZ NOT NULL | Creation time. |
| `updated_at` | TIMESTAMPTZ NOT NULL | Update time. |

### `graph_relations`

| Column | Draft type | Notes |
|---|---|---|
| `graph_relation_id` | BIGSERIAL PK | Internal relation ID. |
| `source_entity_id` | BIGINT FK | References `graph_entities`. |
| `target_entity_id` | BIGINT FK | References `graph_entities`. |
| `relation_type` | VARCHAR(80) NOT NULL | Normalized relation category. |
| `relation_label` | TEXT NOT NULL | Bounded label for UI/debug. |
| `confidence` | NUMERIC(5,4) NOT NULL | Extractor confidence. |
| `source_document_chunk_id` | BIGINT FK | Source chunk backing the relation. |
| `evidence_text_hash` | CHAR(64) NULL | Hash of internal evidence text; do not store evidence text. |
| `metadata_json` | JSONB NOT NULL DEFAULT '{}' | Safe extractor metadata and provenance refs. |
| `created_at` | TIMESTAMPTZ NOT NULL | Creation time. |

### `graph_entity_mentions`

| Column | Draft type | Notes |
|---|---|---|
| `graph_entity_mention_id` | BIGSERIAL PK | Mention row ID. |
| `graph_entity_id` | BIGINT FK | Resolved entity. |
| `document_chunk_id` | BIGINT FK | Chunk containing the mention. |
| `document_version_id` | BIGINT FK | Version for stale graph handling. |
| `mention_text_hash` | CHAR(64) NOT NULL | Hash only; do not store mention text. |
| `mention_offset_start` | INTEGER NULL | Optional offset if safe and stable. |
| `mention_offset_end` | INTEGER NULL | Optional offset if safe and stable. |
| `confidence` | NUMERIC(5,4) NOT NULL | Mention extraction confidence. |
| `metadata_json` | JSONB NOT NULL DEFAULT '{}' | Normalization and extractor metadata. |

### `graph_index_runs`

| Column | Draft type | Notes |
|---|---|---|
| `graph_index_run_id` | BIGSERIAL PK | Graph indexing run ID. |
| `document_version_id` | BIGINT FK | Indexed document version. |
| `status` | VARCHAR(30) NOT NULL | queued, running, succeeded, failed, skipped. |
| `extractor_type` | VARCHAR(80) NOT NULL | rule_based, deterministic_fake, llm_optional, etc. |
| `extractor_version` | VARCHAR(80) NOT NULL | Version string or hash. |
| `entity_count` | INTEGER NOT NULL DEFAULT 0 | Created/updated entities. |
| `relation_count` | INTEGER NOT NULL DEFAULT 0 | Created/updated relations. |
| `error_code` | VARCHAR(100) NULL | Safe code only. |
| `started_at` | TIMESTAMPTZ NULL | Start time. |
| `finished_at` | TIMESTAMPTZ NULL | Terminal time. |

### `graph_retrieval_paths`

| Column | Draft type | Notes |
|---|---|---|
| `graph_retrieval_path_id` | BIGSERIAL PK | Stored graph path summary. |
| `retrieval_run_id` | BIGINT FK | Related `retrieval_runs`. |
| `path_json` | JSONB NOT NULL | Safe entity/relation IDs, labels, relation types, hop count. No raw text. |
| `score_breakdown_json` | JSONB NOT NULL | Path score, entity score, relation score, vector support score. |
| `source_chunk_ids_json` | JSONB NOT NULL | Chunk IDs backing nodes/edges/path. |
| `created_at` | TIMESTAMPTZ NOT NULL | Creation time. |

## Index Candidates

- `graph_entities(entity_type, canonical_name)` for lookup.
- GIN on `graph_entities.aliases_json` if alias search remains JSONB-backed.
- `graph_relations(source_entity_id, relation_type)` for traversal.
- `graph_relations(target_entity_id, relation_type)` for reverse traversal.
- `graph_entity_mentions(document_chunk_id)` for chunk-to-entity lookup.
- `graph_entity_mentions(document_version_id)` for version invalidation.
- `graph_index_runs(document_version_id, status)` for worker state.
- `graph_retrieval_paths(retrieval_run_id)` for debug/citation lookup.

## Migration Plan

PR-46 should create migration files after review of this draft. Migration order:

1. Add graph entity and index run tables.
2. Add relation and mention tables with FK constraints.
3. Add retrieval path table after retrieval contract is fixed.
4. Add indexes after representative query plans are known.
5. Backfill only via explicit graph index jobs, not migration-time extraction.

## PR-45 Constraint

No migration is created in PR-45. This file is a schema draft and migration plan only.
