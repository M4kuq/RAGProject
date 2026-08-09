# RAG-81 raw-free atomic-claim calibration

## Scope and recovery point

RAG-81 is an additive evaluator diagnostic stacked on Draft PR #151 head
`b283ac94181803edd9ad3d396b4b8295d4f7210a`. It does not inherit the rejected
RAG-80 / PR #152 candidate. Retrieval, generation, model, prompt, temperature,
output budget, Judge rubric, and the existing Calibrated Grounded Answer Pass Rate
remain unchanged. The whole-statement exact matcher remains the primary diagnostic;
the new semantic-equivalence dimension cannot replace or rewrite it.

Rollback is to leave the RAG-81 Draft PR unmerged or revert its isolated commit.
PR #151/#152, PR #128/#130/#131, Gold v2, runtime profiles, LM Studio, databases,
Qdrant, Neo4j, and Docker volumes are unchanged.

## Reference audit and decision

The only located review artifact is bound to RAG-79 run 112 and has:

- schema: `phase3.oracle_codex_assisted_review.v1`
- artifact SHA-256:
  `352474e14760db1a06a55e81e637170e5eaba615e43f0c0ffca48c4c7eec1747`
- provenance: `codex_assisted_manual_content_review`
- status: `requires_human_signoff`
- persisted raw content: false
- decisions: 17 total, 13 baseline decisions

The legacy decision is observation-level. PR #151 derives its reported semantic
supported-claim count by rounding
`manual_context_utilization * required_fact_count`. It does not contain a
`required_fact_id` or claim ordinal paired with a supported/unsupported reference
label. Therefore the prior aggregate of 27 semantic supported claims cannot be
expanded into claim-level truth and cannot establish claim-level false negatives or
false positives.

The RAG-81 evidence decision is consequently:

| Field | Result |
|---|---|
| reference provenance | Codex-assisted observation review |
| authoritative per-claim labels | 0 |
| per-claim calibration coverage | 0 |
| whole exact FN / FP | null / null |
| atomic candidate FN / FP | null / null |
| candidate selected | false |
| decision | `not_calibrated` |
| reason | `insufficient_hash_bound_claim_labels` |
| human signoff | still required |

The reviewed RAG-79 subset has 39 repeat-observations, of which 27 are answerable;
the remaining 12 unanswerable/abstention observations are not applicable to
required-fact matching and stay outside the denominator. Across the complete
three-repeat 40-case confirmation, 16 unanswerable cases correspond to 48
not-applicable repeat-observations. Neither count is treated as a false positive.

This result corrects the evidentiary limit; it does not retroactively rewrite PR
#151 and does not claim that FN or FP equals zero.

## New strict per-claim contract

A future reference must use
`phase3.oracle_atomic_claim_review.v1`. Pydantic models set
`extra="forbid"` and permit only hashes, identifiers, ordinals, decisions, and
provenance. Each decision is bound to:

- run ID and `local_accuracy_dev_v1`;
- dataset, case-set, generation-config, prompt, and budget fingerprints;
- exact model `qwen/qwen3.5-9b`, temperature 0, and frozen budget values;
- `case_id + answer_hash + context_hash + required_fact_id + claim_ordinal`;
- `reference_supported`;
- reviewer provenance/version and human-signoff state.

Source fingerprint drift, answer/context hash drift, duplicate identities, unknown
candidate identities, raw/unknown fields, and answerable/not-applicable overlap are
rejected. Codex-, Qwen-, or deterministic provenance is never relabeled as human
review.

The candidate schema changes only `semantic_equivalence_only`. Segmentation is
fixed as `presegmented_reference_claim` and reported separately. Candidate
selection is possible only when coverage is complete, false negatives decrease,
false positives do not increase, not-applicable misapplication is zero, and
pipeline failures are zero. Even then the result is screening-only and requires a
separate confirm; it is not public accuracy or profile promotion.

## CLI

The raw-free CLI reads input inside the process, emits only schema-safe
hashes/counts/reason codes, and never prints a validation body:

```powershell
cd backend
python -m app.scripts.run_evaluation_atomic_claim_calibration `
  --candidate-manifest <candidate-json> `
  --reference-manifest <review-json> `
  --output <raw-free-summary-json>
```

Passing the legacy RAG-79 review returns exit code 2 with
`calibration_status=insufficient_hash_bound_claim_labels`, coverage 0, null
FN/FP, and `candidate_selected=false`. Malformed input, fingerprint drift, and
unreadable input also fail closed with stable reason codes.

## Verification and next evidence

Synthetic truth tests verify the strict schema, legacy aggregate classification,
complete and partial binding, source/hash drift rejection, FN reduction, FP
increase rejection, not-applicable separation, raw-free output, and CLI
fail-closed behavior. Synthetic truth is only instrument verification and is not
used as RAG-79 accuracy evidence.

The next evidence step is a raw-free per-claim re-review manifest with explicit
provenance. Until it exists and passes hash binding, the atomic equivalence
candidate remains `not_calibrated`; no screening repeat, confirm, or promotion is
authorized.
