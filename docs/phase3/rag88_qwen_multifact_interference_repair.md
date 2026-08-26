# RAG-88 Qwen multi-fact interference and generic two-pass repair

## Status

The single authorized live run is complete. Its formal conclusion is `inconclusive` because
the pre-registered zero-pipeline-failure validity gate failed. The candidate is not adopted,
and the existing baseline is retained.

RAG-88 is independent from the RAG-87 sensitivity decision. It does not reinterpret the
RAG-87 fixture or use its false-insufficiency wording gate. The experiment first establishes
within-case interference with single-fact controls and only then permits a formal candidate
adoption decision.

## Fixed paired design

- 12 repository-external private safe-synthetic case groups.
- Each group uses one frozen six-source Oracle context and source order for every variant.
- Required evidence is bound to citations 2 and 5.
- Variants are single-A, single-B, combined baseline, and combined generic repair.
- The combined candidate reuses the exact paired combined-baseline answer as pass 1. Only the
  generic repair audit is an additional model call.
- Three repeats use predeclared case rotations 0, 4, and 8. Single-A/single-B order alternates;
  combined baseline is immediately followed by its paired repair audit.
- Total: 144 model calls and 144 raw-free variant observations, including 36 repair calls.

The private fixture was generated once with namespace
`R88-interference-repair-20260826` and seed `88088`. Its SHA-256 is
`61a1e92df5e894da180dd5362219a9b069a8c641abf97b195201252b1503453b`.
The raw-free lock SHA-256 is
`a2356dab10d5778063234c8f6bbc68e6aa3d3c10fd4384f9cacb6e3e86e7eaad`, and the
experiment manifest SHA-256 is
`207d163d44fa8d98d0e6d6d1d75e16f15171d6b637a42110f147a8c4f44dba91`.

One-way hash checks found zero overlap with local_accuracy_dev_v1, the RAG-84 independent
confirm set, and the fixed RAG-87 private fixture for questions, normalized required facts,
source content, and logical source identifiers. Reference content was not used for case design
and is not persisted by RAG-88.

## Frozen generation contract

- Exact model: `qwen/qwen3.5-9b`.
- LM Studio context: `12312`.
- Temperature: `0.0`; reasoning disabled.
- Context/output/token budgets: `6000 / 12000 / 8192`.
- Existing evaluation generation retry and a 180-second hard timeout per model call.
- RAG-86 exact-target-only pre/post stability is binding. Non-target/full-inventory drift is
  recorded but is not a validity gate.
- Baseline uses the existing baseline generation prompt.
- Repair sees only the user question, the same Oracle context, and the paired pass-1 answer.
  It receives no evaluator required fact, expected answer, or evaluator fact ID.
- Repair emits only a structured keep/revise, coverage, insufficiency, and citation decision.
  A revision is allowed at most once. Chain-of-thought is neither requested nor persisted.

## Frozen sensitivity gate

An eligible group requires both single-A and single-B to be case-majority correct and citation
grounded. The primary interference value is eligible single-controls joint completeness minus
eligible combined-baseline joint completeness.

Sensitivity is established only when all conditions pass:

1. at least 8 eligible groups;
2. at least 6 eligible groups are combined-baseline incomplete;
3. interference drop is at least `0.5`;
4. the pre-seeded 10,000-resample paired bootstrap 95% lower bound is greater than zero;
5. the two-sided exact paired test has `p <= 0.05`; and
6. baseline pipeline failures are zero and exact-target validity passes.

If this gate fails, all candidate values are descriptive/inconclusive and adoption is forbidden.

## Frozen candidate adoption gate

The primary candidate value is eligible combined-candidate joint completeness minus eligible
combined-baseline joint completeness. Adoption requires every condition below:

- baseline sensitivity passed;
- joint-completeness delta at least `0.25`;
- at least 3 improved groups;
- paired bootstrap 95% lower bound greater than zero;
- two-sided exact paired `p <= 0.05`;
- atomic fact recall, citation grounding, and citation source coverage do not decline;
- false insufficiency does not increase;
- unexpected-fact and forbidden-claim majority-group counts are zero;
- pipeline failure, binding drift, exclusion, and replacement counts are zero; and
- candidate end-to-end p95 latency is no more than 2 times combined baseline.

