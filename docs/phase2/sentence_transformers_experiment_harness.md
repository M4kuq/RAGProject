# PR-33 SentenceTransformers Experiment Harness

PR-33 adds a local opt-in experiment harness for comparing embedding and reranker
models against existing Phase2 evaluation datasets. It is an experiment/reporting
tool only; it does not switch production embedding or reranker settings.

## Scope

Implemented:

- experiment manifest schema (`phase2.experiment.v1`)
- model registry for public SentenceTransformers embedding and reranker candidates
- model availability checks with cache/download policy
- dry-run and local modes
- optional seed-document indexing into experiment-specific Qdrant collections
- Strategy Evaluation Runner integration through the PR-31 real retrieval smoke path
- JSON result artifact and Markdown comparison report
- local wrapper scripts

Not implemented:

- fine-tuning or training loops
- production model cutover
- required CI heavy model downloads
- GPU-required execution
- external API-required evaluation
- Graph-RAG, OCR, AWS, or OIDC

## Manifest

The example manifest is:

```text
backend/app/experiments/manifests/phase2_retrieval_models.example.json
```

It defines:

- `dataset`: existing evaluation dataset name or id
- `strategies`: `dense`, `sparse`, `hybrid`, or `agentic_router`
- `embedding_models`: public SentenceTransformers embedding candidates
- `reranker_models`: public SentenceTransformers reranker candidates
- `metrics`: existing deterministic evaluation metrics
- `required`: whether a missing model blocks the experiment
- `download_policy`: `never`, `if-cached`, or `opt-in-download`

Manifest files must not contain secrets, credentials, private model tokens, local
paths, raw prompts, raw context, or raw chunk text.

## Download And Cache Policy

Default policy is `if-cached`. The harness sets Hugging Face offline flags while
checking cached models, so missing local cache is reported as `skipped` or
`blocked` rather than silently downloading.

`opt-in-download` is allowed only when the operator explicitly passes it. Normal
CI does not use this mode and does not require SentenceTransformers models.

## Commands

Dry-run, no model download:

```powershell
.\scripts\run_retrieval_model_experiment.ps1 -Mode dry-run
```

```bash
sh scripts/run_retrieval_model_experiment.sh
```

Local opt-in with cached models:

```powershell
.\scripts\run_retrieval_model_experiment.ps1 -Mode local -DownloadPolicy if-cached
```

Local opt-in with explicit downloads allowed:

```powershell
.\scripts\run_retrieval_model_experiment.ps1 -Mode local -DownloadPolicy opt-in-download
```

The local mode uses experiment-specific Qdrant collection names derived from the
manifest/model ids, indexes deterministic seed documents for each available
model pair by default, then runs the existing Strategy Evaluation Runner through
the PR-31 retrieval smoke path.

## Artifacts

Default outputs:

```text
artifacts/experiments/retrieval_model_comparison.json
artifacts/experiments/retrieval_model_comparison.md
```

Artifacts include safe metadata only:

- model ids, provider, model type, dimensions
- availability status and reason codes
- dataset name, strategies, metric names
- aggregate metric summaries by strategy/model pair
- skipped/blocked/failure reason counts

Artifacts do not include raw prompts, full context, raw chunk text, full answer
text, PII, secrets, tokens, cookies, credentials, local cache paths, or raw
payload dumps.

## PR-34 Handoff

PR-34 can use these artifacts to choose retrieval configurations for larger
advanced-import experiments, but production model cutover remains a separate
explicit change.

