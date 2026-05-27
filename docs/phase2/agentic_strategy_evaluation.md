# Agentic Strategy Evaluation / Failure Dataset Promotion

## Purpose

PR-30 extends the Phase2 evaluation loop so one dataset can compare `dense`, `sparse`, `hybrid`, and `agentic_router`. The runner keeps the PR-25 deterministic metrics and adds bounded agentic metrics from PR-29 trace data.

## Agentic metrics

- `strategy_selection_accuracy`: calculated only when an evaluation case has `metadata_json.expected_strategy` or `metadata_json.acceptable_strategies`.
- `fallback_rate`: fraction of `agentic_router` cases with `strategy_decision_json.fallback_used=true`.
- `budget_exhausted_rate`: fraction of `agentic_router` cases with `budget_exhausted=true`.
- `sufficiency_score_avg`: average safe sufficiency score stored by the agentic retrieval loop.
- `retrieval_call_count_avg`: average bounded retrieval calls used by the loop.

All metric details store only strategy names, counts, booleans, reason codes, hashes, and scores. Raw prompts, full context, raw chunk text, PII, and secrets are not stored.

## Failure candidates

The evaluation service extracts safe failure candidates from run items and metric rows:

- `no_context`
- `low_recall`
- `low_mrr`
- `low_citation_coverage`
- `low_groundedness`
- `low_faithfulness`
- `strategy_selection_incorrect`
- `budget_exhausted`
- `fallback_failed`
- `high_latency`
- retrieval, generation, and citation build exceptions

Failure candidates include `question_hash`, `failure_type`, `strategy_type`, reason codes, and a numeric metric snapshot. They do not include raw context, raw chunks, prompts, or full answer text.

## Promotion

Admins can promote failure candidates back into an active evaluation dataset with:

- `GET /api/v1/evaluations/runs/{evaluation_run_id}/failure-candidates`
- `POST /api/v1/evaluations/runs/{evaluation_run_id}/promote-failures`

Promotion is idempotent. The case key is derived from a deterministic promotion key based on the source dataset/case, strategy, failure type, and question hash. Repeating the same promotion returns an `already_exists` result instead of creating duplicates.

Promoted cases copy the original evaluation question and expected signals because those are the dataset inputs. Promotion metadata records the source run item, strategy, failure type, reason codes, metric snapshot, promotion key, and question hash.

## UI

The admin evaluation detail page shows:

- an `agentic_router` row in strategy comparison,
- agentic summary metrics,
- failure candidates,
- a minimal promote action with duplicate promotion treated as a safe no-op.

## Handoff

PR-31 can use the same dataset, agentic metrics, and promotion metadata for CI retrieval evaluation and scheduled smoke runs. LangSmith, online evaluation, external judges, Graph-RAG, OCR, and external operation agents remain out of scope.
