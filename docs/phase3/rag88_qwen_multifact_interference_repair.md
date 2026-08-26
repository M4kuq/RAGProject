# RAG-88 Qwen multi-fact interference and generic two-pass repair

## Status

Pre-live implementation. No RAG-88 model call has run at this revision.

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

Pre-live focused tests, full backend checks, Ruff, mypy, isolated Compose, and GitHub CI evidence
are recorded separately after completion. The formal one-shot result will be added without
changing this contract.

Rollback is commit-scoped. The runner does not write to application datastores, so no datastore
rollback is required.
