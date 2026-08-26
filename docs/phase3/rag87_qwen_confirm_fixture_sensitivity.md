# RAG-87 independent confirm fixture sensitivity

## Status

RAG-87 is a diagnostic-only baseline measurement. Its purpose is to determine
whether an independent confirm fixture can detect the multi-fact completeness
difficulty observed by RAG-84 before any candidate change is evaluated. Jira
RAG-87 comment `10133` is the frozen authority. The stacked base is Draft PR
#158 head `a62977d3ac1c8d411c1441f886db9b96011995dd`.

This document describes the pre-live contract. The single authorized live run
must not start until the implementation, tests, this document, and the raw-free
lock are committed and pushed.

## Fixture boundary

- Use 12 independently generated safe-synthetic cases: 6 Japanese and 6
  English, including 2 prompt-injection-tagged cases.
- Every case has two sources, two required facts, and two required citations.
- Match the RAG-84 tune selection exactly on the frozen structural feature
  vectors: question/source/context/fact character lengths, fact-start distance,
  cross-source ASCII-token Jaccard overlap, language ratio, injection-tag
  ratio, source order, and citation count.
- Require zero overlap with `local_accuracy_dev_v1` for question hashes,
  normalized-fact hashes, source-content hashes, and logical document IDs.
- Generate the private fixture from seed `87087` and namespace
  `R87-sensitivity-20260826` plus one-time private entropy, only at a
  repository-external path. The entropy is not persisted; the resulting input
  is bound by SHA-256. The repository stores hashes and structural aggregates,
  never instantiated question, source, context, answer, or fact text.
- Gold v2 is neither opened nor changed. Existing local tune content is not
  copied into the confirm fixture.

Case selection, replacement, exclusion, post-result difficulty adjustment,
extra repeats, and reruns are prohibited.

## Frozen baseline execution

- Model: exact `qwen/qwen3.5-9b`.
- Prompt profile: `baseline`.
- Temperature: `0.0`.
- Reasoning: disabled.
- Context/output/token budgets: `6000 / 12000 / 8192`.
- Loaded context length: `12312`.
- Existing evaluation generation retry policy and 180-second hard timeout.
- Source order: first required fact, then second required fact.
- Execution order: repeat, then case key ascending.
- Repeats: 3 per case; total `12 × 3 = 36` generations, once only.
- No candidate comparison is permitted in this run.

## Decision rule

The primary metric is case-majority atomic required-fact recall. A required
fact is supported when at least two of three repeats match its deterministic
identifier-equivalence contract.

Fixture sensitivity is established only when every pre-registered check
passes:

1. majority supported facts are between 9 and 20 of 24 inclusive;
2. at least 2 cases have both facts supported by majority;
3. at least 2 cases support only the first fact while asserting insufficient
   evidence in a majority of repeats;
4. first-fact majority recall minus second-fact majority recall is at least
   `0.166667`;
5. the maximum minus minimum per-repeat atomic recall is at most `0.25`; and
6. every validity gate passes.

The formal conclusions are `fixture_sensitivity_established`,
`fixture_sensitivity_not_established`, or `inconclusive`. A failure must not be
used to rebuild the fixture or adjust a threshold. The follow-on two-pass
experiment is authorized only by `fixture_sensitivity_established`.

## Validity and raw-free evidence

Validity requires zero pipeline failures, binding drift, case exclusions, and
case replacements. Before and after the run, the exact target must have the
expected model-ID fingerprint, exactly one loaded instance, context length
`12312`, and the same target-entry fingerprint. Full non-target inventory drift
is recorded but is non-binding when the exact target remains stable.

The lock, attempt marker, result, progress output, Jira evidence, PR evidence,
and CI evidence contain only hashes, fingerprints, counts, booleans, metrics,
and stable reason codes. Attempt and result paths must be repository-external,
absent, non-symlinked, and distinct. The attempt marker is created exclusively
before generation 1.

The dedicated runner rejects an unexpected branch, HEAD, stacked base, or
dirty path. The inherited user-owned
`scripts/test_nvidia_generation.ps1` is the only allowed dirty path and is not
edited, staged, or reverted. The root checkout, PR #158, LM Studio shared
starting state, Gold v2, DB, Qdrant, Neo4j, existing containers, and volumes
remain protected.

## Pre-live preparation and execution

Generate the private input and raw-free lock at new external paths:

```powershell
python -m app.scripts.run_evaluation_qwen_confirm_fixture_sensitivity `
  prepare-private `
  --output $RAG87_PRIVATE_INPUT `
  --confirm-repository-external-private-output

python -m app.scripts.run_evaluation_qwen_confirm_fixture_sensitivity `
  build-lock `
  --private-input $RAG87_PRIVATE_INPUT `
  --output $RAG87_EXTERNAL_LOCK
```

After reviewing the lock, commit and push its exact raw-free bytes with the
implementation. Then run exactly once:

```powershell
python -m app.scripts.run_evaluation_qwen_confirm_fixture_sensitivity `
  run `
  --private-input $RAG87_PRIVATE_INPUT `
  --prelive-commit $PRELIVE_COMMIT `
  --attempt-marker $RAG87_ATTEMPT_PATH `
  --output $RAG87_RESULT_PATH `
  --confirm-local-only `
  --confirm-one-shot
```

Verification order is focused tests, full backend tests, Ruff, mypy, isolated
Compose, pre-live commit and push, one-shot live execution, raw-free artifact
validation, result-documentation commit and push, and GitHub CI.

Pre-live verification completed with 9 focused tests passing, 1,098 local
backend tests passing with 19 skipped, Ruff format/check passing across 315
files, and lock-version mypy passing across 210 source files. The final
isolated Compose run passed Ruff, mypy, and 1,108 tests with 9 skipped. Its
first full attempt exposed one RAG-87 Linux checkout-boundary test assumption,
which was corrected, plus one unrelated auth-session timing failure that
passed immediately in isolation; the complete Compose suite then passed on
the required rerun. The final run reported 18 existing dependency/marker
warnings. All exact temporary Compose containers and its explicit isolated
network were removed after verification.

## Rollback

Rollback is commit-SHA scoped: revert the pre-live implementation commit and,
after live evidence is recorded, the result-documentation commit. The runner
does not write to a datastore, so no DB, Qdrant, Neo4j, or volume rollback is
required. Repository-external private input and raw-free attempt/result
artifacts are audit material and are never committed.
