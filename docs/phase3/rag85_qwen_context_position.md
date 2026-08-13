# RAG-85 Qwen Oracle-context position diagnostic

RAG-85 is a diagnostic-only experiment for one coordinate: the ordinal position of the
two required Oracle chunks. It uses the unchanged baseline generation prompt and exact
`qwen/qwen3.5-9b`. It does not adopt a prompt candidate, change a profile, open Gold v2,
or contribute to public accuracy.

## Frozen fixture and position coordinate

- Source fixture: the RAG-84 independent 14-case safe-synthetic confirm set, with 7
  Japanese and 7 English cases and exactly two required facts per case.
- Context set: the two original required chunks plus six fixed safe-synthetic non-answer
  spacers. Every condition has the same eight chunk texts, chunk IDs, source labels, and
  citation IDs.
- Required citations remain IDs 1 and 2. Their block occupies ordinals 1-2 for `front`,
  4-5 for `middle`, and 7-8 for `end`.
- The complete context fits below the fixed 6,000-character budget. Runtime rejects any
  chunk-set, citation binding, required-position, fixture, prompt, or manifest drift
  before generation; order-dependent clipping is prohibited.
- Each condition has three repeats. Per-case condition order rotates by repeat:
  `front/middle/end`, `middle/end/front`, then `end/front/middle`.
- The run has exactly `14 x 3 x 3 = 126` generations. An external one-shot attempt marker
  is written before the first generation. A failed or interrupted attempt is not repeated,
  extended, replaced, or filtered.

The committed raw-free lock is
`backend/app/evaluation/fixtures/rag85_qwen_context_position_lock.json`. It binds the RAG-84
fixture fingerprint, all case/question/fact/context hashes, every condition sequence,
the baseline prompt fingerprint, the generation contract, and the decision rule.

## Fixed generation contract

- model: exact `qwen/qwen3.5-9b`
- provider: loopback LM Studio only
- temperature: `0`
- reasoning: off
- prompt profile: unchanged `baseline`; the rejected RAG-84 evidence-ledger candidate is
  not used
- context/output/token budgets: `6000 / 12000 / 8192`
- retry: existing evaluation generation retry
- case wall-clock timeout: 180 seconds

## Predeclared primary decision

For each case, required fact, and condition, a fact is positive when at least two of three
repeats match the calibrated deterministic identifier-equivalence rule. The primary score
is the mean of the two majority fact indicators per case, paired across the same 14 cases.

Each of `front-middle`, `front-end`, and `middle-end` must independently satisfy all three
criteria to count as position-dependent:

1. absolute paired majority recall delta is at least 0.15;
2. a 10,000-resample case-paired percentile bootstrap 95% interval excludes zero, using
   seed 85085 and linear-interpolation type-7 percentiles;
3. the two-sided exact sign-flip p-value over non-zero case differences has Holm-adjusted
   `p <= 0.05` across the three comparisons.

The overall result is:

- `position_dependence_detected` when the validity gate passes and at least one comparison
  passes all three primary criteria;
- `no_detectable_position_dependence` when the validity gate passes but none does;
- `inconclusive` when binding, case, pipeline, or LM inventory validity fails.

Validity requires zero binding drift, zero case exclusions/replacements, zero pipeline
failures, and identical LM Studio inventory immediately before and after the 126 calls.
The RAG-84 tune baseline recall from Jira comment 10093 is recorded only as a descriptive,
different-case-set reference. Reference drift never excludes or replaces an observation.

Whole completeness, whole-statement exact rate, false insufficiency, citation grounding,
required source coverage, unexpected/forbidden facts, per-repeat recall, and p95 latency are
secondary. They do not enter the primary position-dependence gate.

## One-shot local execution

Commit the implementation, tests, documentation, and lock before loading the model. Use the
same 40-character commit SHA for the entire run. Output and attempt paths must be new,
repository-external, and outside every Git checkout.

Record the original LM inventory, then load only the target model if needed:

```powershell
lms ps
lms load qwen/qwen3.5-9b --exact --identifier qwen/qwen3.5-9b -y
```

Run once from `backend`:

```powershell
python -m app.scripts.run_evaluation_qwen_context_position `
  --prelive-commit <40-char-pre-live-commit> `
  --attempt-marker <repository-external-new-attempt-path> `
  --output <repository-external-new-result-path> `
  --confirm-local-only `
  --confirm-one-shot
```

The CLI refuses a non-matching HEAD, uncommitted RAG-85 changes, repository-local/existing/
symlinked outputs, unavailable inventory, or a target loaded zero or multiple times. The
known user-owned `scripts/test_nvidia_generation.ps1` difference is allowed but never
edited or staged. Progress contains only condition/repeat/count/failure metadata. The
attempt marker and final artifact contain hashes, counts, booleans, rates, latencies, and
stable reason codes; they never persist question, source, context, chunk, answer, or fact
text.

After the result is written, restore the exact original load state. If the target was
initially unloaded, use:

```powershell
lms unload qwen/qwen3.5-9b
lms ps
```

Do not rerun when the validity gate fails or the attempt is interrupted. Preserve the
attempt marker and report the stable raw-free reason.

## Safety and rollback

The runner performs no retrieval, database write, Qdrant/Neo4j access, Docker volume
mutation, Gold access, profile change, merge, or deployment. The already-stopped RAG-83
listener is not started or stopped by this work.

Rollback is commit-SHA scoped: revert the RAG-85 pre-live implementation commit and any
separate post-live documentation-only commit, then remove the unmerged branch/PR only if
separately authorized. Repository-external raw-free artifacts may be retained for review.
No datastore rollback is required because the runner does not mutate a datastore.
