# RAG-90 Qwen timeout-contract-only validation

## Status

Pre-live contract frozen. No RAG-90 model call has started. RAG-88 remains
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
