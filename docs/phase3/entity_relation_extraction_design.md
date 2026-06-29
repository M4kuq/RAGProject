# Entity / Relation Extraction Design

C2b makes LLM entity/relation extraction the default graph extraction mode. It
uses the existing generation provider abstraction for chunk-scoped structured
extraction and falls back gracefully to the deterministic `rule_based`
extractor when no usable provider is configured or the provider fails. Both
modes index ready `document_versions` without persisting raw evidence text.

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
| llm | Default higher-recall extractor using the configured generation provider. It grounds returned mentions/evidence back to chunk spans before persistence. | Default in C2b. |
| rule_based | Deterministic baseline for technical labels and explicit relation keywords. | Graceful fallback when LLM provider is unavailable, fails, times out, returns invalid JSON, or returns an empty response. |

## Raw Text Handling

Extraction internally reads chunk text and the LLM prompt contains the current
chunk in memory only. It must not persist or log raw document text, raw chunk
text, raw LLM response, full context, prompt material, PII details, credential
values, or secret values. Use:

- `document_chunk_id`
- `document_version_id`
- `mention_text_hash`
- `evidence_text_hash`
- bounded safe labels
- confidence and reason codes
- actual `extractor_type` / `extractor_version`
- provider/model labels, latency, cost estimate, and token counts only as safe aggregate metadata

## PII Redaction

The extraction layer must run the same redaction policy as retrieval traces.
Person-like or organization-like labels are allowed only as grounded,
normalized entity labels when needed for retrieval. Raw private details, copied
source passages, prompt text, and raw LLM responses must not be logged,
persisted, projected to Neo4j, or displayed in debug output.

## Entity Normalization

Normalization remains deterministic after LLM extraction:

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

LLM extractor failures do not fail the graph job by themselves. The worker falls
back to `rule_based`, persists the actual extractor used, and records safe reason
codes such as:

- `graph_extraction_llm_unavailable`
- `graph_extraction_llm_failed`
- `graph_extraction_llm_invalid_response`
- `graph_extraction_llm_empty_response`
- `graph_extraction_llm_fallback`

Hard graph failures must still use safe error codes such as:

- `graph_extraction_failed`
- `graph_normalization_failed`
- `graph_relation_validation_failed`
- `graph_index_write_failed`

Failure logs should include counts, IDs, extractor type/version, and error code only.
