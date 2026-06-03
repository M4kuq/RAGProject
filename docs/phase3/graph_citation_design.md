# Graph Citation / Graph Path Validation Design

Graph Citation connects graph nodes, edges, and paths back to the existing citation model. PR-45 defines the design only.

## Core Principle

User-facing citations remain chunk-backed. A graph path can explain why evidence was selected, but citations must resolve to source chunks that appear in `retrieval_run_items` for the same retrieval run.

## Mapping Model

```text
graph node -> graph_entity_mentions -> document_chunk_id -> retrieval_run_items -> citation
graph edge -> source_document_chunk_id -> retrieval_run_items -> citation
graph path -> source_chunk_ids_json -> retrieval_run_items -> citations
```

## Node To Source Chunk Mapping

A graph node is citable only when it has at least one mention linked to an eligible source chunk. The UI can show a safe entity label and source count, not raw mention text.

## Edge To Source Chunk Mapping

A graph edge is citable only when it has `source_document_chunk_id` or equivalent support refs. The edge stores `evidence_text_hash`, not raw evidence text.

## Path To Citations

A graph path is citable when every critical hop has source chunk support, or when unsupported hops are clearly marked as non-grounding context and excluded from answer evidence.

Candidate path citation summary:

- path ref ID
- hop count
- entity labels
- relation labels/types
- source chunk IDs
- citation IDs
- score breakdown summary
- validation status

## Existing Citation Policy Alignment

Existing `citations` are built from selected retrieval run items. Graph Citation should not create citations directly from graph rows if the backing chunks are absent from selected retrieval run items. This preserves the current invariant:

```text
answer citation -> retrieval_run_item -> document_chunk
```

## Graph Path Validation

Validation should check:

- each node/edge ref exists
- source chunks exist and are active unless version-aware
- source chunks are included in or can be added to retrieval run items
- path hop count is within budget
- relation confidence is above threshold
- path source support is sufficient
- no path includes unsafe payload keys
- no raw evidence text is present in debug or trace payloads

## Old Version Handling

If a graph path points to old document versions:

- default retrieval should exclude it from grounding evidence
- version-aware retrieval can include it with explicit old-version flags
- admin debug can show safe stale path metadata
- viewer UI should not present stale graph paths without a citation/version indicator

## Source Locator

Graph Citation should extend source locator concepts with optional graph context:

- source chunk location remains primary
- graph entity/edge/path refs can be secondary context
- OCR/image region locators join later through source locator extension

## Evidence Pack Mapping

Evidence Pack should carry graph refs as safe metadata:

- `graph_path_id`
- `graph_entity_ids`
- `graph_relation_ids`
- source chunk refs
- evidence hash
- compression method

It should not store raw graph evidence text.

## UI Policy

Viewer UI may show:

- citation source label
- page/section/region when available
- safe graph relation/path labels
- confidence or validation warning

Admin Debug may show more detail, still bounded:

- path refs
- node/edge IDs
- scores
- drop reasons
- validation status
- stale/version flags

Neither UI should show raw graph evidence text, full context, raw chunk text, PII, credential values, or secret values.
