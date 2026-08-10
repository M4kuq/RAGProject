# RAG-83 local candidate-blind per-claim review

## Outcome and current blocker

The review workflow is implemented and synthetic end-to-end testable, but the
real RAG-79 review has not started. The known raw-free authority is:

- `artifacts/rag32-local/oracle-context-confirm-run112.json`
- schema `phase3.oracle_context_confirm.v1`
- 40 case aggregates
- 39 repeat-level hash matches across 13 legacy review decisions
- 27 answerable repeat-level hash matches
- no question, source, answer, context, chunk, required-fact text, or per-claim
  reference decision

Those aggregate counts are not per-claim truth and are never expanded into
labels. The 13 legacy decisions are deduplicated case/answer-hash bindings; the
39 and 27 figures count their matches across three Oracle repeats. Scope
preparation therefore validates repeat-hash occurrence counts while presenting
each unique answer binding to the reviewer once. An unrelated failed repeat may
have a null Oracle answer hash and is accepted only as an authority input, never
as a review target.

The repository database is not a substitute for the missing Oracle private
input. `evaluation_review_payloads` holds the source run's retrieved-context
answer and context (the R-side input to the Oracle diagnostic). RAG-79 generated
the Oracle answer (the O-side review target) in process and retained only its
hash in the raw-free aggregate. Any database or private export adapter must
prove exact answer/context hash equality and fail closed on a mismatch.

The user-facing server is therefore ready only after an exact legacy manifest
and matching private Oracle answer/context input are found. Until then the
stable boundary is `atomic_claim_review_legacy_manifest_unreadable` or
`atomic_claim_review_private_input_unreadable`. If those historical private
inputs no longer exist, the two honest recovery choices are:

1. run a new fixed-condition calibration review with a new run ID and
   fingerprint, never representing it as run112 or RAG-79 evidence; or
2. have the user identify an existing repository-external private export and
   validate it by hash before startup.

## Responsibility boundary

| Module | Canonical responsibility |
|---|---|
| `evaluation_atomic_claim_contracts.py` | strict raw-free base, fixed source contract, ID/hash bindings, JSON read/write, model/payload equivalence, atomic safe output, stable blocked response |
| `evaluation_atomic_claim_calibration_service.py` | RAG-81 legacy review compatibility and additive calibration calculator |
| `evaluation_atomic_claim_blind_review_service.py` | RAG-82 blind Phase A commitment and Phase B validation |
| `evaluation_atomic_claim_review_workflow_service.py` | RAG-83 scope stripping, private input adapter, progress, human signoff, localhost HTTP/UI security |
| `run_evaluation_atomic_claim_review_workflow.py` | prepare, validate, and serve CLI |

The old service and CLI module names remain importable. Existing schema versions,
reason codes, safe outputs, not-applicable behavior, and the
`candidate_selected=false` gate without human signoff are unchanged.

## Duplication measurement

| Metric | Before | After |
|---|---:|---:|
| original four service/CLI lines | 1,101 | 924 |
| common canonical module lines | 0 | 157 |
| total including common module | 1,101 | 1,081 |
| shared non-trivial service lines | 90 | 67 |
| shared non-trivial CLI lines | 31 | 26 |
| canonical shared definitions | 0 | 14 |

The original four modules dropped 177 lines while the five-module total dropped
20 lines. The purpose is one source of truth, not line-count minimization.

## Candidate-blind preparation

The legacy review manifest contains prior Codex-assisted decisions. It is never
passed to the browser review process. Preparation strips it to a raw-free scope
containing only case ID and answer hash:

    uv run --frozen python -m app.scripts.run_evaluation_atomic_claim_review_workflow prepare-scope \
      --source-contract <fixed-raw-free-source-contract.json> \
      --run112-summary <oracle-context-confirm-run112.json> \
      --legacy-review-manifest <exact-rag79-review-manifest.json> \
      --output <rag83-scope.json>

Preparation verifies:

- run112, `local_accuracy_dev_v1`, Qwen 3.5 9B, temperature 0, baseline prompt,
  and 6000/12000/8192 budgets;
- dataset, case-set, generation-config, prompt, and source-contract fingerprints;
- exact legacy reviewed count, case count, answerable count, case IDs, and answer hashes;
- absence of raw fields in the run112 aggregate;
- candidate results and candidate identifiers are absent from the generated scope.

Validate the private adapter without printing raw content:

    uv run --frozen python -m app.scripts.run_evaluation_atomic_claim_review_workflow validate-input \
      --scope-manifest <rag83-scope.json> \
      --private-input <private-answer-context-input.json>

The private input schema is
`phase3.oracle_atomic_claim_private_review_input.v1`. It contains the scope
SHA-256, export provenance/tool/version/time, and exact answer/context values with
their hashes. Unknown fields and candidate fields are rejected. JSON whitespace is not
canonicalized; the exact supplied bytes are hashed before values are read into process memory and never copied to the raw-free progress or final files.

Question, source evidence, and required facts are loaded from the committed
`local_accuracy_dev_v1` fixture. Answer hashes must be present in the stripped
RAG-79 scope. Context hashes use the existing NUL-joined context-item hashing
contract.

## One command to start the review

Run this from the `backend` directory after the scope and private input validate:

    uv run --frozen python -m app.scripts.run_evaluation_atomic_claim_review_workflow serve --scope-manifest <rag83-scope.json> --private-input <private-answer-context-input.json> --output-dir <private-output-directory> --reviewer-provenance human:local-reviewer --port 8765 --confirm-local-only

Open:

    http://127.0.0.1:8765

The server refuses any bind other than `127.0.0.1`. It makes no external HTTP,
provider, or LLM call.

## Browser operation

For each claim the page shows the question, authoritative source evidence,
answer, runtime context, and required fact. Select:

- Supported
- Unsupported
- 保留

保留 remains an incomplete decision. Finalization fails while any claim is
pending. After every choice, a raw-free progress file is written so Ctrl+C and a
restart with the same command resume the review.

After all claims are complete, check the explicit human-signoff checkbox and
generate:

- `rag83-review-progress.json`
- `rag83-reference-manifest.json`
- `rag83-phase-a-commitment.json`

Only hashes, IDs, ordinals, booleans, provenance/tool/version, UTC timestamps,
and signoff state are persisted. The manifest and commitment contain no raw
question, source, answer, context, claim, PII, or secret.

## Local security boundary

- no access log and no validation-body echo;
- `Cache-Control: no-store`, CSP default deny, no browser storage or cache API;
- exact Host and Origin checks;
- SameSite Strict HttpOnly session cookie;
- per-process CSRF token with constant-time comparison;
- same-origin fetch enforcement and request-size limit;
- raw strings are inserted with `textContent`, never `innerHTML`;
- symlink output targets are rejected and raw-free outputs use atomic replace.

No compatible PII masking utility exists in the PR #154 base. Generic masking
could change the semantic review decision, so this workflow keeps the exact raw
text inside the explicit localhost-only boundary and warns the reviewer not to
copy, screenshot, or share it.

## Stop, resume, and rollback

Press Ctrl+C to stop. The raw input is not persisted by the server; only the
raw-free progress file remains. Restart the same command with the same scope,
private input, reviewer provenance, and output directory. Any hash, provenance,
scope, or claim-binding drift fails closed.

Rollback is to leave the stacked RAG-83 Draft PR unmerged. If later merged,
revert its isolated commits and return to PR #154 head
`4d35fdc5a7dfd6d3af51d88c732ed886d30878bb`. Do not merge, deploy, retarget, or
remove Draft state as part of this workflow.
