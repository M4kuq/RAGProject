# LangSmith Optional Adapter / Trace Export

## Purpose

PR-32 adds a provider-neutral trace export layer for Phase2 retrieval,
generation, citation, evaluation, and CI retrieval smoke observability. The
default behavior is no external export. LangSmith is optional and is used only
when explicitly enabled and configured.

## Default No-Op Mode

The default settings are:

```text
TRACE_EXPORT_ENABLED=false
TRACE_EXPORT_PROVIDER=none
LANGSMITH_TRACING_ENABLED=false
LANGSMITH_API_KEY=
```

With these defaults, RAG search, RAG ask, strategy evaluation, and CI retrieval
smoke continue normally. Export requests return a skipped result and do not
write payloads to logs.

## LangSmith Optional Mode

LangSmith export is enabled only when all of the following are true:

- `TRACE_EXPORT_ENABLED=true`
- `TRACE_EXPORT_PROVIDER=langsmith`
- `LANGSMITH_TRACING_ENABLED=true`
- `LANGSMITH_API_KEY` is set outside version control

The adapter lazy-imports the LangSmith SDK. If the SDK is unavailable or the
secret is missing, export is skipped or failed as safe adapter status without
failing the originating RAG or evaluation flow.

The dependency is declared as an optional backend extra:

```sh
uv run --extra observability python -m app.scripts.retrieval_eval_smoke --preflight-only
```

Normal CI uses the existing dev/runtime dependency set and does not install this
extra.

## Exported Retrieval Fields

Retrieval exports include minimized safe metadata:

- `retrieval_run_id`
- `request_id`
- `strategy_type`
- `selected_strategy`
- `execution_strategy`
- `fallback_used`
- `retrieval_call_count`
- `query_hash`
- `intent`
- safe reason codes
- retrieval score summary
- latency breakdown
- retrieval settings snapshot
- selected/excluded counts
- confidence label and numeric confidence scores
- status and safe error code

## Exported Evaluation Fields

Evaluation exports include:

- `evaluation_run_id`
- `evaluation_dataset_id`
- `dataset_name`
- strategy list
- metric names and aggregate metric summary
- strategy comparison rows
- strategy metrics summary
- status and safe error code

PR-31 CI retrieval smoke exports can reuse the same redaction layer for safe
aggregate artifacts: dataset name/id, strategy list, aggregate metrics,
threshold status, failure counts, and known limitations.

## Never Exported

The export minimization layer removes forbidden keys and secret-like values.
The following must not be exported:

- raw user query
- raw prompt
- full context
- raw chunk text
- full answer text
- raw Qdrant payloads
- job payloads
- local file paths
- email addresses or PII-like values
- API keys, tokens, passwords, cookies, CSRF/session IDs, credentials, secrets

Query previews and rewritten-query previews are not exported by default.
`TRACE_EXPORT_INCLUDE_PREVIEWS=true` and a bounded
`TRACE_EXPORT_PREVIEW_MAX_CHARS` are required before preview-like fields can be
retained.

## Failure Semantics

Exporter failures are non-fatal. The originating search, ask, evaluation run,
or CI smoke artifact remains governed by its own result. Exporter exceptions are
collapsed into safe result codes such as `export_failed`; raw exception messages
and external API responses are not logged or returned.

## Settings

```text
TRACE_EXPORT_ENABLED=false
TRACE_EXPORT_PROVIDER=none
TRACE_EXPORT_TIMEOUT_SECONDS=3
TRACE_EXPORT_INCLUDE_RETRIEVAL=true
TRACE_EXPORT_INCLUDE_EVALUATION=true
TRACE_EXPORT_INCLUDE_CI_SUMMARY=true
TRACE_EXPORT_INCLUDE_PREVIEWS=false
TRACE_EXPORT_PREVIEW_MAX_CHARS=0
LANGSMITH_TRACING_ENABLED=false
LANGSMITH_PROJECT=ragproject-phase2
LANGSMITH_ENDPOINT=
LANGSMITH_API_KEY=
```

Normal CI does not require LangSmith secrets and does not send external traces.

## Handoff

PR-33 can use this safe export foundation while adding optional local
SentenceTransformers experiment artifacts. Production trace sampling, online
evaluation, alerting, and external dashboard UI remain later work.
