# Qwen3.5 9B local RAG accuracy workflow

This workflow is local/manual only. It does not change the default retrieval profile,
delete an existing Qdrant collection, reset a database, or download a reranker during
normal CI.

## Fixed generation conditions

- answer generation: `qwen/qwen3.5-9b`
- Agentic LLM planner: `qwen/qwen3.5-9b`
- auxiliary judge: `qwen/qwen3.5-9b`
- generation temperature: `0.0`
- public result: manually calibrated Grounded Answer Pass Rate

The same 9B model acting as generator and auxiliary judge is not treated as an
independent evaluation. Human calibration remains the source of the publishable
result.

## Prepare the tuning dataset

From `backend`:

```powershell
uv run python -m app.scripts.export_local_accuracy_dev
```

Validate and import `artifacts/evaluation/local_accuracy_dev_v1.json` using:

- `POST /api/v1/evaluations/datasets/validate`
- `POST /api/v1/evaluations/datasets/import`
- `POST /api/v1/evaluations/datasets/{evaluation_dataset_id}/corpus/prepare`

Wait until the corpus readiness endpoint reports `ready=true`. The fixture has 40
cases with these enforced balances:

- answerable / unanswerable: 24 / 16
- single-hop / multi-hop: 20 / 20
- Japanese / English: 20 / 20
- prompt injection: 8

Its fact IDs, questions, and source keys use the `local_dev_*` namespace and are
separate from Gold v2.

## Run the bounded experiment

Load these LM Studio models before a local run:

- `qwen/qwen3.5-9b`
- `text-embedding-nomic-embed-text-v1.5`
- `text-embedding-qwen3-embedding-4b`

Then run:

```powershell
.\scripts\run_retrieval_model_experiment.ps1 `
  -Manifest app\experiments\manifests\local_rag_accuracy_v2.example.json `
  -Mode local `
  -DownloadPolicy opt-in-download `
  -SkipSeedIndexing
```

`opt-in-download` applies to `BAAI/bge-reranker-v2-m3`. The experiment:

1. probes each LM Studio embedding with a fixed harmless string;
2. builds at most 15 one-coordinate-at-a-time candidates;
3. indexes only the isolated dataset documents into a collection derived from
   `corpus fingerprint + resolved embedding model + dimension`;
4. screens all candidates retrieval-only;
5. selects three finalists by Recall@K, MRR, then no-context rate;
6. runs only the finalists end-to-end with Qwen3.5 9B;
7. records profile and repeat numbers, resolved models, tokens, latency, and safe
   failure codes.

## Holdout and promotion

Freeze the winning dev configuration before running unchanged
`gold_answer_quality_v2`. Run baseline and candidate three times with cache disabled.
Use a three-run majority vote per case.

Review all cases from repeat 1. For repeats 2 and 3, review changed results, hard-gate
failures, low-confidence judgments, judge/human disagreements, and the deterministic
15% case-hash sample.

Add `local_accuracy_v1` to UI/config examples only when all gates pass:

- calibrated mean improvement is at least 6 percentage points;
- unanswerable accuracy and prompt-injection resistance do not regress;
- citation correctness and answer completeness do not regress;
- pipeline failures are zero;
- candidate p95 latency is at most twice the baseline.

If a gate fails, keep the current default and report the observed delta and failure
reason codes. Compare runs with `strict=true`; incompatible dataset, corpus, case set,
scope, backend, or generation model returns HTTP 409.

## Security follow-up

After local accuracy validation, perform the repository threat-model phase for
retrieved-chunk prompt injection, corpus poisoning, tool authorization, Agentic budget
exhaustion, cloud escalation cost attacks, rate limits, daily budgets, and the
external-LLM data boundary. Accuracy promotion and security promotion are separate
gates.
