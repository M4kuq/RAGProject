# Graph Debug UI Design

PR-45 defines the Graph Debug UI contract. It does not change frontend code.

## Placement

Graph debug should extend the existing admin Retrieval Debug page rather than create a viewer-facing route. It remains admin-only.

Candidate panels:

- Graph Strategy Summary
- Entity Lookup Summary
- Relation Traversal Summary
- Graph Path Summary
- Graph + Vector Merge Summary
- Graph Citation Validation Summary
- Graph Evaluation Snapshot

## Viewer/Admin Boundary

| User | Allowed graph information |
|---|---|
| Viewer | answer, citations, confidence, safe strategy label, optional safe graph path label |
| Admin | safe graph counts, IDs, scores, path refs, validation status, fallback reasons |

No user sees raw document text, raw chunk text, full context, prompt material, PII, credential values, or secret values.

## Safe Fields

Graph Debug may show:

- `strategy_type`
- `selected_strategy`
- `execution_strategy`
- `graph_enabled`
- `entity_candidate_count`
- `relation_candidate_count`
- `path_candidate_count`
- `selected_path_count`
- `dropped_path_count`
- `max_hops`
- `budget_exhausted`
- `source_chunk_support_count`
- `citation_validation_status`
- `fallback_reason`
- safe reason codes
- score breakdown summaries

## Unsafe Fields

Graph Debug must not show:

- raw document body
- raw chunk body
- raw OCR output
- prompt material
- full context
- raw graph payload dumps
- unredacted URLs with sensitive query values
- credential values
- session values
- local filesystem paths

## Panel Behavior

- Empty graph state shows an empty-state summary and fallback strategy.
- Missing graph index shows `graph_index_unavailable` reason code.
- Citation validation failure shows graph path excluded from grounding evidence.
- Stale graph path shows old-version warning for admins.
- Budget exhaustion shows selected/dropped counts, not raw payloads.

## Implementation Sequence

Graph Debug UI should wait until backend safe trace fields are implemented and tested. Frontend types should be added after backend schema settles.
