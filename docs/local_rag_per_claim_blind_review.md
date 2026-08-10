# RAG-82 candidate-blind per-claim re-review

## Decision first

RAG-82 does not yet have an authoritative hash-bound per-claim reference manifest
for the RAG-79 reviewed subset. No human per-claim signoff was available in the
repository or remote evidence, and the earlier observation-level aggregate is not
expanded into claim labels.

The current measured decision is therefore fail closed:

| Field | Result |
|---|---|
| scope | local_accuracy_dev_v1 / run 112 / existing RAG-79 reviewed subset only |
| authoritative per-claim labels | 0 |
| Phase A reference manifest SHA-256 | null |
| human signoff | absent |
| calibration coverage | 0 |
| whole exact FN / FP | null / null |
| atomic candidate FN / FP | null / null |
| candidate selected | false |
| public accuracy / profile promotion | forbidden |
| decision | insufficient_hash_bound_claim_labels |

This is a calibration-availability result, not an accuracy result. It neither
inherits RAG-80 / PR #152 nor changes the RAG-81 primary metric, model, prompt,
retrieval, generation, budgets, Judge rubric, Gold v2, or any runtime profile.

## Fixed source contract

The strict source contract remains fixed to:

- evaluation run 112 and local_accuracy_dev_v1;
- exact model qwen/qwen3.5-9b, temperature 0;
- baseline prompt;
- 6000 context characters, 12000 output characters, and 8192 output tokens;
- dataset, case-set, generation-config, prompt, and budget fingerprints.

Every answerable claim is bound to case ID, question/source/answer/context hashes,
required-fact ID and hash, and claim ordinal. Unanswerable and abstention
observations use a separate not-applicable collection and never enter the
required-fact denominator.

## Phase A: blind reference commitment

The new phase3.oracle_atomic_claim_blind_review.v1 schema contains only raw-free
identifiers, hashes, labels, provenance, version, timestamps, and signoff state.
Unknown fields are rejected. It explicitly asserts that candidate results and
candidate identifiers were not observed while reviewing.

The phase-a CLI:

1. validates the complete reference and source fingerprints;
2. rejects duplicate claim identities, raw/unknown fields, malformed timestamps,
   inconsistent signoff state, and review-scope drift;
3. hashes the exact reference bytes;
4. writes phase3.oracle_atomic_claim_blind_commitment.v1 before any Phase B input
   is accepted.

The commitment includes the reference SHA-256, claim count, reviewer provenance,
reviewer type, review tool/version, review timestamp, commitment timestamp, and
signoff state. It contains no candidate result.

## Phase B: additive RAG-81 calculation

Phase B refuses to run unless:

- the exact reference bytes still match the Phase A SHA-256;
- source and review-scope fingerprints match;
- provenance/version/timestamp/signoff fields match the commitment;
- the candidate explicitly binds to the Phase A reference SHA-256;
- logical claim coverage is complete;
- question/source/answer/context/required-fact hashes all match;
- duplicate claims and answerable/not-applicable overlap are absent.

Only after those checks does it call the existing RAG-81 calculator for whole
exact versus atomic semantic equivalence. If requires_human_signoff is true, the
RAG-82 wrapper forces candidate_selected=false even when synthetic FN/FP gates
would otherwise pass. A human-signed reference can only select a screening
candidate; public accuracy claims and profile promotion remain forbidden.

Example:

    cd backend
    python -m app.scripts.run_evaluation_atomic_claim_blind_review phase-a \
      --reference-manifest <raw-free-reference-json> \
      --output <raw-free-commitment-json>

    python -m app.scripts.run_evaluation_atomic_claim_blind_review phase-b \
      --reference-manifest <raw-free-reference-json> \
      --phase-a-commitment <raw-free-commitment-json> \
      --candidate-manifest <raw-free-candidate-json> \
      --output <raw-free-summary-json>

The CLI reads inputs only inside the process and prints only schema-safe hashes,
counts, provenance, and stable reason codes. It never echoes a validation body.

## Verification and evidence boundary

Synthetic tests cover Phase A byte commitment, candidate contamination, raw-field
rejection, duplicate claims, scope/hash/provenance drift, incomplete coverage,
not-applicable separation, signoff gating, raw-free output, and CLI fail-closed
behavior. They validate the instrument only and are not RAG-79 FN/FP evidence.

The local execution runner could not create a root-linked worktree because the
root Git metadata is read-only, and an isolated clone could not reach GitHub 443
inside the sandbox. The remote branch was created directly at PR #153 head and
GitHub CI is the authoritative execution environment. Root checkout, existing
worktrees, LM Studio, databases, Qdrant, Neo4j, Docker volumes, and source PRs
remain unchanged.

## Next single change and rollback

The next single evidence change is not a semantic matcher change. It is a
candidate-blind human per-claim review of the fixed RAG-79 scope using the new
Phase A schema, followed by manifest hash commitment and explicit signoff. Until
that occurs, Phase B evidence remains unavailable and candidate_selected remains
false.

Rollback: do not merge the RAG-82 Draft PR. If it is later merged, revert its
isolated commit and return to PR #153 head
e99737959dca38ed084136f7dc91f6c734a307c3. No merge, deploy, retarget, or Draft
state change is authorized.
