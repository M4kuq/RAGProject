# CI Retrieval Evaluation / Scheduled Smoke

## Purpose

PR-31 adds a lightweight retrieval evaluation smoke workflow for GitHub Actions. It runs the existing Phase2 evaluation runner against a small deterministic dataset so regressions in strategy evaluation can be caught without external LLM APIs, GPU, LangSmith, or heavyweight BAAI model downloads.

The default workflow path uses:

- `phase2_strategy_smoke`
- real local retrieval with PostgreSQL, Qdrant, and indexed demo documents
- cached `sentence-transformers/all-MiniLM-L6-v2` local embeddings
- `dense,hybrid,agentic_router`
- warn-mode thresholds
- JSON and Markdown artifacts

The workflow does not use fake embedding, fake reranker, or a fake evaluator for
the smoke itself. The smoke is retrieval-only and does not exercise answer
generation. If the configured local embedding backend, model cache, or Qdrant
endpoint is unavailable, the workflow writes a blocked artifact and summary
instead of silently falling back to fake behavior.

## GitHub Actions

Workflow file:

```text
.github/workflows/retrieval-eval-smoke.yml
```

Triggers:

- `workflow_dispatch` for manual smoke runs.
- Weekly `schedule` at a low frequency.

The workflow is not mandatory on every pull request by default. Normal PR checks remain Backend CI, Frontend CI, Docker CI, and Compose Smoke. Retrieval evaluation can be run manually when a PR touches evaluation, retrieval, routing, agentic behavior, or failure-promotion logic.

Manual inputs:

- `dataset`: evaluation fixture name or persistent dataset id.
- `strategies`: comma-separated list from `dense`, `sparse`, `hybrid`, `agentic_router`.
- `mode`: `local`.
- `threshold_mode`: `warn` or `fail`.
- `case_limit`.
- selected threshold overrides such as `recall_at_k_min` and `no_context_rate_max`.

The workflow is intentionally not a required pull-request gate. It is available
through manual dispatch and a low-frequency optional schedule so real local
retrieval prerequisites can be handled explicitly.

## Local Command

From the repository root:

```powershell
scripts/run_retrieval_eval_smoke.ps1 -Dataset phase2_strategy_smoke -Strategies dense,hybrid,agentic_router -ThresholdMode warn
```

On Unix-like shells:

```sh
DATASET=phase2_strategy_smoke STRATEGIES=dense,hybrid,agentic_router scripts/run_retrieval_eval_smoke.sh
```

The local command expects the backend environment to be initialized with migrated database tables, seeded and indexed demo data, reachable Qdrant, and non-fake local retrieval dependencies. For GitHub Actions, the workflow installs and caches the small local embedding prerequisite before preflight; if model/cache prerequisites are still unavailable, it reports `blocked` and uploads the safe artifact.

Direct backend command:

```sh
cd backend
uv run --with "sentence-transformers>=2.7.0,<4" python -m app.scripts.retrieval_eval_smoke \
  --dataset phase2_strategy_smoke \
  --strategies dense,hybrid,agentic_router \
  --mode local \
  --threshold-mode warn \
  --output-json ../artifacts/retrieval_eval_smoke.json \
  --output-md ../artifacts/retrieval_eval_smoke.md
```

## Thresholds

Default thresholds are intentionally lenient so the smoke catches broken runs, unsafe configuration drift, and severe metric regressions without creating noisy scheduled failures.

Supported threshold fields include:

- `recall_at_k_min`
- `mrr_min`
- `citation_coverage_min`
- `groundedness_min`
- `faithfulness_min`
- `no_context_rate_max`
- `p95_latency_ms_max`
- `strategy_selection_accuracy_min`
- `fallback_rate_max`
- `budget_exhausted_rate_max`
- `sufficiency_score_avg_min`
- `retrieval_call_count_avg_max`

In `warn` mode, threshold violations and failed evaluation items are written to the JSON artifact and Markdown summary, but the workflow exits successfully. In `fail` mode, threshold violations or any failed evaluation item make the script exit non-zero after artifacts and summary are written. The `p95_latency_ms_max` threshold is compared against each strategy's p95 value, not its mean latency.

## Artifacts

The workflow uploads:

```text
artifacts/retrieval_eval_smoke.json
artifacts/retrieval_eval_smoke.md
artifacts/retrieval_eval_smoke.exitcode
```

The JSON artifact includes safe run metadata, strategy-level metrics, aggregate failure counts, thresholds, warnings, and known limitations. It intentionally excludes case prompts, full context, raw chunk text, full answers, PII, tokens, credentials, API keys, and secrets.

The Markdown artifact is also appended to the GitHub step summary. If preflight
is blocked, the artifact includes only safe reason codes and check names; it
does not include local filesystem paths, model payloads, raw prompts, or raw
retrieval content.

## Privacy And Safety

The smoke script redacts forbidden keys and secret-like string values before writing artifacts. It does not dump evaluation cases, retrieval payloads, prompt text, context items, or retrieval run item payloads.

CI default mode does not use fake retrieval adapters and does not require:

- GitHub secrets
- external LLM or judge API keys
- BAAI/heavyweight model downloads
- GPU
- LangSmith credentials

The workflow caches the small local embedding model first, then sets Hugging
Face/Transformers offline flags for the smoke. A missing local model/cache is
reported as `blocked` rather than replaced with fake behavior.

## Handoff

PR-32 can add optional LangSmith trace export on top of these safe evaluation summaries. Production online evaluation, alerting, and trace sampling remain separate later work.
