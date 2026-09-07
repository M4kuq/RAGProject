# RAG-90 Qwen timeout-contract-only validation

## Status

One-shot completed on 2026-09-07 under the pre-live GPU-only host amendment.
Formal result: `baseline_sensitivity_not_established` /
`rag90_baseline_sensitivity_not_established`. Candidate metrics are descriptive only;
candidate adoption is false and the baseline is retained. RAG-88 remains
`inconclusive / rag88_pipeline_failure`; its fixture, result, thresholds, and Draft PR are
unchanged and non-rerunnable.

## Single changed behavioral coordinate

RAG-90 changes only the generation timeout contract. It inherits the RAG-88 model, prompt,
Oracle-context construction, source order, budgets, retry policy, candidate pass-1 reuse,
generic repair task/schema, schedule shape, statistical gates, and latency guardrail. The new
fixture necessarily has new private identifiers, hashes, fingerprints, and schedule fingerprint,
but those identity changes do not alter model-facing behavior.

The timeout is selected before live by the following fixed formula:

```text
ceil_to_60s(
  RAG-88 right-censor boundary
  + max(RAG-84 baseline p95, RAG-88 successful-standard p95)
)
= ceil_to_60s(180.000 s + max(166.329 s, 159.420 s))
= ceil_to_60s(346.329 s)
= 360 s
```

Supporting raw-free observations are RAG-84's 166.329-second successful maximum and RAG-88's
179.741-second successful maximum, 40 calls censored at 180 seconds, and 14,460,586 ms of
physical-accounted sequential duration. RAG-84's latency snapshot was marked runtime-unstable;
that limitation is retained rather than hidden. The right-censor boundary and longer historical
p95 supply the conservative headroom; rounding to a whole minute removes false precision.

The frozen contract is:

- 360-second hard standard logical-call deadline, which bounds every contained physical request;
- the unchanged standard retry attempts share that single deadline;
- 360-second HTTP client operation timeout;
- one 360-second hard repair request/deadline;
- 720-second combined baseline-plus-repair logical upper bound;
- existing child cleanup only: terminate wait 5 seconds, then kill wait 5 seconds;
- 730-second candidate upper bound including the only applicable timeout cleanup path; and
- no live extension, case-specific timeout, extra retry, or extra grace.

Cancellation implementation, physical-call count/structure, output budget, prompt, source order,
candidate logic, and efficacy gates are unchanged. Physical request durations and logical
end-to-end durations are recorded separately with raw-free counters only.

## Independent fixture and schedule

- 12 new repository-external private safe-synthetic groups and 36 questions.
- Each group has single-A, single-B, combined baseline, and combined generic two-pass variants.
- Each variant uses the same six-source Oracle context and source order; required citations remain
  2 and 5.
- Three repeats use the inherited Latin rotations 0, 4, and 8, for 144 observations.
- The candidate reuses the paired combined-baseline answer and makes only the inherited generic
  repair request.
- The new private input SHA-256 is
  `b6d8138e1f589044d0c6e94f677b92e400aed2c9da97aeeaeed631814bdb70a5`.
- One-way hash checks against local_accuracy_dev_v1, RAG-84, RAG-87, and RAG-88 found zero
  question, normalized required-fact, source-content, or logical-source-identifier overlap.
  Reference text was not used for case design or persisted by RAG-90.
- The raw-free lock SHA-256 is
  `754cb70571028e7332327528f3d5d29e76d9713ab2de010574af77a52a91502c`.

## Frozen validity and decision gates

The inherited baseline-sensitivity gate requires at least 8 eligible groups, at least 6 incomplete
combined baselines, a drop of at least 0.5, a positive 10,000-resample paired-bootstrap lower
bound, exact paired p at most 0.05, zero baseline pipeline failures, and exact-target stability.

Candidate adoption is evaluated only if baseline sensitivity passes. It requires joint
completeness delta at least 0.25, at least 3 improved groups, positive bootstrap lower bound,
exact paired p at most 0.05, no atomic-recall/citation-grounding/source-coverage regression,
no false-insufficiency increase, zero unexpected or forbidden groups, zero pipeline/binding/
exclusion/replacement failure, and candidate logical p95 no more than 2 times baseline.

Any pipeline failure makes the run formally inconclusive. Candidate metrics then remain
descriptive and adoption is forbidden. The timeout is not extended and no failed call, case, or
repeat may be rerun or replaced.

## Pre-live host gate

### User-authorized amendment before the first live attempt

On 2026-09-07 the user explicitly requested one attempt under the current GPU load.
The runner therefore permits the GPU-only exception through an explicit confirmation flag.
The original 10% threshold and all three actual samples remain recorded; elevated load is
not represented as absent. The host snapshot and result expose
`gpu_load_exception_authorized=true`. This is a host-protocol amendment to the original
preregistration, and the result must be reported as measured under shared GPU load.
It does not establish isolated-host latency or a pure cross-run timeout causal effect.
The original fixture/lock, generation settings, timeout, schedule, and statistical/latency
decision gates remain unchanged. Target/context and concurrency checks still apply.
Task-owned target loading is recorded truthfully and only that added instance is removed
at the end. The amended runner must be committed and pushed before the single attempt.

