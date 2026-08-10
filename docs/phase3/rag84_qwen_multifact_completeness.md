# RAG-84 Qwen multi-fact completeness experiment

RAG-84 tests one generation-only coordinate: a prompt-only internal evidence ledger for
`qwen/qwen3.5-9b`. Retrieval mode, Oracle source text and order, citation ids, model,
temperature, reasoning mode, context/output budgets, and the existing evaluation retry
policy remain fixed. The baseline prompt remains unchanged.

## Frozen scope

- Tune: all 12 answerable multi-hop cases in `local_accuracy_dev_v1`, one repeat per
  profile.
- Confirm: 14 answerable two-fact safe-synthetic cases in
  `rag84_qwen_multifact_confirm_v1`, three repeats per profile.
- Confirm balance: 7 Japanese and 7 English cases; two cases carry an untrusted
  instruction note for prompt-injection non-regression.
- Confirm isolation: the fixture uses the new `R84-confirm-20260810` namespace. Tune and
  confirm have zero overlap for question hashes, fact ids, normalized fact hashes,
  source-content hashes, and logical document ids.
- Gold v2 is not read or changed by this experiment. Neither dataset contributes to Gold,
  public accuracy, or profile promotion.

The committed experiment lock is
`backend/app/evaluation/fixtures/rag84_qwen_multifact_experiment_lock.json`. It binds the
stacked base, both dataset/case/context fingerprints, both prompt fingerprints, the
independence proof, and the decision thresholds. Runtime recomputes the full manifest and
fails closed on drift.

## Predeclared decision rule

Tune advances only when all conditions hold:

- observed baseline required-fact recall is the frozen 50% reference;
- candidate required-fact recall is at least 75% and improves by at least 25 percentage
  points;
- false insufficiency assertions decrease;
- unexpected facts and forbidden claims are both zero;
- citation-source coverage and claim-level citation grounding do not regress;
- both profiles have zero pipeline failures.

Confirm is run once only after tune passes. The candidate is adopted only when majority
required-fact recall improves, whole-statement exact match and citation/grounding do not
regress, unsupported/forbidden facts do not worsen, both profiles have zero pipeline
failures, and stable-runtime candidate p95 latency is no more than 2x baseline. No prompt,
fixture, or threshold changes are allowed after confirm; a failed gate retains baseline.

Whole-statement exact and calibrated atomic identifier-equivalence metrics are both
reported. The atomic evaluator calibration has mixed provenance: 14 user-origin labels,
21 Codex-assisted pending fills, one Codex-assisted correction, followed by user acceptance
of all 36 claims. It is not described as independent human-only calibration.

## Local execution

Run only after the prompt, fixture, tests, and experiment lock have been committed. Use the
same commit SHA for tune and confirm and write outputs outside every Git checkout.

```powershell
cd backend
python -m app.scripts.run_evaluation_qwen_multifact_completeness tune `
  --preconfirm-commit <40-char-commit-sha> `
  --output <repository-external-path> `
  --confirm-local-only

python -m app.scripts.run_evaluation_qwen_multifact_completeness confirm `
  --preconfirm-commit <same-40-char-commit-sha> `
  --tune-result <tune-result-path> `
  --output <new-repository-external-path> `
  --runtime-stable `
  --confirm-local-only
```

The CLI sends only question plus the frozen Oracle context to generation. Required facts
and expected answers are used after generation for local scoring only. Progress and result
artifacts contain hashes, counts, booleans, latency, and stable reason codes; they never
persist question, source, context, answer, required-fact, or expected-answer text.

## Safety and rollback

The runner does not access the database, Qdrant, Docker volumes, Gold v2, or non-loopback
HTTP. It does not load or unload LM Studio models. It refuses repository-local, existing,
or symlinked output paths. Rollback is deletion/revert of the RAG-84 branch commit and
repository-external raw-free result artifacts; no data-store rollback is required.
