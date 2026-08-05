# Oracle multi-fact evidence grouping screening

## Scope

This RAG-80 screening isolates one coordinate on `local_accuracy_dev_v1`: the
two Oracle citation items for each multi-hop answerable case are placed under a
single evidence-group label. The source text, source order, citation markers,
question, model, prompt profile, temperature, output budget, and source run are
unchanged. The default RAG generation path and non-multi-fact Oracle cases keep
the existing separate-source rendering.

The source state is PR #151 head
`b283ac94181803edd9ad3d396b4b8295d4f7210a`, evaluation run `112`, and the
existing one-repeat baseline screening artifact. The candidate uses exact model
`qwen/qwen3.5-9b`, temperature `0`, reasoning disabled by the existing LM Studio
adapter, and the frozen baseline prompt/budgets from the source run.

## Raw-free screening result

The target set contains the 12 answerable multi-hop cases
`local_dev_answerable_13` through `local_dev_answerable_24`.

| Diagnostic | Separate-source baseline | Grouped candidate | Delta |
| --- | ---: | ---: | ---: |
| Auxiliary pass | 7/12 | 3/12 | -4 |
| Required-facts-supported pass | 10/12 | 7/12 | -3 |
| Citation-support pass | 11/12 | 10/12 | -1 |
| Mean deterministic context utilization | 0.083333 | 0.000000 | -0.083333 |
| Generation gap | 5/12 | 9/12 | +4 |
| Pipeline failures | 0 | 0 | 0 |

Case transitions were `pass_to_pass=2`, `pass_to_fail=5`, `fail_to_pass=1`, and
`fail_to_fail=4`. The raw-free artifact SHA-256 is
`5c8e1019d67389c5f82a1b38af7fd376d79093f2495a91fee753133c781a9c34`.

## Decision

The candidate failed every non-regression guardrail and produced no measured
improvement. It is not selected for three-repeat confirmation and must not be
promoted. The screening repeat is not counted as a confirm repeat. PR #151 and
the existing RAG-79 baseline remain the recovery point.

This is a local development diagnostic, not a profile-promotion result, public
accuracy claim, security score, or production readiness claim. Cases tagged for
prompt injection are not treated as a security evaluation here, and no security
fixture score is mixed into the accuracy result.

## Reproduction and rollback

Run the raw-free screening CLI against the existing RAG-79 screening artifact:

```text
python -m app.scripts.run_evaluation_oracle_evidence_grouping \
  --run-id 112 \
  --source-screening-artifact <raw-free-screening-artifact> \
  --output <ignored-local-output> \
  --confirm-local-only
```

The PostgreSQL collection is read-only for this run; no seed, reset, migration,
Qdrant, Neo4j, or Docker volume operation is required. Roll back by not merging
the Draft PR, or by reverting the isolated RAG-80 commit after merge. No model
load/unload is part of reproduction.

## Remaining diagnostic boundary

Because a minimal presentation label made generation materially worse, evidence
grouping is rejected as the next accuracy lever. RAG-79 still shows a larger
evaluator calibration problem: semantically supported atomic claims can fail the
exact expected-slot matcher. The next isolated diagnostic should therefore test
atomic claim segmentation/equivalence on hash-matched dev observations without
changing retrieval, generation, or the calibrated primary metric.