The original default policy, retained unless this exception is explicitly authorized, is:

Immediately before the single attempt, a raw-free host snapshot must show exactly one loaded
`qwen/qwen3.5-9b` target at context length 12312, no concurrent evaluation, no concurrent model
load, and three GPU utilization samples each at or below 10%. External load is never stopped or
killed; a failed gate only postpones the start. Other aliases are not unloaded or reloaded.
RAG-86 exact-target-only pre/post stability is binding; unrelated full-inventory TTL drift is
recorded but not a validity gate.

## Safety and rollback

Raw question, source, context, answer, fact, and chain-of-thought values remain outside the
repository, Jira, PR, logs, and result artifact. Gold v2, application datastores, Qdrant, Neo4j,
Docker volumes, dirty root checkout, the user-owned PowerShell change, and Draft PRs #159-#161
are not modified. No merge, deploy, Draft removal, public accuracy claim, or profile promotion is
authorized.

Rollback is commit-scoped. The runner performs no application datastore write, so no datastore
rollback is required.

## Completed one-shot evidence

Original preregistration: `765285a94e2c348c857bb7264e855f918a13bfa5`.
Amended pre-live commit, pushed before dispatch:
`4a69434b4cc4ac98c8b11acc0d43987bb21da1bd`.
The single attempt ran approximately 18:35-19:45 JST on 2026-09-07. The attempt marker
remains consumed; no failed-case replacement, exclusion, extra repeat, or rerun occurred.

The direct user authorization was verified in task
`01a03c05-b71a-7570-a92b-3167dcb9d7f8`: after a question at 18:22:34.971 JST about
whether GPU 10% was necessary, the assistant explained at 18:23:39.787 that the value
was uncalibrated and a recorded pre-live amendment was required to proceed above it.
The user's next direct message requested one attempt in the current state at
18:24:13.277 JST, turn `01a07b2e-b2fc-7373-acda-93b06ec3e21c`.
This direct message, not another agent's instructions or generic LM startup permission,
was the authorization basis. It was reconfirmed from the local primary user-message
record during the run. The fixed runner has no safe pause/checkpoint interface; no
process was killed, suspended, or unloaded during generation.

Actual host samples were 7%, 15%, 27%, with five-second sampling waits. The original
`<=10% x3` condition **failed**. The unchanged recorded threshold is 10%,
`gpu_high_load_absent=false`, and `gpu_load_exception_authorized=true`.
The result's `validity_gate_passed=true` is an amended-protocol execution flag, not a
claim that the original host contract passed. Shared GPU load remains a limitation.

### Fixed-gate result

| Measure | Observed result |
| --- | --- |
| Scheduled observations / physical requests | 144 / 144 |
| Standard / repair physical requests | 108 / 36 |
| Physical timeouts / pipeline failures / derived unavailable | 0 / 0 / 0 |
| Binding drift / exclusions / replacements | 0 / 0 / 0 |
| Exact-target / full inventory stability | true / true |
| Eligible groups | 12 of 12 |
| Incomplete combined-baseline groups | 0 (required at least 6) |
| Single-control / baseline / candidate joint completeness | 1.0 / 1.0 / 1.0 |
| Interference drop; bootstrap 95% CI; exact paired p | 0.0; [0.0, 0.0]; 1.0 |
| Candidate joint delta; bootstrap 95% CI; exact paired p | 0.0; [0.0, 0.0]; 1.0 |
| Improved / regressed groups | 0 / 0 |
| Baseline / candidate atomic fact recall | 1.0 / 1.0 |
| Baseline / candidate citation grounding and source coverage | 1.0 / 1.0 each |
| Baseline / candidate false-insufficiency observations | 0 / 0 |
| Candidate unexpected / forbidden groups | 0 / 0 |
| Repair keep / revision observations | 36 / 0 |
| Baseline sensitivity / candidate adoption gate | false / false |

Each repeat contains 48 observations; each variant contains 36 observations. All physical
requests completed without retry amplification (one request per observation). The case-majority
ceiling prevented both the preregistered interference and candidate-improvement tests from
establishing an effect. Passing guardrails does not override the failed sensitivity gate.
This is not evidence that multi-fact interference cannot occur, or that repair is ineffective
on an independently sensitive population.

### Latency, separated by scope

| Scope | Count | p50 (s) | p95 (s) | Maximum (s) |
| --- | ---: | ---: | ---: | ---: |
| Standard physical request | 108 | 33.817 | 53.305 | 59.132 |
| Repair physical request | 36 | 3.005 | 3.360 | 3.447 |
| Combined baseline logical end-to-end | 36 | 29.547 | 48.266 | 48.650 |
| Combined candidate logical end-to-end | 36 | 36.477 | 55.612 | 56.074 |

