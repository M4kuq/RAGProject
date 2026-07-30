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

## Dev-only Oracle Context diagnostic (2026-07-30)

Run 112 was diagnosed with the same `qwen/qwen3.5-9b`, temperature `0.0`,
`generation_max_context_chars=6000`, `generation_max_output_chars=12000`, and
`generation_max_output_tokens=8192`. The R condition used the frozen answers and the
case-level decisions from the stable three-repeat Judge replay. The O condition
replaced only the retrieved context with each dev case's expected evidence source.
It did not read or inject `expected_answer`.

The first attempted comparison was discarded before use because the older judgments
stored in the database reported `34/40`, while the stable replay reported `27/40`.
The accepted diagnostic requires all three replay decisions, answer/context hashes,
case set, dataset, corpus, and model to match before Oracle generation starts.

| Metric | R: frozen retrieval | O: Oracle Context | Delta |
|---|---:|---:|---:|
| Auxiliary Grounded Answer Pass | 67.5% (27/40) | 72.5% (29/40) | +5.0 pp |
| Answerable auxiliary pass | 75.0% (18/24) | 70.833% (17/24) | -4.167 pp |
| Unanswerable auxiliary pass | 56.25% (9/16) | 75.0% (12/16) | +18.75 pp |
| Mean Claim Recall | 0.875 | 1.000 | +0.125 |
| Mean deterministic Context Utilization | 0.333333 | 0.312500 | -0.020833 |
| Pipeline failures | 0 | 0 | unchanged |

Answerable transitions were 15 pass/pass, 2 fail/pass, 3 pass/fail, and 4 fail/fail.
The two Oracle rescues already had all required facts in R, so no answerable rescue
was classified as a missing-retrieval gap. Seven answerable cases still failed with
complete Oracle evidence: `local_dev_answerable_02`, `_08`, `_14`, `_16`, `_17`,
`_20`, and `_24`. Four of these are the answerable prompt-injection cases.

All eight prompt-injection cases failed the composite auxiliary pass in both
conditions. This does not by itself prove that the injected instruction succeeded;
the composite can fail on another rubric dimension. Security dimension results must
remain a separate RAG-31 measurement.

The deterministic Context Utilization value uses exact normalized fact-statement
matches. It is additive diagnostic evidence, not semantic completeness: for example,
an auxiliary-pass answer can paraphrase a fact and score zero on this detector. It
must not replace the auxiliary Judge or human calibration.

The accepted run completed 40/40 comparable cases in 2,438 seconds, with no Judge
retry recovery and no pipeline failure. The safe artifact contained 40 case records,
valid answer/context hashes, no forbidden raw-content keys, and zero matches against
932 frozen raw payload values. The full raw-free artifact is intentionally not
committed.

Reproducibility fingerprints:

- dataset: `fb28ebfa7faf894d3850131bdb4fe14abe8ce0e6805f3b7806cb846c87905b6e`
- corpus: `f28554eeab050420119adff43669ad78c9380d395dd9bd131a94c460c0634333`
- case set: `83a02913a5143a061d61427613d4956b8e6273f73468a019a997ddd620fa5419`
- generation config: `886564b7237786e113b1acc105b664e041101606c4fbd8700c7a1e5c8615e898`
- R Judge replay: `5c58833e55b548aeb9225a53b224b4d518dabf717a3f83ea8bc79d60220b7495`

The next local accuracy work should therefore prioritize:

1. dimension-level and human review of the seven Oracle answerable failures;
2. a fixed-Oracle ablation of evidence grouping and multi-fact answer instructions,
   without changing retrieval or output budget in the same comparison;
3. prompt-injection handling as a separate security gate;
4. only then, position/noise perturbations and retrieval changes.

A 4B generator is not the next accuracy experiment. It would change the generator
while the 9B generation gap is still unresolved. Evaluate 4B later as an independent
cost/latency routing candidate with the same frozen cases and no-regression gates.

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

| Candidate | Run | Auxiliary pass | Paired delta vs B1 | Citation correctness | Completeness | p95 latency | Unanswerable | Injection-tagged overall pass | Judge failures |
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
- overall auxiliary pass on the eight prompt-injection-tagged cases improved
  from `4/8` to `7/8`; this is not a dedicated resistance measurement;
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

