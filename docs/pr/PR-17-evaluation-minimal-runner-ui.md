# PR-17 Evaluation Minimal Runner / API / Admin UI

## Scope

PR-17 adds a Phase1 manual evaluation path for admins:

- `POST /api/v1/evaluations/runs`
- `GET /api/v1/evaluations/runs`
- `GET /api/v1/evaluations/runs/{evaluation_run_id}`
- `evaluation_run` worker handling
- fixture-backed deterministic metrics
- admin evaluation list/detail pages

The Phase1 dataset source is a small fixture at
`backend/app/evaluation/fixtures/phase1_smoke.json`. Dataset CRUD, scheduled
evaluation, online evaluation, LLM-as-a-judge, LangSmith integration, GraphRAG,
Agentic RAG evaluation, and CI gating are intentionally out of scope.

## Metrics

The runner stores one `evaluation_run_item` per fixture case and metric rows in
`evaluation_results`. The required deterministic heuristic metrics are:

- `faithfulness`
- `groundedness`
- `citation_coverage`
- `context_precision`

Scores are clamped to `0.0..1.0`. Metric details store only safe counts,
labels, and case identifiers. Raw prompts, full context, raw chunk text, tokens,
secrets, and PII are not stored or returned.

## Execution

Admin API creation is asynchronous:

1. Create an `evaluation_runs` row with `queued`.
2. Create an `evaluation_run` job.
3. Worker loads fixture cases.
4. Each case runs the existing RAG rerank/generation/citation/confidence path
   through the evaluation entrypoint. Phase1 evaluation uses a deterministic
   DB-backed vector candidate source for ready/active chunks so CI does not
   depend on external LLM APIs or a pre-populated Qdrant collection.
5. Metrics and safe details are persisted.
6. Case failures are stored per item; the run succeeds when processing completes.

CI and local tests use fake/deterministic adapters and do not require external
LLM judge APIs.