Candidate p95 ratio is 1.152198 (15.22% higher), below the unchanged 2x guardrail,
but with no measured completeness gain. Physical-accounted sequential duration is
3,869,910 ms; it is not the total run wall clock. No standard request exceeded the old
180-second deadline. Thus this run does not isolate a beneficial effect of the 360-second
timeout extension: the new independent fixture and shared-host conditions differ from
RAG-88. It does not supersede RAG-88's inconclusive result or RAG-89's root-cause limits.

### Immutable raw-free artifact ledger

Artifacts remain in the existing repository-external `rag90-timeout-contract-20260827`
evidence directory. They were validated against strict models, complete observation/telemetry
counts, and canonical binding hashes. The result file is pretty-printed JSON, so its file-byte
SHA differs from canonical model hashes used in bindings; content equivalence is checked
using the repository's `model_bytes_match` convention. An initial post-run audit incorrectly
assumed the stored bytes were canonical; correcting that read-only check required no
artifact modification or new generation.

| Artifact | SHA-256 of file bytes |
| --- | --- |
| Original lock | `754cb70571028e7332327528f3d5d29e76d9713ab2de010574af77a52a91502c` |
| Host gate at amended commit | `c7641ebdbdda8208fd79ce559b3e622e279158fe98ba17a865f5e75d2c0c9c27` |
| Exclusive attempt marker | `2b6a9727f3c355268e1db31855fc2d52abdd86377f6e15a4811e185ddf4f7e85` |
| Final result | `5559511b886be2b285e9aa8e73188d6df565c902f37ac96cb8da1c4a75464103` |

### Verification and remaining risk

Before live: focused RAG-90/RAG-88 tests 20 passed; full backend 1125 passed,
21 skipped, 1 failed, 3 warnings. The failure was the existing SQLite timestamp-boundary
test `test_graph_index_worker_records_retryable_llm_failure_without_fallback`, raising
`ck_jobs_finished_after_started`; its isolated recheck passed. This reliability risk is
reported separately from the live result and was not fixed or hidden. Ruff lint passed,
Ruff format checked 322 files, and mypy checked 215 application source files. Isolated
Compose then passed 1126 tests with 21 skipped and 3 warnings. No local verification
workload ran concurrently with live generation. GitHub CI is checked on the final PR head.

The first GitHub runs on evidence head `70e60f1d4a98c6a5a65ab51498e0b1cf8d9b238e`
failed: Backend CI `34113538081` and Compose Smoke `34113538164` both reported the same
12 mypy argument-type errors in the new host-exception test. A heterogeneous `dict[str,
object]` expanded as keyword arguments obscured the tuple/int/bool parameter types. The
pre-live local `mypy app` check had not checked tests, unlike CI's `mypy .`.
The post-live fix uses explicit named test arguments and typed tuple iteration only;
it does not change application code, fixture, timeout, result, or any decision gate.
No type-ignore, test exclusion, live rerun, or artifact rewrite was used.

After the test-only fix: focused 20 passed; full backend 1126 passed / 21 skipped /
3 warnings; Ruff lint/format passed; `mypy .` checked all 322 files successfully.
The isolated no-network Compose service then passed Ruff, full-scope mypy and the full
suite (1126 passed / 21 skipped / 3 warnings). The original timestamp-boundary flake and
initial CI failures remain part of the record. Final-head CI links and conclusions are
tracked in Draft PR #162 and Jira RAG-90 without changing immutable live evidence.

After the result was written and the exact task-added target was confirmed idle with no
queued request and the final last-used binding unchanged, only that Qwen instance was
unloaded. Readback restored loaded count 0 and retained all 6 registered model entries.
Existing Docker services and volumes are not part of task cleanup.
The task-owned runner was removed after verifying no generation child remained; no task
container remains. The 12 original Docker services were retained. Two additional non-task
containers were present at final readback and were not touched, so continuous host-wide
quiescence is not asserted. Root dirty count remained 87 and the protected user script SHA
remained `8f807522d9b286eecd4ec46cdd7eea6c32d9926ae0b1775c9707b5ec7c646c36`.

Rollback reference is the untouched stacked base PR #161 at
`b063d545f90e28096ddefa5a815a0784f0699d1a`. No rollback was performed. The diagnostic
runner is not promoted or wired into production. If later authorized, revert only the
RAG-90-only commits in a clean review worktree; preserve the consumed attempt and all
result evidence. No datastore rollback or model-profile promotion is needed.

Next single-coordinate proposal (not executed): a new preregistered baseline-only
sensitivity study varying **Oracle context length** while fixing the number of required
facts, required citation ordinals, model/prompt/budgets/retry/timeout, and statistical gates.
Use a new independent private fixture, fixed length levels and one-shot schedule; never
increase difficulty repeatedly until a low score appears. RAG-90's all-complete baseline
and zero revisions motivate measuring context-utilization sensitivity before another repair
efficacy claim. This is a proposal, not an established cause or an authorization to rerun.
