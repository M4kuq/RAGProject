# Evaluation and Observability Strategy

## Direction

Phase2 treats evaluation and observability as central architecture, not support tooling. Retrieval strategies must be observable and comparable before new retrieval algorithms are added.

## Trace Foundation

PR-21 stores safe retrieval trace for the existing dense flow:

- query plan hash and counts
- strategy decision metadata
- retrieval settings snapshot
- latency breakdown
- item source and score breakdown

Trace payloads never store raw query, raw prompt, full context, raw chunk text, PII, secrets, credentials, tokens, or external raw request/response bodies.

## Dataset Foundation

PR-22 stores evaluation datasets and cases in DB so the same dataset can be reused across strategies:

- manual datasets
- imported fixture datasets
- future feedback-promoted datasets
- archived datasets retained for auditability

Dataset/case APIs are admin-only. Writes require CSRF. Delete is intentionally not exposed; archive is used instead.

## Strategy Metrics

The Phase2 strategy metric schema includes:

- `recall_at_k`
- `mrr`
- `citation_coverage`
- `groundedness`
- `faithfulness`
- `no_context_rate`
- `p95_latency`
- `strategy_selection_accuracy`

`evaluation_results.metric_value` stores the canonical metric value. `metric_score` remains for Phase1 ratio metrics. `metric_detail_json` stores safe details such as counts, units, labels, case key, and reason codes.

## Comparison Model

The comparison key is:

```text
evaluation_dataset_id + strategy_type + metric_name
```

PR-22 stores this shape but does not run sparse, hybrid, or agentic_router comparisons. PR-25 will add Strategy Evaluation Runner on top of this schema.

## CI Direction

CI should use deterministic fixtures and fake adapters. Heavy model downloads, external API keys, LangSmith credentials, and external trace export must not be required for PR-22 validation.

## Redaction Rules

Do not store or display:

- raw prompt
- full context
- raw chunk text
- PII
- secret, token, credential, API key, password
- raw external API request/response
- full trace JSON dumps in logs

Evaluation case `question` is saved only after validation rejects PII-like and secret-like values.