| Ablation | Run | Auxiliary pass | Completeness | Unanswerable | Injection-tagged overall pass | p95 | Decision |
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
overall auxiliary pass on injection-tagged cases improved from `4/8` to `7/8`,
and pipeline failures were zero.

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

### Idle-window A3/A2 confirmation

On 2026-07-29, 56 unrelated running containers were stopped without deleting
containers, images, networks, or volumes. The exact container names and restore
procedure are recorded in
`artifacts/experiments/docker_quiet_window_20260729_2122.json`. Only the four
healthy RAGProject services remained running. LM Studio continued to use the
same loaded Qwen3.5 9B generation model, Qwen3 Embedding 4B, temperature, prompt,
retrieval profile, and dataset. The execution order was reversed from the
earlier measurement (`A3` then `A2`) to reduce order bias.

| Candidate | Screening run | E2E run | Auxiliary pass | Unanswerable | Citation correctness | Completeness | p95 | Pipeline failures |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A3, 3,000-token-equivalent | 93 | 94 | 85.000% (34/40) | 12/16 | 100.000% | 33.333% | **149.395 s** | 0 |
| A2, 4,000-token-equivalent | 95 | 96 | **87.500% (35/40)** | **13/16** | 100.000% | 33.333% | 210.967 s | 0 |

Both candidates retained Recall@K `87.500%` and MRR `72.473%`. Compared with
the earlier runs, A3 p95 fell `19.912%` and A2 p95 fell `9.222%`. Reduced
background load therefore improved both measurements, but did not explain the
remaining A2 tail:

- A2 still exceeded the existing B1 twice-baseline limit (`185.580 s`) by
  `25.387 s`, or `2.274x` the B1 p95.
- A3 was `1.610x` the existing B1 p95 and below that existing limit.
- A2 had only one additional passing case over A3 (`+2.500 pp`, paired bootstrap
  95% CI `[-5.000, +10.000] pp`, exact McNemar `p=1.0`) while its p95 was
  `41.214%` higher, total tokens `5.746%` higher, and output tokens `11.615%`
  higher.
- The slowest A2 and A3 cases were dominated by generation time, especially
  unanswerable cases. Retry-enabled cases could consume two capped attempts:
  up to 8,000 reported output tokens for A2 and 6,000 for A3.

Against run 57, the strict paired comparison for idle-window A3 was `+10.000 pp`
with 95% CI `[-7.500, +27.500] pp` and exact McNemar `p=0.423950`. For A2 it
was `+12.500 pp`, 95% CI `[-5.000, +32.500] pp`, and exact McNemar
`p=0.301758`. These intervals include no improvement and remain uncalibrated
single-repeat auxiliary-Judge evidence.

A3 remains the preferred provisional candidate: A2 continues to fail the
existing latency gate, while its one-case quality advantage over A3 is not
statistically established. Promotion is still blocked because B1 was not rerun
in the same idle window. The next controlled latency check is an idle-window B1
rerun followed by A3 under the same load and order controls; Gold v2 remains
closed.

The subgroup recomputation also exposed a security measurement gap. All eight
prompt-injection-tagged cases in runs 57, 88, 90, 94, and 96 had
`prompt_injection_resisted=not_applicable`. Therefore values such as `7/8`
measure overall auxiliary pass within the tagged subgroup, not prompt-injection
resistance. Security non-regression must remain a separate RAG-31 gate until the
contract is propagated to the dedicated judgment dimension.

Qwen3.5 4B generation is not mixed into this A2/A3 comparison. It would change
the fixed-generation-model coordinate and invalidate the current paired claim.
Because the local 4B model is already available, it is a useful later
efficiency experiment with A3 frozen: compare 4B, 9B, and a 4B-to-9B cascade on
dev/confirm using three repeats and manual calibration. It should be evaluated
for latency and escalation cost, not treated as an unproven accuracy
improvement. Qwen3 Embedding 4B is already used in A2 and A3.

### Three-repeat B1/A3 idle-window confirmation

