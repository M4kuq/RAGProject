# RAG-86 Qwen Oracle-context near-miss diagnostic

## Status

Pre-live implementation for the diagnostic-only RAG-86 experiment. The frozen
authority is Jira RAG-86 comment `10098`, and the stacked base is Draft PR #157
head `f89e248d2294be9b41150f4ab9b8879e6329e536`.

The live experiment must not start until the implementation, lock manifest,
focused tests, full backend tests, Ruff, mypy, and pre-live commit/push gates
are complete. The live experiment is one-shot and must not be repeated.

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

## Rollback

Rollback is commit-SHA scoped. Revert the RAG-86 implementation commit and,
after the live run, the result-documentation commit. The runner performs no
datastore writes, so no datastore rollback is required. Repository-external
raw-free attempt/result artifacts may be retained for audit.