All cases are answerable, so the unanswerable-error guardrail is not applicable. RAG-88 does not
authorize a production profile change, public accuracy claim, Gold evaluation, merge, or deploy.

## One-shot and safety boundary

The private fixture, lock, prompt/schema fingerprints, schedule, statistics, thresholds, and all
variants must be committed and pushed before the only authorized live run. Failed-case
replacement, case exclusion, added repeats, rerun, and post-result threshold or fixture changes
are forbidden.

Raw question, source, context, answer, fact, and chain-of-thought values remain outside the
repository, Jira, PR, normal logs, and result artifact. The result stores hashes, aggregates,
booleans, enums, latencies, and stable reason codes only. Gold v2, the dirty root checkout,
user-owned PowerShell change, PR #159, DB, Qdrant, Neo4j, and existing Docker volumes are not
modified.

## Verification and result

Pre-live verification completed before any model call:

- focused RAG-88 tests: 11 passed;
- full backend final run: 1,107 passed and 21 skipped;
- Ruff check and format: 317 files passed;
- mypy app scope: 212 source files passed; and
- isolated Compose: Ruff and mypy passed, with 1,119 tests passed and 9 skipped.

The first full backend run had one unrelated SQLite worker timestamp-boundary failure. That
test passed immediately in isolation, and the complete rerun passed. The first Compose start
stopped before service creation because Docker's default network pools were exhausted. A
dedicated non-conflicting subnet was used without deleting an existing network; the final
isolated run passed, and its six containers and dedicated network were removed.

The fixed live run executed all 144 scheduled calls and retained every observation. The formal
raw-free result is:

- conclusion: `inconclusive`;
- stable reason: `rag88_pipeline_failure`;
- validity / baseline sensitivity / candidate adoption: `false / false / false`;
- eligible groups: `4` (required minimum: `8`);
- baseline incomplete eligible groups: `0` (required minimum: `6`);
- interference drop and 95% bootstrap CI: `0.0`, `[0.0, 0.0]`;
- interference exact paired p-value: `1.0`;
- baseline and candidate eligible-group joint completeness: `1.0 / 1.0`;
- baseline and candidate eligible-group atomic recall: `1.0 / 1.0`;
- baseline and candidate citation grounding recall: `1.0 / 1.0`;
- baseline and candidate citation source coverage: `0.833333 / 0.833333`;
- candidate joint delta, improved groups, and exact p-value: `0.0 / 0 / 1.0`;
- candidate p95 latency ratio: `1.0`;
- pipeline failures: `51`, comprising `40` standard-call wall-clock timeouts and `11`
  paired candidate observations whose pass 1 was unavailable;
- repair decisions among completed repair calls: `25` keep and `0` revise;
- false insufficiency, unexpected-fact groups, and forbidden-claim groups: `0 / 0 / 0`;
- binding drift, case exclusion, and case replacement: `0 / 0 / 0`; and
- exact-target stability passed. Full inventory stability did not pass because the unrelated
  pre-existing alias expired under its one-hour TTL during the long run; full inventory is not
  a RAG-88 validity gate.

Candidate metrics are descriptive only. In particular, the observed equality on the four
eligible groups is not evidence that the repair is effective because the experiment failed its
pipeline and minimum-eligibility gates. No rerun, failed-case replacement, extra repeat,
threshold change, fixture change, adoption, or profile promotion is authorized.

The result artifact SHA-256 is
`dca0865277b40f1c05401ca3afc74798c3a69b01b7e8ea161c819e3b6d2954e9`; the attempt marker
SHA-256 is `ffac4be6bcc021791937835f105771a955159ee47b47ff6926fb9e22555591c7`.
Both artifacts passed strict schema, model-byte, 144-observation/ordinal, and forbidden raw-key
checks. They persist neither raw evaluation content nor chain-of-thought.

Rollback is commit-scoped. The runner does not write to application datastores, so no datastore
rollback is required.
