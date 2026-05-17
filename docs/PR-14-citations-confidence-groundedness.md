# PR-14 Citations / Confidence / Groundedness

## Scope

- `/api/v1/rag/ask` builds citations only from selected `retrieval_run_items`.
- Citation markers use local ids in `[1]` format.
- `citation_build_failed` is returned for zero markers or markers that do not map to selected context.
- `/api/v1/rag/search` does not create citations or confidence scores.
- Confidence and groundedness are deterministic heuristics; no external model or API key is required in CI.

## Persistence

- `citations.document_chunk_id` points back through `(retrieval_run_id, document_chunk_id)` to `retrieval_run_items`.
- `document_version_id` is not stored on citations.
- `old_version_flag` is derived at response time through `document_chunk_id -> document_versions`.
- `retrieval_runs.answer_confidence`, `groundedness_score`, and `confidence_label` are stored only for successful `/rag/ask` runs.
- Confidence is a generation-time snapshot. Replay returns the stored confidence, while `old_version_flag` reflects the current source state for display.

## Safety

- Raw prompt and full context are not returned.
- Raw chunk text is not stored in `citations`; only a bounded snippet is saved.
- Fake generation emits deterministic markers for tests.
