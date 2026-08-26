# RAG-86 Qwen Oracle-context near-miss diagnostic

## Status

The diagnostic-only RAG-86 experiment completed its single authorized live run.
The frozen authority is Jira RAG-86 comment `10098`, and the stacked base is
Draft PR #157 head `f89e248d2294be9b41150f4ab9b8879e6329e536`.

The implementation and frozen lock were committed and pushed at pre-live SHA
`54a8200f2ea86ae8b2bb7fea93e321d29e9c9261` before the experiment started.
The one-shot experiment is complete and must not be repeated.

## Live result

- Formal conclusion: `no_detectable_near_miss_causal_effect`.
- Validity gate: passed; exact target remained stable.
- Executions: 84 of 84, with zero pipeline failures, exclusions,
  replacements, or binding drift.
- Primary majority atomic required-fact recall: clean `1.0`, near-miss `1.0`.
- Primary delta (near-miss minus clean): `0.0`.
- Paired bootstrap 95% interval: `[0.0, 0.0]`.
- Exact sign-flip p-value: `1.0`.
- The effect-size, confidence-interval, and exact sign-flip gates all failed,
  so the combined causal-effect gate did not pass.
- Per-repeat atomic required-fact recall was `1.0` in all three repeats for
  both conditions.
- Majority whole completeness and majority citation grounding were `1.0` for
  both conditions.
- Near-miss contamination and adoption rates were `0.0`.
- p95 latency was `80783` ms for clean and `81775` ms for near-miss.
- Result artifact SHA-256:
  `13fd5c78aa2b4c1713e622ea70d3b1d95bfb8eccbfd875752836c8d126375547`.
- Stable reason code: `rag86_no_detectable_near_miss_causal_effect`.

The result and attempt marker passed strict schema validation, model-dump byte
equivalence, observation-count validation, and the raw-free key gate. The
runner restored LM Studio to its starting state of zero loaded models. The
result is diagnostic only and does not authorize a mitigation, confirmation,
profile promotion, merge, deployment, or production behavior change.

## Frozen coordinate

- Reuse the independent 14-case RAG-84 safe-synthetic non-Gold fixture.
- Keep the two required citations at middle ordinals 4 and 5.
- Keep all eight chunk positions, IDs, labels, citation IDs, questions,
  required evidence, prompt, model, budgets, retry, and timeout fixed.
- Change only citation 5 at ordinal 3 from the clean irrelevant spacer to the
  case-specific safe-synthetic near-miss spacer.
- Bind the clean and near-miss target lengths to 141 and 159 characters,
  respectively, for a fixed rounded ratio of `1.12766`.
- Prove that all 14 alternate identifiers are unique and do not collide with
  the selected case, required evidence, source context, or clean spacer.
- Reject instruction, prompt-injection, PII, secret, URL, and credential
  markers in the near-miss spacer.

Gold v2 is not accessed by this implementation.

## Frozen execution

- Model: exact `qwen/qwen3.5-9b`.
- Prompt profile: `baseline`.
- Temperature: `0.0`.
- Reasoning: disabled.
- Context/output/token budgets: `6000 / 12000 / 8192`.
- Loaded context length: `12312`.
- Hard case timeout: 180 seconds.
- Repeats: 3 per condition.
- Total: `14 × 2 × 3 = 84` generations, once only.
- Deterministic case/repeat parity produces 21 clean-first and 21
  near-miss-first pairs, with 7/7 order balance in every repeat.

No failed-case replacement, exclusion, extra repeat, or rerun is allowed.

## Decision rule

The only primary metric is case-paired majority atomic required-fact recall.
Each fact is positive when at least two of the three repeats match. The primary
delta is near-miss minus clean.

A causal effect is detected only when all three conditions hold:

1. absolute delta is at least `0.15`;
2. the seed `86086`, 10,000-resample case-paired bootstrap 95% interval using
   type-7 interpolation excludes zero; and
3. the two-sided exact sign-flip p-value is at most `0.05`.

There is one primary comparison and no multiple-testing adjustment. Whole
completeness, false insufficiency, near-miss contamination/adoption, citation
grounding/source coverage, unexpected/forbidden facts, p95 latency, and
per-repeat stability are exploratory only.

The valid conclusions are:

- `near_miss_causal_effect_detected`;
- `no_detectable_near_miss_causal_effect`; or
- `inconclusive` when a validity gate fails.

Even a significant result does not authorize mitigation, confirmation,
profile promotion, merge, deployment, or a production behavior change.

## Exact-target validity

Validity requires zero pipeline failures, binding drift, exclusions, and
replacements, plus stable exact-target bindings before and after the run:

- exact target model ID fingerprint;
- exactly one loaded exact-target instance;
- loaded context length `12312`; and
- exact target-entry fingerprint.

The full LM inventory fingerprint is recorded. Non-target inventory drift is
non-binding when all exact-target checks pass. This target-scoped rule is the
RAG-86 contract and must not be replaced with the RAG-85 full-inventory gate.

## Raw-free and one-shot boundaries

- The lock manifest, attempt marker, result artifact, CLI progress, Jira
  evidence, and PR evidence contain only IDs, hashes, aggregates, booleans,
  metrics, and stable reason codes.
- Raw question, source, context, required fact, or generated answer text is not
  persisted in the result artifact or normal logs.
- Attempt and result paths must be repository-external, absent before the run,
  non-symlinked, and distinct.
- The attempt marker is created with exclusive-create semantics before the
  first generation.
- The CLI rejects an unexpected branch, HEAD, stacked base, or dirty path.
  The only allowed inherited dirty path is
  `scripts/test_nvidia_generation.ps1`; it is never edited or staged.
- The root checkout, PR #157, Gold v2, DB, Qdrant, Neo4j, and existing Docker
  volumes remain unchanged.
- LM Studio is restored to its starting loaded state after artifact validation.

## Pre-live verification and execution

Build the pre-live lock from `build_rag86_experiment_manifest()` and commit it
with the service, CLI, tests, and this document. Verify the frozen lock through
the focused test before the live run.

Run the one-shot command only from the committed dedicated branch, with new
repository-external paths:

```powershell
python -m app.scripts.run_evaluation_qwen_context_near_miss `
  --prelive-commit $PRELIVE_COMMIT `
  --attempt-marker $RAG86_ATTEMPT_PATH `
  --output $RAG86_RESULT_PATH `
  --confirm-local-only `
  --confirm-one-shot
```

Required verification order:

1. focused RAG-86 tests;
2. full backend tests;
3. Ruff format/check;
4. mypy;
5. isolated Compose verification;
6. pre-live commit and push;
7. the single live 84-generation run;
8. post-live artifact schema/byte/hash/raw-free validation;
9. post-live result documentation, commit, push, and GitHub CI.

Pre-live verification completed with 14 focused tests passing, 1,087 full
backend tests passing with 21 skipped, Ruff format/check passing, and mypy
passing across 311 source files. Isolated Compose verification completed with
1,099 tests passing and 9 skipped; its exact temporary containers, volumes, and
network were removed after verification. GitHub CI status is recorded on the
stacked Draft PR.

## Rollback

Rollback is commit-SHA scoped. Revert the RAG-86 implementation commit and,
after the live run, the result-documentation commit. The runner performs no
datastore writes, so no datastore rollback is required. Repository-external
raw-free attempt/result artifacts may be retained for audit.