On 2026-07-30, the previously running non-RAG containers were stopped without
deleting containers, images, networks, or volumes. The exact reversible state
is recorded in
`artifacts/experiments/docker_quiet_window_20260730_0940.json`. The six valid
end-to-end runs used Qwen3.5 9B, temperature `0.0`, disabled retrieval cache,
the same 40-case `local_accuracy_dev_v1` dataset, fixed corpus and generation
prompt, and the frozen B1 or A3 manifest. The controlled order was
`B1, A3, A3, B1, B1, A3`, which balances profile position across the six-run
window. Runs `100` and `102` were excluded with explicit
`invalid_parallel_measurement` and `invalid_interrupted_measurement` reason
codes; no data was deleted.

| Profile | Repeat | E2E run | Auxiliary pass | Unanswerable | Citation correctness | Completeness | p95 | Pipeline failures |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 | 1 | 98 | 72.500% (29/40) | 12/16 | 84.211% | 31.579% | 105.093 s | 0 |
| B1 | 2 | 108 | 75.000% (30/40) | 12/16 | 84.211% | 31.579% | 82.864 s | 0 |
| B1 | 3 | 110 | 75.000% (30/40) | 12/16 | 84.211% | 31.579% | 89.185 s | 0 |
| A3 | 1 | 104 | 85.000% (34/40) | 12/16 | 100.000% | 33.333% | 155.527 s | 0 |
| A3 | 2 | 106 | 87.179% observed (34/39); 85.000% conservative (34/40) | 12/15 observed; 12/16 conservative | 100.000% | 33.333% | 128.728 s | 0 |
| A3 | 3 | 112 | 85.000% (34/40) | 12/16 | 100.000% | 33.333% | 150.908 s | 0 |

Run `106` had one failed auxiliary judgment,
`local_dev_unanswerable_37` / `judge_failed`. The conservative aggregate counts
that missing judgment as a failed case instead of silently removing it from the
denominator. Its other two A3 repeats agree, so all 40 case-majority outcomes
remain resolvable.

| Three-repeat aggregate | B1 | A3 | Delta / ratio |
|---|---:|---:|---:|
| Mean conservative auxiliary pass | 74.167% | 85.000% | **+10.833 pp** |
| Case-majority pass | 75.000% | 85.000% | **+10.000 pp** |
| Mean conservative unanswerable accuracy | 75.000% | 75.000% | 0.000 pp |
| Mean citation correctness | 84.211% | 100.000% | +15.789 pp |
| Mean answer completeness | 31.579% | 33.333% | +1.754 pp |
| Mean p95 latency | 92.381 s | 145.054 s | **1.570x** |
| Pipeline failures | 0 | 0 | 0 |

The 40-case majority comparison produced a `+10.000 pp` absolute change and
`+13.333%` relative improvement. Its 10,000-sample paired-bootstrap 95%
confidence interval was `[-7.500, +27.500] pp`, and exact McNemar was
`p=0.423950`. Each paired repeat was strictly comparable. The candidate passed
the `+6 pp`, unanswerable, citation, completeness, pipeline, and p95-within-2x
numeric gates, but the confidence interval includes no improvement and one
Judge failure remains.

Therefore A3 is **not promoted to `confirm_dev`** by this automatic run. It
remains the preferred frozen candidate. The next accuracy action is a separate,
reviewable Judge-reliability ablation: add bounded automatic Judge retry with an
explicit attempt count and terminal reason code, retain the first failure as
audit evidence, and repeat confirmation without changing retrieval, prompt, or
output budget. A second-model or human calibration gate is still required
before any public accuracy claim.

All 24 prompt-injection-tag observations per profile still record
`prompt_injection_resisted=not_applicable`. Prompt-injection resistance was not
used to inflate the accuracy result and remains a separate RAG-31 security
gate. Gold v2, the default profile, PR #128, and `feature/local-rag-accuracy`
were not changed. Safe artifacts are stored under
`artifacts/experiments/b1a3-confirm-20260730/`; they contain no raw question,
answer, or retrieved-context text.

## Security follow-up

After local accuracy validation, perform the repository threat-model phase for
retrieved-chunk prompt injection, corpus poisoning, tool authorization, Agentic budget
exhaustion, cloud escalation cost attacks, rate limits, daily budgets, and the
external-LLM data boundary. The repository-grounded plan is documented in
`docs/security/RAGProject-threat-model.md`. Accuracy promotion and security
promotion are separate gates.
