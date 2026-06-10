# Entity / Relation Extraction Design

PR-47 implements the first safe entity/relation extraction pipeline. It uses a
deterministic rule-based extractor and a `graph_index_build` worker handler to
index ready `document_versions` without persisting raw evidence text.

## Extraction Target

The extractor consumes approved `document_versions` and their `document_chunks` after ingest is ready. Candidate outputs are:

- entity candidates with canonical name, type, aliases, confidence, and source mention refs
- relation candidates with source entity, target entity, relation type, confidence, and source chunk refs
- safe extraction metadata such as extractor type/version, counts, status, and error code

## Timing

Graph indexing should run after a document version reaches ready state and before, or shortly after, admin approval depending on the PR-46/PR-47 final policy. The recommended default is:

```text
document_version ready -> graph_index job queued -> entity/relation extraction -> graph_index_run terminal state
```

If approval changes the active version, graph rows from old versions remain traceable but should not be selected for active retrieval unless explicitly version-aware.

## Worker Job

Implemented job type:

```text
graph_index_build
```

Payload shape includes IDs and options only:

- `job_type`
- `document_version_id`
- `graph_index_run_id`
- `extractor_type`
- `extractor_version`
- `reindex_policy`

The payload must not include raw document text or raw chunk text.

## Extractor Interface

The implementation snapshots chunk refs, runs extraction outside a long DB
transaction, and persists the safe result:

```text
extract(chunk_refs) -> GraphExtractionResult
```

`chunk_refs` include chunk text in memory for extraction. Extractor results,
logs, job results, and graph rows contain only IDs, hashes, labels, offsets,
counts, confidence, and safe metadata.

## Extractor Modes

| Mode | Purpose | Phase3 use |
|---|---|---|
| rule_based | Safe baseline for technical labels and explicit relation keywords | Implemented in PR-47. |
| llm_optional | Higher recall extraction | Future, optional, and gated by export policy. |

## Raw Text Handling

Extraction internally reads chunk text. It must not persist or log raw document text, raw chunk text, full context, prompt material, PII, credential values, or secret values. Use:

- `document_chunk_id`
- `document_version_id`
- `mention_text_hash`
- `evidence_text_hash`
- bounded safe labels
- confidence and reason codes

## PII Redaction

The extraction layer must run the same redaction policy as retrieval traces. Person-like or organization-like labels are allowed only as normalized entity labels when they are needed for retrieval, but raw private details must not be logged or displayed in debug output.

## Entity Normalization

Normalization should be deterministic before any optional LLM merge:

- Unicode normalization.
- trim/case folding where appropriate.
- alias canonicalization.
- type-specific validators.
- source-count based confidence.
- conflict recording through safe reason codes.

## Alias Merging

Alias merge should be conservative:

- exact normalized match merges automatically.
- high-confidence acronym/expanded-form pairs can merge with reason code.
- ambiguous aliases remain separate until review or higher-confidence evidence.
- merge metadata stores refs and hashes, not raw text.

## Confidence

Entity and relation confidence should be separate:

- mention confidence: entity span/label quality
- entity confidence: canonicalization and support count
- relation confidence: relation evidence quality
- path confidence: retrieval-time score composition

## Relation Evidence Mapping

Each relation must map to at least one source chunk through `source_document_chunk_id` or equivalent support refs. The relation stores `evidence_text_hash`, not evidence text.

## Reindex / Version Update Behavior

- New document version gets a new graph index run.
- PR-47 rebuilds a document version by replacing its existing mentions and relations.
- Entities are merged by normalized canonical name and entity type.
- Active retrieval filters should prefer active document versions.
- Reindex failure should not corrupt existing ready graph rows.
- Reindex retry creates a new run when the payload references a failed run.

## Failure Handling

Failures must use safe error codes such as:

- `graph_extraction_failed`
- `graph_normalization_failed`
- `graph_relation_validation_failed`
- `graph_index_write_failed`

Failure logs should include counts, IDs, extractor type/version, and error code only.
