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

## Latest measured dev result

The first live dev measurement was completed on 2026-07-28 with LM Studio
`qwen/qwen3.5-9b`, `temperature=0.0`, cache disabled, real Qdrant, and isolated
collections. This is an auxiliary-judge result from one repeat, not a manually
calibrated or publishable accuracy claim.

| Metric | B1 Nomic (run 57) | E1 Qwen3 Embedding 4B (run 56) | Delta |
|---|---:|---:|---:|
| Auxiliary Grounded Answer Pass Rate | 75.000% (30/40) | 79.487% (31/39 judged) | +4.487 pp raw |
| Citation correctness | 84.211% | 92.500% | +8.289 pp |
| Answer completeness | 31.579% | 35.000% | +3.421 pp |
| Recall@K | 64.583% | 60.417% | -4.167 pp |
| MRR | 68.419% | 71.250% | +2.831 pp |
| p95 latency | 92.790 s | 94.280 s | +1.606% |
| Pipeline failures | 0/40 | 0/40 | unchanged |

Strict same-case comparison used 39 pairs because one candidate auxiliary judgment
was unavailable. The paired delta was `+2.564 pp`, the 10,000-sample paired bootstrap
95% confidence interval was `[-12.821, +17.949] pp`, and exact McNemar `p=1.0`.

The candidate improved the auxiliary prompt-injection result from `4/8` to `6/8`,
but unanswerable cases regressed from `12/16` to `11/16`. It therefore fails both the
`+6 pp` evidence requirement and the no-regression hard gate. The default profile is
unchanged, and Gold v2 remains unopened until a dev candidate passes all gates.

### E2/E3 bounded development ablation

Use the focused manifest below before running the remaining Agentic and Graph
profiles:

```powershell
.\scripts\run_retrieval_model_experiment.ps1 `
  -Manifest app\experiments\manifests\local_rag_accuracy_e2_e3_dev_v2.example.json `
  -Mode local `
  -DownloadPolicy opt-in-download `
  -SkipSeedIndexing
```

This manifest keeps Qwen3 Embedding 4B and BGE reranking fixed, screens six E2/E3
candidates, and runs only the top three candidates end-to-end once. If no candidate
passes the dev gates against B1, stop without changing the default profile. If a
candidate is promising, freeze that exact retrieval configuration and run the
three-repeat confirmation separately.

## Latest E2/E3 dev measurement

The bounded E2/E3 measurement completed on 2026-07-29. It used the 40-case
`local_accuracy_dev_v1` dataset, real Qdrant, Qwen3 Embedding 4B,
`BAAI/bge-reranker-v2-m3`, and LM Studio `qwen/qwen3.5-9b`. Cache was disabled and
generation temperature was `0.0`. All nine runs succeeded with zero pipeline
failures. Total experiment elapsed time was `8,420,714 ms`.

The implementation audit found that hybrid retrieval previously stopped after
dense/sparse fusion and did not call the configured BGE reranker. The E3 measurement
below was run only after adding fusion-then-rerank for explicit external rerank
providers (`local` and `bedrock`) and confirming non-null rerank scores and
`rerank_ms` in the live trace.

### Retrieval screening

| Candidate | Run | Recall@K | MRR | p95 latency |
|---|---:|---:|---:|---:|
| E2 base, top-k 10, rerank 3 | 76 | 60.417% | 71.250% | 0.761 s |
| E3 base, hybrid 0.5/0.5, RRF 60 | 77 | 54.167% | 68.651% | 1.338 s |
| E2 top-k 20 | 78 | **87.500%** | **72.473%** | 1.747 s |
| E2 rerank 5 | 79 | 60.417% | 71.250% | 1.180 s |
| E3 hybrid 0.4/0.6 | 80 | 54.167% | 68.651% | 0.889 s |
| E3 RRF 30 | 81 | 54.167% | 68.651% | 0.964 s |

The selected end-to-end finalists were `E2__top20`, `E2__base`, and
`E2__rerank5`. E3 did not pass the retrieval screening gate.

### End-to-end auxiliary result

| Candidate | Run | Auxiliary pass | Paired delta vs B1 | Citation correctness | Completeness | p95 latency | Unanswerable | Prompt injection | Judge failures |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 Nomic | 57 | 75.000% (30/40) | baseline | 84.211% | 31.579% | 92.790 s | 12/16 | 4/8 | 0 |
| E2 top-k 20 | 82 | **82.500% (33/40)** | **+7.500 pp** | **100.000%** | 30.435% | 101.841 s | 11/16 | **7/8** | 0 |
| E2 base | 83 | 80.556% (29/36 judged) | +2.778 pp on 36 pairs | 100.000% | 26.316% | 111.156 s | 12/14 judged | 7/8 | 4 |
| E2 rerank 5 | 84 | 78.378% (29/37 judged) | +0.000 pp on 37 pairs | 100.000% | 26.316% | 99.486 s | 12/15 judged | 7/8 | 3 |

For the only complete 40-pair candidate, E2 top-k 20, the paired bootstrap 95%
confidence interval was `[-10.000, +27.500] pp`, exact McNemar `p=0.607239`, and
relative improvement was `10.000%`. These are auxiliary-Judge results from one
repeat, not manually calibrated or publishable accuracy numbers.

E2 top-k 20 is the provisional dev winner, but it is not promoted:

- the `+6 pp` auxiliary threshold passed;
- unanswerable accuracy regressed from `12/16` to `11/16`;
- answer completeness regressed from `31.579%` to `30.435%`;
- prompt-injection resistance improved from `4/8` to `7/8`;
- citation correctness improved to `100.000%`;
- pipeline failures remained zero;
- p95 latency remained below twice the B1 baseline.

Because two hard gates failed, the default profile remains unchanged. No
three-repeat confirmation or Gold v2 holdout run was started.

## Answerability and output-budget ablation

RAG-32 tested one generation coordinate at a time with E2 top-k 20 retrieval
held fixed. All measurements used `local_accuracy_dev_v1`, real Qdrant,
Qwen3 Embedding 4B, BGE reranking, LM Studio `qwen/qwen3.5-9b`,
`temperature=0.0`, and no retrieval cache. Gold v2 was not opened.

| Ablation | Run | Auxiliary pass | Completeness | Unanswerable | Injection | p95 | Decision |
|---|---:|---:|---:|---:|---:|---:|---|
| E2 top-k 20 | 82 | 82.500% (33/40) | 30.435% | 11/16 | 7/8 | 101.841 s | reference |
| A1 retry disabled | 86 | 85.000% (34/40) | 30.435% | 11/16 | 7/8 | 121.993 s | reject |
| A2 4,000-token-equivalent (`max_output_chars=16000`) | 88 | 90.000% (36/40) | 33.333% | 13/16 | 7/8 | 232.399 s | latency reject |
| A3 3,000-token-equivalent (`max_output_chars=12000`) | 90 | 87.500% (35/40) | 33.333% | 12/16 | 7/8 | 186.539 s | provisional candidate |
| A4 2,850-token-equivalent (`max_output_chars=11400`) | 92 | 86.842% (33/38 judged) | 33.333% | 11 pass, 2 Judge failures | 7/8 | 146.841 s | reject |

The token figures are approximate labels derived from a four-characters-per-token
budget. The enforced setting is the character cap shown above; actual token
counts vary by language and tokenizer.

A3 improved the one-repeat auxiliary pass rate over B1 from `75.0%` to
`87.5%` (`+12.5 pp`), with a paired bootstrap 95% CI of `[-5.0, +30.0] pp`
and exact McNemar `p=0.266846`. Citation correctness was `100%`,
completeness was `33.333%`, unanswerable was unchanged at `12/16`,
prompt injection improved from `4/8` to `7/8`, and pipeline failures were zero.

A3 nevertheless missed the original B1 p95 limit by `0.959 s`:
`186.539 s` versus `185.580 s`. A4 was then measured while other local tasks
were running; the observed GPU snapshot was 85% utilization with
`11.54 / 12.28 GiB` VRAM in use. Therefore A4's `146.841 s` p95 is recorded
for reproducibility but is not accepted as a promotion-gate result. A4 also
had two auxiliary-Judge failures, both on unanswerable cases, so it does not
establish the required no-regression result.

Further tuning on the same 40 cases stops here. A3 is frozen as the provisional
confirmation candidate:

- manifest:
  `local_rag_accuracy_e2_output_budget_3k_dev_v2.example.json`
- SHA-256:
  `AE98F837EE9B5AB439C69803D9B4CE832F7CAC423E10C8DDD4708C1287FA0A32`
- next gate: rerun B1 and A3 in an otherwise idle LM Studio/GPU window, then
  proceed to a separate `confirm_dev` only if all gates pass

These values remain auxiliary-Judge dev evidence, not a calibrated or public
accuracy claim. The default profile, PR #128, and Gold v2 remain unchanged.

## Security follow-up

After local accuracy validation, perform the repository threat-model phase for
retrieved-chunk prompt injection, corpus poisoning, tool authorization, Agentic budget
exhaustion, cloud escalation cost attacks, rate limits, daily budgets, and the
external-LLM data boundary. The repository-grounded plan is documented in
`docs/security/RAGProject-threat-model.md`. Accuracy promotion and security
promotion are separate gates.
